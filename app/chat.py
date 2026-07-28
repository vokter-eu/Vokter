import hmac
import logging
import time
import uuid
from contextlib import closing

from fastapi import APIRouter, Header
from pydantic import BaseModel

from agent_config import build_system_prompt, get_config
from config import CHAT_MODEL, HUMAN_SESSION_TOKEN, MAX_HISTORY
from db import get_db
from engine import ENGINE, ChatRequest
from rag import retrieve
import memory

router = APIRouter()
log = logging.getLogger("vokter.chat")


class Question(BaseModel):
    question: str
    conversation_id: str | None = None


def is_local_human_session(mark: str | None) -> bool:
    """P2 gate: True only when the request carries THIS launch's human-session token
    (see config.HUMAN_SESSION_TOKEN). Constant-time compare, mirroring
    auth.requires_admin. When no token is configured (raw uvicorn/docker dev, no
    Electron to mint one) → False: strict deny-by-default, memory is withheld. This is
    the single point that ENFORCES "personal memory reaches only the local human
    session, never a peer/MCP/webhook" — a comment can promise that, only this can
    keep it (docs/threat-model-prompt-injection.md §7-8)."""
    token = HUMAN_SESSION_TOKEN
    if not token:
        return False
    if mark is None:
        return False
    # Compare as BYTES, like auth.admin_token_ok — comparing str with compare_digest
    # raises on a non-ASCII header (Starlette decodes headers latin-1), which a crafted
    # X-Vokter-Human-Session could trigger; bytes avoids that 500 (still fail-closed).
    return hmac.compare_digest(mark.encode(), token.encode())


def build_chat_system(cfg: dict, human: bool) -> str:
    """Assemble the chat system prompt. Personal memory (P2) is appended ONLY for the
    local human session; withheld for every other caller (deny-by-default). Pure — no
    network, no model — so a test can assert the invariant directly:
      human=True  → byte-identical to Phase 1b: build_system_prompt(cfg) + memory.system_block()
      human=False → byte-identical to a memory-less Vokter: build_system_prompt(cfg)"""
    base = build_system_prompt(cfg)
    if human:
        return base + memory.system_block()
    return base


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
async def ask(q: Question, x_vokter_human_session: str | None = Header(default=None)):
    human = is_local_human_session(x_vokter_human_session)

    # Explicit memory save ("recuérdame que…" / "remember that…"): store the fact
    # VERBATIM, confirm predictably in the same language, and return — it never runs
    # through the model or RAG. The user can see/edit/delete it in the review window.
    # GATED ON THE HUMAN SESSION (deny-by-default WRITE): a peer's "remember that X"
    # over /api/ask must not write to the table that later joins the human's prompt —
    # that would be indirect injection into the human's future sessions. Without the
    # human mark this branch is skipped and the sentence is treated as a normal
    # question; nothing is stored.
    if human:
        fact = memory.parse_remember(q.question)
        if fact:
            memory.add(fact, source="told")
            conv_id = q.conversation_id or str(uuid.uuid4())
            answer = (f"Anotado: {fact}" if memory.trigger_lang(q.question) == "es"
                      else f"Got it — I'll remember: {fact}")
            _save_turn(conv_id, q.question, answer)
            return {"answer": answer, "sources": [], "conversation_id": conv_id}

    cfg = get_config()
    model       = cfg.get("chat_model")  or CHAT_MODEL
    max_history = int(cfg.get("max_history") or MAX_HISTORY)

    # RAG is now AUGMENTING, not gating: retrieve, but keep only chunks above the
    # relevance floor. A greeting or an off-topic message matches nothing well, so
    # `relevant` is empty and Vokter simply CONVERSES — no "upload a document" wall,
    # the model is always called. When real matches exist we ground + cite as before.
    scored = await retrieve(q.question, top_k=int(cfg.get("rag_chunks") or 4))
    min_score = float(cfg.get("rag_min_score") or 0.57)
    relevant = [(s, doc, content) for (s, doc, content) in scored if s >= min_score]

    # Phase 1b + P2 gate: personal memory joins the SYSTEM prompt ONLY for the local
    # human session; withheld for any other caller (build_chat_system, deny-by-default).
    # With the human mark this is byte-identical to before.
    system  = build_chat_system(cfg, human)
    conv_id = q.conversation_id or str(uuid.uuid4())
    history = _load_history(conv_id, max_history)

    # Fail-closed VISIBLE: if a caller was denied memory while facts DO exist, leave a
    # trace (log here, `memory_withheld` in the response so the UI can say so) — a Vokter
    # that stops recognising you must never be mistaken for lost memory. Silent when there
    # are no facts to withhold, to avoid noise.
    #   Level is INFO, not WARNING, on purpose: at THIS point the backend cannot tell an
    #   expected peer/MCP denial from a broken-human-wiring denial. The obvious
    #   discriminator (the caller carries the admin/A2A token) is not viable — the
    #   orchestrator sets no ADMIN_TOKEN in the shipped product, so internal callers reach
    #   /api/ask with no distinguishing header, and the A2A token never leaves the /a2a
    #   boundary. A WARNING on every routine peer ask would only train alarm-fatigue. The
    #   human's own case is covered VISIBLY by the UI notice (memory_withheld) — this log
    #   is the secondary, diagnostic trace.
    memory_withheld = (not human) and bool(memory.system_block())
    if memory_withheld:
        log.info("[memory] withheld: request lacks a valid human-session mark "
                 "(personal memory not injected)")

    if relevant:
        context = "\n\n---\n\n".join(f"[{doc}]\n{content}" for _, doc, content in relevant)
        user_content = f"Context from your documents:\n{context}\n\nUser: {q.question}"
        sources = sorted({doc for _, doc, _ in relevant})
    else:
        user_content = q.question       # plain conversation — no context to ground in
        sources = []

    # History stores raw turns — document context is only injected for the current one.
    messages = (
        [{"role": "system", "content": system}]
        + history
        + [{"role": "user", "content": user_content}]
    )

    answer = await ENGINE.chat(ChatRequest(
        messages=messages, model=model, context_size=8192, timeout=300,
    ))

    _save_turn(conv_id, q.question, answer)

    return {"answer": answer, "sources": sources, "conversation_id": conv_id,
            "memory_withheld": memory_withheld}
