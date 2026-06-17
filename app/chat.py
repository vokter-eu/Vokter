import asyncio
import json
import uuid

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from agent_config import build_system_prompt, get_config
from config import OLLAMA_URL, CHAT_MODEL, MAX_HISTORY
from rag import retrieve

router = APIRouter()

# WARNING: process-local — do NOT run with multiple uvicorn workers
conversations: dict[str, list[dict]] = {}
conversations_lock = asyncio.Lock()
_MAX_CONVERSATIONS = 500  # cap in-memory sessions to avoid unbounded RAM growth


class Question(BaseModel):
    question: str
    conversation_id: str | None = None


@router.post("/api/ask")
async def ask(q: Question):
    cfg = get_config()
    model       = cfg.get("chat_model")  or CHAT_MODEL
    max_history = int(cfg.get("max_history") or MAX_HISTORY)

    scored = await retrieve(q.question, top_k=int(cfg.get("rag_chunks") or 4))
    if not scored:
        return {
            "answer": f"You haven't taught me any documents yet. Upload one and ask me about it.",
            "sources": [],
            "conversation_id": q.conversation_id,
        }

    context = "\n\n---\n\n".join(f"[{doc}]\n{content}" for _, doc, content in scored)
    system  = build_system_prompt(cfg)

    conv_id = q.conversation_id or str(uuid.uuid4())
    history = conversations.get(conv_id, [])

    # History stores raw Q/A pairs — RAG context is only injected for the current turn
    messages = (
        [{"role": "system", "content": system}]
        + history
        + [{"role": "user", "content": f"Context:\n{context}\n\nQuestion: {q.question}"}]
    )

    async with httpx.AsyncClient(timeout=300) as client:
        r = await client.post(
            f"{OLLAMA_URL}/api/chat",
            json={"model": model, "stream": False, "messages": messages,
                  "options": {"num_ctx": 8192}},
        )
    if r.status_code != 200:
        raise HTTPException(502, f"Ollama (chat) returned {r.status_code}. "
                                 f"Did you run 'ollama pull {model}'?")
    try:
        answer = r.json()["message"]["content"]
    except (json.JSONDecodeError, KeyError):
        raise HTTPException(502, "Unexpected response format from Ollama")

    # Locked re-read before write: prevents clobbering concurrent turns on the same conv_id
    async with conversations_lock:
        current = conversations.get(conv_id, [])
        conversations[conv_id] = (current + [
            {"role": "user",      "content": q.question},
            {"role": "assistant", "content": answer},
        ])[-max_history:]
        if len(conversations) > _MAX_CONVERSATIONS:
            conversations.pop(next(iter(conversations)))

    return {
        "answer":          answer,
        "sources":         sorted({doc for _, doc, _ in scored}),
        "conversation_id": conv_id,
    }
