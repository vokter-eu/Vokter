"""Personal-memory API (Phase 1) — the transparency window.

GET    /api/memory         — everything Vokter knows about you (newest first)
POST   /api/memory         — add a fact  {content}
PATCH  /api/memory/{id}    — correct a fact  {content}
DELETE /api/memory/{id}    — forget one fact
DELETE /api/memory         — "forget everything about me" (real delete + VACUUM)
POST   /api/memory/suggest — Phase 2b: PROPOSE facts noticed in chat (never stores)

Loopback-only, like the rest of the local API. The user sees and controls ALL of
it — nothing is hidden.
"""
import unicodedata
from contextlib import closing

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import memory
from db import get_db

router = APIRouter()

_CONTEXT_TURNS = 4   # recent user turns given to the extractor to resolve references


class MemoryIn(BaseModel):
    content: str


class SuggestIn(BaseModel):
    message: str
    conversation_id: str | None = None


def _norm(s: str) -> str:
    """lowercase + strip accents — so dedupe ignores case/accents."""
    s = unicodedata.normalize("NFKD", s.strip().lower())
    return "".join(c for c in s if not unicodedata.combining(c))


def _recent_user_context(conv_id: str, message: str) -> list[str]:
    """Up to the last _CONTEXT_TURNS user turns BEFORE the current message, oldest
    first. The frontend calls /suggest after /api/ask has already saved this turn,
    so the newest stored user turn IS `message` — drop it and return the ones
    before. (If it isn't saved yet, nothing is dropped — the extractor just sees a
    little more context, which is harmless: it only ever PROPOSES.)"""
    with closing(get_db()) as db:
        rows = db.execute(
            "SELECT content FROM conversations WHERE conv_id=? AND role='user'"
            " ORDER BY seq DESC LIMIT ?",
            (conv_id, _CONTEXT_TURNS + 1),
        ).fetchall()
    turns = [r[0] for r in reversed(rows)]      # oldest → newest
    if turns and turns[-1] == message:
        turns = turns[:-1]                       # drop the current message itself
    return turns[-_CONTEXT_TURNS:]


@router.get("/api/memory")
def memory_list():
    return {"memory": memory.list_all()}


@router.post("/api/memory")
def memory_add(item: MemoryIn):
    content = item.content.strip()
    if not content:
        raise HTTPException(400, "empty memory")
    return memory.add(content, source="told")


@router.patch("/api/memory/{mem_id}")
def memory_edit(mem_id: int, item: MemoryIn):
    content = item.content.strip()
    if not content:
        raise HTTPException(400, "empty memory")
    if not memory.edit(mem_id, content):
        raise HTTPException(404, "no such memory")
    return {"ok": True, "id": mem_id, "content": content}


@router.delete("/api/memory/{mem_id}")
def memory_delete(mem_id: int):
    if not memory.delete(mem_id):
        raise HTTPException(404, "no such memory")
    return {"ok": True, "id": mem_id}


@router.delete("/api/memory")
def memory_forget_all():
    removed = memory.forget_all()
    return {"ok": True, "forgotten": removed}


@router.post("/api/memory/suggest")
async def memory_suggest(item: SuggestIn):
    """Phase 2b — PROPOSE durable facts noticed in the user's latest message. This
    NEVER stores: it returns candidates for the frontend to show as a confirm chip;
    only an explicit Guardar (POST /api/memory) writes anything. So "never remember
    without the user's OK" stays true by construction — this path has no write.

    Additive to the chat: the frontend calls it AFTER rendering the /api/ask answer,
    so /api/ask is untouched and no latency is added to the visible reply.

    Dedupe (a): drop anything already in memory. Dedupe (b) — facts the user already
    dismissed — lives in the frontend session only; it is deliberately NOT persisted
    here (persisting a dismissal would put a row in the memory table and break the
    invariant). Known limit: dismissals do not survive a restart, so a rejected fact
    can be re-proposed in a later session. Acceptable for now; if it nags in 2c, the
    fix is a SEPARATE dismissed-suggestions table (never touches `memory`)."""
    message = item.message.strip()
    if not message:
        return {"suggestions": []}
    context = (_recent_user_context(item.conversation_id, message)
               if item.conversation_id else [])
    proposed = await memory.extract_candidate(message, context=context)
    existing = {_norm(m["content"]) for m in memory.list_all()}
    suggestions = [f for f in proposed if _norm(f) not in existing]
    return {"suggestions": suggestions}
