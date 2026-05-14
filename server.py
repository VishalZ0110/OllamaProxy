from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
import httpx
import uuid
import time
import json
import logging
import sys
import os
from contextlib import asynccontextmanager
from pathlib import Path
from dotenv import load_dotenv
import logging.handlers

load_dotenv()

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_CHAT_URL = f"{OLLAMA_BASE_URL}/api/chat"
API_KEY = os.getenv("PROXY_API_KEY", "")

# ---------------- logging ----------------

LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / "proxy_debug.log"

file_handler = logging.handlers.RotatingFileHandler(
    LOG_FILE,
    maxBytes=10 * 1024 * 1024,  # 10 MB
    backupCount=5
)

console_handler = logging.StreamHandler(sys.stdout)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    handlers=[console_handler, file_handler]
)

logger = logging.getLogger("ollama-proxy")


@asynccontextmanager
async def lifespan(fastapi_app: FastAPI):
    limits = httpx.Limits(max_keepalive_connections=20, max_connections=100)
    fastapi_app.state.http_client = httpx.AsyncClient(timeout=300, limits=limits)
    logger.info("Shared HTTP client initialized")
    try:
        yield
    finally:
        await fastapi_app.state.http_client.aclose()
        logger.info("Shared HTTP client closed")


app = FastAPI(lifespan=lifespan)

# ---------------- helpers ----------------

def normalize_tool_message_fields(message):
    normalized = {}
    if "name" in message:
        normalized["name"] = message.get("name")
    if "tool_call_id" in message:
        normalized["tool_call_id"] = message.get("tool_call_id")
    return normalized


def normalize_content(content):
    # Convert OpenAI multipart content into Ollama-compatible text + images.
    if not isinstance(content, list):
        return ("" if content is None else content), []

    text_parts = []
    image_parts = []
    unsupported_parts = []

    for part in content:
        if isinstance(part, dict) and part.get("type") == "text":
            text_parts.append(part.get("text", ""))
        elif isinstance(part, dict) and part.get("type") in ("image_url", "input_image"):
            image_url = part.get("image_url")
            image_url = image_url.get("url") if isinstance(image_url, dict) else image_url

            if not image_url and isinstance(part.get("input_image"), dict):
                image_url = part["input_image"].get("url")

            if isinstance(image_url, str):
                if image_url.startswith("data:") and ";base64," in image_url:
                    image_parts.append(image_url.split(";base64,", 1)[1])
                elif image_url.startswith("http://") or image_url.startswith("https://"):
                    logger.warning("Remote image URLs are not supported for Ollama input; use data URL/base64")
                else:
                    # Assume caller already provided a raw base64 string.
                    image_parts.append(image_url)
            else:
                unsupported_parts.append(part)
        else:
            unsupported_parts.append(part)

    if unsupported_parts:
        logger.warning("Unsupported multipart content omitted")

    return "".join(text_parts), image_parts


def normalize_messages(messages):
    normalized = []

    for m in messages:

        role = m.get("role")

        content = m.get("content")
        content, images = normalize_content(content)

        msg = {
            "role": role,
            "content": content
        }
        if images:
            msg["images"] = images

        # assistant tool calls can pass through
        if "tool_calls" in m:
            converted_calls = []

            for call in m["tool_calls"]:
                args = call["function"].get("arguments")

                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except Exception:
                        logger.warning("Failed to parse tool arguments string")
                        args = {}

                converted_calls.append({
                    "type": "function",
                    "function": {
                        "name": call["function"]["name"],
                        "arguments": args
                    }
                })

            msg["tool_calls"] = converted_calls

        # tool responses
        if role == "tool":
            msg.update(normalize_tool_message_fields(m))

        normalized.append(msg)

    return normalized


def convert_tool_calls(tool_calls):

    converted = []

    for tool in tool_calls:

        args = tool["function"].get("arguments", "{}")

        if not isinstance(args, str):
            args = json.dumps(args, ensure_ascii=False)

        converted.append(
            {
                "id": tool.get("id", f"call_{uuid.uuid4().hex}"),
                "type": "function",
                "function": {
                    "name": tool["function"]["name"],
                    "arguments": args
                }
            }
        )

    return converted


def convert_tools_for_ollama(tools):
    converted = []

    for tool in tools:
        if tool.get("type") == "function":
            fn = tool["function"]

            converted.append({
                "type": "function",
                "function": {
                    "name": fn["name"],
                    "description": fn.get("description", ""),
                    "parameters": fn.get("parameters", {})
                }
            })

    return converted


# ---------------- streaming ----------------

