import asyncio
import hmac
import json
import logging
import time
import uuid
from contextlib import closing

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agent_config import build_system_prompt, get_config
from config import CHAT_MODEL, HUMAN_SESSION_TOKEN, MAX_HISTORY
from db import get_db
from engine import get_engine, ChatRequest
from rag import retrieve
import memory

router = APIRouter()
log = logging.getLogger("vokter.chat")


class Question(BaseModel):
    question: str
    conversation_id: str | None = None
    stream: bool = False              # True → SSE token stream (the Electron shell); the
                                      # plain-fetch / peer / MCP path leaves it False → JSON


def _sse(data: dict) -> str:
    """One Server-Sent-Events frame, mirroring planner.py's wire format."""
    return f"data: {json.dumps(data)}\n\n"


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


async def build_chat_system(cfg: dict, human: bool, query: str | None = None) -> str:
    """Assemble the chat system prompt. Personal memory (P2) is appended ONLY for the
    local human session; withheld for every other caller (deny-by-default). The P2 gate is
    unchanged — this is the ONE point that keeps "memory reaches only the human session":
      human=False → build_system_prompt(cfg), byte-identical to a memory-less Vokter.
      human=True, query given → base + memory.relevant_block(query): core facts + the facts
        RELEVANT to this message (Direction A / A1), never the whole store.
      human=True, query=None → base + memory.system_block(): the dump-all fallback, kept so
        callers without a query (and the eval's BEFORE baseline / the P2 gate test) still
        get the byte-identical Phase-1b behaviour."""
    base = build_system_prompt(cfg)
    if not human:
        return base
    if query is None:
        return base + memory.system_block()
    return base + await memory.relevant_block(query)


def _load_history(conv_id: str, limit: int, human: bool) -> list[dict]:
    # C2a bit-guard: a caller only ever loads rows of its OWN ownership class. A non-human
    # caller passing the human's conv_id gets ZERO rows — indistinguishable from a
    # non-existent id (no "exists but forbidden" oracle) — and a peer write to the human's
    # conv_id (human_owned=0) never enters the human's loaded history (human reads
    # human_owned=1). Deny-closed and injection-safe. See docs/SECURITY_REVIEW.md C2a.
    with closing(get_db()) as db:
        rows = db.execute(
            "SELECT role, content FROM conversations"
            " WHERE conv_id=? AND human_owned=? ORDER BY seq DESC LIMIT ?",
            (conv_id, 1 if human else 0, limit),
        ).fetchall()
    return [{"role": r[0], "content": r[1]} for r in reversed(rows)]


