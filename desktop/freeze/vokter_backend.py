"""Frozen entry point — serve the real Vokter backend with uvicorn.

All configuration is env-driven and lives in config.py, including the
bind address (VOKTER_BIND/VOKTER_PORT) — this file adds nothing to it,
so the frozen binary needs no command-line parsing.
"""
import multiprocessing

import uvicorn

from config import BIND, PORT
from main import app

if __name__ == "__main__":
    # No-op on Linux; on Windows/macOS (spawn) it stops a re-executed frozen
    # exe from booting a second server when a dependency spawns a process.
    multiprocessing.freeze_support()
    uvicorn.run(app, host=BIND, port=PORT, log_level="info")