async def stream_ollama(payload, model):

    stream_id = f"chatcmpl-{uuid.uuid4().hex}"
    saw_tool_calls = False
    accumulated_response = ""
    thinking_content = ""  # Track thinking tokens separately
    output_content = ""    # Track output tokens separately
    thinking_done = False   # Track when thinking is logged
    logger.info("Starting Ollama streaming request")

    client = app.state.http_client
    async with client.stream(
        "POST",
        OLLAMA_CHAT_URL,
        json=payload,
        timeout=None,
    ) as response:

            logger.info(f"Ollama stream status: {response.status_code}")

            if response.status_code != 200:
                error_body = await response.aread()
                error_text = error_body.decode("utf-8", errors="replace")
                logger.error("Ollama streaming request failed")
                logger.error(error_text)
                raise HTTPException(status_code=response.status_code, detail=error_text)

            async for line in response.aiter_lines():

                if not line:
                    continue

                try:
                    data = json.loads(line)
                except Exception:
                    logger.error("Invalid JSON received from Ollama")
                    logger.error(line)
                    raise HTTPException(
                        status_code=502,
                        detail="Malformed JSON received from upstream stream",
                    )

                message = data.get("message", {})
                delta = {}

                # Handle thinking tokens (from nested message payload)
                thinking = message.get("thinking")
                if thinking:
                    # Accumulate thinking
                    thinking_content += thinking
                else:
                    # Check if we should log complete thinking
                    if thinking_content and not thinking_done:
                        logger.info(f"[THINKING] {thinking_content}")
                        thinking_done = True

                # Handle output tokens (from nested message payload)
                token = message.get("content")
                if token:
                    delta["role"] = "assistant"
                    delta["content"] = token
                    # Accumulate output
                    output_content += token

                # Log tool calls if present
                tool_calls = message.get("tool_calls")
                if tool_calls:
                    saw_tool_calls = True
                    logger.info(f"[TOOL CALL] {json.dumps(tool_calls, ensure_ascii=False)}")
                    delta["role"] = "assistant"
                    delta["tool_calls"] = []

                    for idx, tool in enumerate(tool_calls):
                        args = tool["function"].get("arguments", "{}")

                        if not isinstance(args, str):
                            args = json.dumps(args, ensure_ascii=False)

                        delta["tool_calls"].append(
                            {
                                "index": idx,
                                "id": tool.get(
                                    "id",
                                    f"call_{uuid.uuid4().hex}"
                                ),
                                "type": "function",
                                "function": {
                                    "name": tool["function"]["name"],
                                    "arguments": args
                                }
                            }
                        )

                # Yield chunk if we have content in delta
                if delta:
                    chunk = {
                        "id": stream_id,
                        "object": "chat.completion.chunk",
                        "created": int(time.time()),
                        "model": model,
                        "choices": [
                            {
                                "index": 0,
                                "delta": delta,
                                "finish_reason": None
                            }
                        ]
                    }

                    yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"

                if data.get("done"):

                    finish_reason = "tool_calls" if saw_tool_calls else "stop"

                    end_chunk = {
                        "id": stream_id,
                        "object": "chat.completion.chunk",
                        "created": int(time.time()),
                        "model": model,
                        "choices": [
                            {
                                "index": 0,
                                "delta": {},
                                "finish_reason": finish_reason
                            }
                        ]
                    }

                    yield f"data: {json.dumps(end_chunk)}\n\n"
                    yield "data: [DONE]\n\n"

                    if output_content:
                        logger.info(f"[OUTPUT] {output_content}")

                    break


# ---------------- routes ----------------

@app.get("/")
async def ollama_root():

    logger.info("Health check called")

    client = app.state.http_client
    response = await client.get(f"{OLLAMA_BASE_URL}/")

    return JSONResponse(
        status_code=response.status_code,
        content=response.json()
        if response.headers.get(
            "content-type",
            ""
        ).startswith("application/json")
        else {"message": response.text},
    )


@app.post("/chat/completions")
async def chat_completions(request: Request):

    start_time = time.time()

    logger.info("---- Incoming /chat/completions request ----")

    auth = request.headers.get("authorization")

    if auth != f"Bearer {API_KEY}":
        logger.warning("Invalid API key attempt")
        raise HTTPException(
            status_code=401,
            detail="Invalid API key"
        )

    try:
        body = await request.json()
    except json.JSONDecodeError:
        logger.warning("Malformed JSON body received")
        raise HTTPException(status_code=400, detail="Malformed JSON request body")
    except Exception:
        logger.warning("Failed to parse request JSON body")
        raise HTTPException(status_code=400, detail="Invalid request body")

    model = body.get("model", "gemma4:26b")

    messages = normalize_messages(
        body.get("messages", [])
    )

    stream = body.get("stream", False)

    tools = body.get("tools")
    tool_choice = body.get("tool_choice")

    ollama_payload = {
        "model": model,
        "messages": messages,
        "stream": stream
    }

    if tools:
        ollama_payload["tools"] = convert_tools_for_ollama(tools)

    if tool_choice:
        ollama_payload["tool_choice"] = tool_choice

    if stream:

        logger.info("Streaming mode enabled")

        return StreamingResponse(
            stream_ollama(
                ollama_payload,
                model
            ),
            media_type="text/event-stream"
        )

    client = app.state.http_client
    response = await client.post(
        OLLAMA_CHAT_URL,
        json=ollama_payload
    )

    logger.info("---- Ollama Response ----")
    logger.info(f"Status: {response.status_code}")
    logger.info(response.text)

    if response.status_code != 200:

        logger.error("Ollama returned error")

        raise HTTPException(
            status_code=response.status_code,
            detail=response.text
        )

    try:
        ollama_data = response.json()
    except json.JSONDecodeError:
        logger.error("Upstream returned invalid JSON")
        raise HTTPException(
            status_code=502,
            detail="Upstream returned malformed JSON response"
        )

    message = ollama_data.get("message", {})

    openai_message = {
        "role": "assistant",
        "content": message.get("content")
    }

    finish_reason = "stop"

    if "tool_calls" in message:

        logger.info("Tool calls returned by Ollama")
        logger.info(json.dumps(message["tool_calls"], indent=2))

        openai_message["tool_calls"] = convert_tool_calls(
            message["tool_calls"]
        )

        openai_message["content"] = None

        finish_reason = "tool_calls"

    openai_response = {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": openai_message,
                "finish_reason": finish_reason
            }
        ],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0
        }
    }

    logger.info(
        f"Request completed in {round(time.time()-start_time,2)} seconds"
    )

    return JSONResponse(openai_response)