def _save_turn(conv_id: str, question: str, answer: str, human: bool) -> None:
    now = time.time()
    owned = 1 if human else 0          # C2a: stamp the ownership class at creation
    with closing(get_db()) as db:
        db.execute(
            "INSERT INTO conversations(conv_id, role, content, ts, human_owned) VALUES(?,?,?,?,?)",
            (conv_id, "user", question, now, owned),
        )
        db.execute(
            "INSERT INTO conversations(conv_id, role, content, ts, human_owned) VALUES(?,?,?,?,?)",
            (conv_id, "assistant", answer, now, owned),
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
            row = memory.add(fact, source="told")
            asyncio.create_task(memory.embed_pending())   # embed the new fact in the bg
            conv_id = q.conversation_id or str(uuid.uuid4())
            es = memory.trigger_lang(q.question) == "es"
            answer = (f"Anotado: {fact}" if es else f"Got it — I'll remember: {fact}")
            # Transparency: if saving this pushed the always-on identity block over budget,
            # tell the user which older facts moved to as-needed — they're NOT deleted, still
            # retrieved when relevant (demote-never-delete). This is the one live add path
            # (the chat-first UI has no memory-management view yet), so the note lives here.
            n = len(row.get("demoted") or [])
            if n:
                answer += ("\n\n(" + (
                    f"Nota: moví {n} dato{'s' if n != 1 else ''} más antiguo"
                    f"{'s' if n != 1 else ''} de «siempre en contexto» a «según haga falta» "
                    "para no recargar el prompt — siguen guardados y se usan cuando son relevantes."
                    if es else
                    f"Note: moved {n} older fact{'s' if n != 1 else ''} from always-on to "
                    "as-needed to keep the prompt lean — still saved, and used when relevant."
                ) + ")")
            _save_turn(conv_id, q.question, answer, human=True)  # this branch is inside `if human:`
            if q.stream:
                # No model runs here, so there is nothing to stream — but the client asked
                # for the SSE shape, so give it the same two frames (whole answer as one
                # token, then done) rather than a mismatched JSON body.
                async def gen_told():
                    yield _sse({"type": "token", "text": answer})
                    yield _sse({"type": "done", "answer": answer, "sources": [],
                                "conversation_id": conv_id, "memory_withheld": False})
                return StreamingResponse(gen_told(), media_type="text/event-stream")
            return {"answer": answer, "sources": [], "conversation_id": conv_id}

    cfg = get_config()
    model       = cfg.get("chat_model")  or CHAT_MODEL
    max_history = int(cfg.get("max_history") or MAX_HISTORY)

    # RAG is now AUGMENTING, not gating: hybrid retrieve (vector + FTS keyword, RRF-fused)
    # applies the relevance gate INTERNALLY — a candidate survives only if it clears the
    # semantic floor OR is a genuine keyword hit. A greeting or off-topic message matches
    # nothing, so `relevant` is empty and Vokter simply CONVERSES — no "upload a document"
    # wall, the model is always called. When real matches exist we ground + cite as before.
    min_score = float(cfg.get("rag_min_score") or 0.57)
    relevant = await retrieve(q.question, top_k=int(cfg.get("rag_chunks") or 4),
                              min_score=min_score)

    # Phase 1b + P2 gate + A1 retrieval: personal memory joins the SYSTEM prompt ONLY for
    # the local human session; withheld for any other caller (build_chat_system, deny-by-
    # default). The user's question is threaded in so memory is retrieved query-aware
    # (core facts + relevant facts), not dumped whole.
    system  = await build_chat_system(cfg, human, q.question)
    conv_id = q.conversation_id or str(uuid.uuid4())
    history = _load_history(conv_id, max_history, human)

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
    memory_withheld = (not human) and memory.has_facts()
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

    if q.stream:
        # Stream the generation as SSE. The system prompt (with personal memory, if this
        # is the human session) is ALREADY assembled above — memory injection is a
        # pre-model step, so it rides the streamed reply exactly as it did the JSON one.
        # Sources + memory_withheld are known before a token is generated; they travel in
        # the final `done` frame after the text, per the request.
        async def gen():
            parts: list[str] = []
            try:
                async for delta in get_engine().chat_stream(ChatRequest(
                        messages=messages, model=model, context_size=8192, timeout=300)):
                    parts.append(delta)
                    yield _sse({"type": "token", "text": delta})
            except HTTPException as e:
                # Model missing / engine returned non-200: tell the client and stop.
                # Nothing is saved — a failed generation never happened.
                yield _sse({"type": "error", "detail": str(e.detail)})
                return
            except asyncio.CancelledError:
                # Client hung up mid-stream. Deliberate trade-off (v1): discard the partial
                # turn rather than persist a truncated answer into history. Re-raise so the
                # httpx stream and the ASGI server unwind cleanly.
                raise
            except Exception:
                # Engine unreachable (e.g. a bad engine_url, Ollama down): httpx raises
                # ConnectError etc. Emit a clean error frame instead of tearing the stream.
                log.exception("[chat] streaming generation failed")
                yield _sse({"type": "error", "detail": "engine error"})
                return
            full = "".join(parts)
            _save_turn(conv_id, q.question, full, human)
            yield _sse({"type": "done", "answer": full, "sources": sources,
                        "conversation_id": conv_id, "memory_withheld": memory_withheld})
        return StreamingResponse(gen(), media_type="text/event-stream")

    answer = await get_engine().chat(ChatRequest(
        messages=messages, model=model, context_size=8192, timeout=300,
    ))

    _save_turn(conv_id, q.question, answer, human)

    return {"answer": answer, "sources": sources, "conversation_id": conv_id,
            "memory_withheld": memory_withheld}
