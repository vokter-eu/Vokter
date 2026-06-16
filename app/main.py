"""
Vokter — Your local AI guardian.

Not a single call leaves your machine. Check it: the only host
this code talks to is the local Ollama container.
"""
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import identity  # noqa: F401 — triggers master key init on startup

from ingestion import router as ingestion_router
from chat import router as chat_router
from email_connector import router as email_router
from voice.whisper import router as whisper_router
from voice.piper import router as piper_router

app = FastAPI(title="Vokter", version="0.3.0")
app.include_router(ingestion_router)
app.include_router(chat_router)
app.include_router(email_router)
app.include_router(whisper_router)
app.include_router(piper_router)

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
def index():
    return FileResponse("static/index.html")
