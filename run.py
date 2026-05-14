import os
import uvicorn
from server import app

if __name__ == "__main__":
    # Load environment variables from .env if present
    from dotenv import load_dotenv
    load_dotenv()

    # Configuration from environment variables
    port = int(os.getenv("PORT", 8000))
    
    print(f"Starting proxy server on 0.0.0.0:{port}")
    
    # Run uvicorn server
    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=port,
        reload=os.getenv("DEBUG", "false").lower() == "true",
        access_log=True
    )
