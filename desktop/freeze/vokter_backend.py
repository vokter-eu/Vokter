"""Frozen entry point — serve the real Vokter backend with uvicorn.

All Vokter configuration stays env-driven (config.py); this only picks
the bind address, so the frozen binary needs no command-line parsing.
"""
import os

import uvicorn

from main import app

if __name__ == "__main__":
    uvicorn.run(
        app,
        host=os.getenv("VOKTER_BIND", "127.0.0.1"),
        port=int(os.getenv("VOKTER_PORT", "8080")),
        log_level="info",
    )
