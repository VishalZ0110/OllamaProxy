# OllamaProxy

This repository provides a production-ready proxy server that wraps [Ollama](https://ollama.com/) with an OpenAI-compatible API interface. It allows you to use Ollama with any client or library designed for the OpenAI API, including **Cursor**.

## Features

- **Integration with Cursor**: Easily integrates with Cursor.
- **Streaming Support**: Full support for Server-Sent Events (SSE) streaming.
- **Tool Use (Function Calling) Support**: Proxies tool calls and tool responses between OpenAI-style requests and Ollama's format.
- **Image Input Support**: Handles image URLs and base64-encoded images in message content.
- **Thinking Tokens Support**: Proxies thinking tokens from Ollama's nested message format.

## Table of Contents

- [Features](#features)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Configuration](#configuration)
- [Running the Server](#running-the-server)
- [Usage](#usage)
  - [Use with Cursor-IDE](#use-with-cursor-ide)
  - [Use as API](#use-as-api)
- [Logging](#logging)
- [Troubleshooting](#troubleshooting)

## Prerequisites

- Python 3.8+
- [Ollama](https://ollama.com/) running on a reachable network address.

## Installation

1. **Clone the repository**.  
  ```bash  
   git clone https://github.com/VishalZ0110/OllamaProxy.git
  ```
2. **Create a virtual environment**:
  ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows use: venv\Scripts\activate
  ```
3. **Install dependencies**:
  ```bash
   pip install -r requirements.txt
  ```

## Configuration

Copy `.env.example` to `.env`
  ```bash
   cp .env.example .env
  ```

### Environment Variables

- `OLLAMA_BASE_URL` - The base URL where your Ollama instance is running (default: `http://localhost:11434`)
- `PROXY_API_KEY` - Optional API Key for proxy authentication (default: `ollama`)
- `PORT` - Server port (default: `8000`)

The host is always set to `0.0.0.0` to allow external connections.

## Running the Server

You can run the server using the provided `run.py` script:

```bash
python run.py
```

Alternatively, you can run it directly via `uvicorn`:

```bash
uvicorn server:app --host 0.0.0.0 --port 8000
```

## Usage

### Use with Cursor-IDE

A Cursor Pro Subscription is required to use custom API keys.

#### 1. Server and Model Preparation

First, ensure your proxy server and Ollama model are ready:

- **Run the server locally**: Start the OllamaProxy server on your local system.
- **Expose the endpoint**: Use a tool like `ngrok` to create a publicly accessible URL for your server. This URL will be used by Cursor to send API requests. For example: `https://000x-00-000-000-00.ngrok-free.app`.
- **Pull Ollama model**: On the machine running the server, pull the desired model using `ollama pull <model_name>`. Verify the model fits your GPU to avoid slow inference due to CPU offloading.

#### 2. Configure Cursor IDE

1. **Access API Keys**: Navigate to `Settings > Models > API Keys`.
2. **Enable OpenAI API Key**: Toggle on the "OpenAI API Key" option.
3. **Override Base URL**: Enable "Override OpenAI Base URL" and paste your ngrok/public URL. Ensure the URL does not include a trailing `/`, `v1/`, or `api/completion/`.
4. **Enter API Key**: Input the `PROXY_API_KEY` configured in your server's `.env` file.
5. **Add Custom Model**: Go to `Models` section, click `View All Models`, then `+ Add Custom Model`. Enter the exact model name used when pulling from Ollama (e.g., `llama3.1`).

#### 3. Chat with your Model

After configuration, you can select your custom Ollama model in the Cursor chat interface and interact with it like any other supported model.

### Use as API

#### 1. Health Check

Check if the proxy and the upstream Ollama service are running:

```bash
curl http://localhost:8000/
```

#### 2. Chat Completions (Non-Streaming)

```bash
curl http://localhost:8000/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer your_super_secret_key" \
  -d '{
    "model": "gemma4:26b",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

#### 3. Chat Completions (Streaming)

```bash
curl -N http://localhost:8000/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer your_super_secret_key" \
  -d '{
    "model": "gemma4:26b",
    "messages": [{"role": "user", "content": "Write a short poem."}],
    "stream": true
  }'
```

#### 4. Chat Completions with Tool Calls

```bash
curl http://localhost:8000/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer your_super_secret_key" \
  -d '{
    "model": "llama3.1",
    "messages": [
      {"role": "user", "content": "What is the current weather?"}
    ],
    "tools": [
      {
        "type": "function",
        "function": {
          "name": "get_weather",
          "description": "Get current weather for a location",
          "parameters": {
            "type": "object",
            "properties": {
              "location": {"type": "string"}
            },
            "required": ["location"]
          }
        }
      }
    ],
    "tool_choice": "auto"
  }'
```

#### 5. Chat Completions with Images

```bash
curl http://localhost:8000/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer your_super_secret_key" \
  -d '{
    "model": "llava",
    "messages": [{
      "role": "user",
      "content": [
        {"type": "text", "text": "Describe this image"},
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}}
      ]
    }]
  }'
```

## Logging

The proxy logs all requests and responses to `logs/proxy_debug.log`. Logs are rotated automatically (10MB max, 5 backups).

## Troubleshooting

### Connection Issues

If you encounter connection errors to Ollama:

1. Ensure Ollama is running and accessible
2. Check `OLLAMA_BASE_URL` in your `.env` file
3. Verify network connectivity to the Ollama instance

### Authentication Errors

If you receive 401 errors:

1. Ensure `PROXY_API_KEY` is set in `.env`
2. Include the API key in all requests: `Authorization: Bearer <key>`

### Invalid JSON from Ollama

If the proxy receives invalid JSON from Ollama, it will return a 502 error with details about the malformed response.