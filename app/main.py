"""
Vokter — Your local AI guardian.

Not a single call leaves your machine. Check it: the only host
this code talks to is the local Ollama container.
"""
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

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
from a2a_server import router as a2a_router
from config import VOKTER_VERSION
from scheduler import scheduler_loop, _running_tasks
import nostr_listener


@asynccontextmanager
async def lifespan(_app: FastAPI):
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
app.include_router(a2a_router)

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
def index():
    return FileResponse("static/index.html")
