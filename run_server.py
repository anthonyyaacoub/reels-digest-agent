"""Entry point: start the FastAPI webhook server with uvicorn."""

import logging

import uvicorn

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)

if __name__ == "__main__":
    from src.config import PORT

    uvicorn.run(
        "src.server:app",
        host="0.0.0.0",
        port=PORT,
        reload=False,
        log_level="info",
    )
