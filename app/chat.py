import time
import uuid
from contextlib import closing

from fastapi import APIRouter
from pydantic import BaseModel

from agent_config import build_system_prompt, get_config
from config import CHAT_MODEL, MAX_HISTORY
from db import get_db
from engine import ENGINE, ChatRequest
from rag import retrieve

router = APIRouter()


class Question(BaseModel):
    question: str
    conversation_id: str | None = None


def _load_history(conv_id: str, limit: int) -> list[dict]:
    with closing(get_db()) as db:
        rows = db.execute(
            "SELECT role, content FROM conversations"
            " WHERE conv_id=? ORDER BY seq DESC LIMIT ?",
            (conv_id, limit),
        ).fetchall()
    return [{"role": r[0], "content": r[1]} for r in reversed(rows)]


def _save_turn(conv_id: str, question: str, answer: str) -> None:
    now = time.time()
    with closing(get_db()) as db:
        db.execute(
            "INSERT INTO conversations(conv_id, role, content, ts) VALUES(?,?,?,?)",
            (conv_id, "user", question, now),
        )
        db.execute(
            "INSERT INTO conversations(conv_id, role, content, ts) VALUES(?,?,?,?)",
            (conv_id, "assistant", answer, now),
        )
        db.commit()


@router.post("/api/ask")
async def ask(q: Question):
    cfg = get_config()
    model       = cfg.get("chat_model")  or CHAT_MODEL
    max_history = int(cfg.get("max_history") or MAX_HISTORY)

    scored = await retrieve(q.question, top_k=int(cfg.get("rag_chunks") or 4))
    if not scored:
        return {
            "answer": "You haven't taught me any documents yet. Upload one and ask me about it.",
            "sources": [],
            "conversation_id": q.conversation_id,
        }

    context = "\n\n---\n\n".join(f"[{doc}]\n{content}" for _, doc, content in scored)
    system  = build_system_prompt(cfg)

    conv_id = q.conversation_id or str(uuid.uuid4())
    history = _load_history(conv_id, max_history)

    # History stores raw Q/A pairs — RAG context is only injected for the current turn
    messages = (
        [{"role": "system", "content": system}]
        + history
        + [{"role": "user", "content": f"Context:\n{context}\n\nQuestion: {q.question}"}]
    )

    answer = await ENGINE.chat(ChatRequest(
        messages=messages, model=model, context_size=8192, timeout=300,
    ))

    _save_turn(conv_id, q.question, answer)

    return {
        "answer":          answer,
        "sources":         sorted({doc for _, doc, _ in scored}),
        "conversation_id": conv_id,
    }
