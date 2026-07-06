"""
Vokter — Your local AI guardian.

Not a single call leaves your machine. Check it: the only host
this code talks to is the local Ollama container.
"""
import asyncio
import os
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from auth import admin_token_ok, requires_admin

import identity  # noqa: F401 — triggers master key init on startup

from ingestion import router as ingestion_router
from chat import router as chat_router
from email_connector import router as email_router
from voice.whisper import router as whisper_router
from voice.piper import router as piper_router
from browser import router as browser_router
from planner import router as planner_router
from wallet_routes import router as wallet_router
from schedule_routes import router as schedule_router
from config_routes import router as config_router
from agent_routes import router as agent_router
from negotiation_routes import router as negotiation_router
from a2a_server import router as a2a_router
from config import VOKTER_VERSION, A2A_URL, ADMIN_TOKEN
from scheduler import scheduler_loop, _running_tasks
import nostr_listener


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Fail closed: refuse to start when exposed without an admin token. A2A_URL
    # set signals intent to expose; without ADMIN_TOKEN the admin API (wallet,
    # config, documents, email) would be reachable by anyone who can reach the
    # port. (Heuristic, not proof of exposure — pair with a reverse proxy that
    # publishes only /a2a and /.well-known.)
    if A2A_URL and not ADMIN_TOKEN:
        raise RuntimeError(
            "Refusing to start: VOKTER_A2A_URL is set (Vokter is being exposed) "
            "but VOKTER_ADMIN_TOKEN is empty — the admin API (wallet, config, "
            "documents, email) would be UNPROTECTED. Set VOKTER_ADMIN_TOKEN, and "
            "reverse-proxy only /a2a and /.well-known."
        )
    sched_task  = asyncio.create_task(scheduler_loop())
    nostr_task  = asyncio.create_task(nostr_listener.start())
    yield
    # Cancel background tasks
    for t in (sched_task, nostr_task):
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass
    # Cancel and await all in-flight _run_task coroutines.
    if _running_tasks:
        for t in list(_running_tasks):
            t.cancel()
        await asyncio.gather(*_running_tasks, return_exceptions=True)


app = FastAPI(title="Vokter", version=VOKTER_VERSION, lifespan=lifespan)


@app.middleware("http")
async def admin_gate(request: Request, call_next):
    """Gate the human's admin API (H1). The public agent surface (/a2a,
    /.well-known, /api/agent/card) and the loopback-only UI pass through."""
    if requires_admin(request.url.path):
        token = request.headers.get("x-vokter-admin-token")
        if not token:
            scheme, _, bearer = request.headers.get("authorization", "").partition(" ")
            if scheme.lower() == "bearer":
                token = bearer
        if not admin_token_ok(token):
            return JSONResponse(
                {"detail": "Unauthorized — admin token required"}, status_code=401
            )
    return await call_next(request)
app.include_router(ingestion_router)
app.include_router(chat_router)
app.include_router(email_router)
app.include_router(whisper_router)
app.include_router(piper_router)
app.include_router(browser_router)
app.include_router(planner_router)
app.include_router(wallet_router)
app.include_router(schedule_router)
app.include_router(config_router)
app.include_router(agent_router)
app.include_router(negotiation_router)
app.include_router(a2a_router)

# Resolve static/ from this file (or the PyInstaller bundle when frozen),
# never from the process CWD — the frozen binary is launched from anywhere.
_STATIC_DIR = os.path.join(
    getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__))), "static"
)
app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")


@app.get("/")
def index():
    return FileResponse(os.path.join(_STATIC_DIR, "index.html"))
