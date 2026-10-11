import asyncio
import base64
import binascii
import hmac
import json
import logging
import time
import uuid
from contextlib import closing

import httpx
from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agent_config import build_system_prompt, get_config
from config import CHAT_MODEL, HUMAN_SESSION_TOKEN, MAX_HISTORY
from db import get_db
from engine import get_engine, ChatRequest, resolve_base_url
from hwdetect import detect, vision_model
from rag import retrieve
import memory

router = APIRouter()
log = logging.getLogger("vokter.chat")


class Question(BaseModel):
    question: str
    conversation_id: str | None = None
    stream: bool = False              # True → SSE token stream (the Electron shell); the
                                      # plain-fetch / peer / MCP path leaves it False → JSON
    image: str | None = None          # Phase 2: base64 image to ASK ABOUT (image Q&A via the VLM).
                                      # Distinct from /api/docs, which OCRs an image into searchable
                                      # text. Present → this request routes to the vision path.


def _sse(data: dict) -> str:
    """One Server-Sent-Events frame, mirroring planner.py's wire format."""
    return f"data: {json.dumps(data)}\n\n"


def _decode_image(b64: str) -> bytes | None:
    """Raw bytes from a (possibly data-URL-prefixed) base64 image, or None if it isn't valid."""
    s = b64.strip()
    if s.startswith("data:"):
        s = s.split(",", 1)[-1]        # strip a `data:image/png;base64,` prefix if the client sent one
    try:
        return base64.b64decode(s, validate=True)
    except (binascii.Error, ValueError):
        return None


def _reply_es(question: str) -> bool:
    """Should the canned vision reply be in Spanish? Follows the user's CONFIGURED reply language
    (how Vokter is told to talk) — not memory.trigger_lang, which only matches 'recuérdame…' save
    phrasing and would call every Spanish question English. On 'auto', a light cue on the question."""
    lang = (get_config().get("language") or "auto").strip().lower()
    if lang.startswith("es"):
        return True
    if lang.startswith("en"):
        return False
    ql = (question or "").lower()                 # auto → infer from the question
    return ("¿" in ql or "¡" in ql
            or any(w in ql for w in (" qué", " cómo", " dónde", " cuál", " imagen", " foto",
                                     " esta", " este", " hola"))
            or ql.startswith(("qué", "cómo", "dónde", "cuál", "hola", "describe esta")))


async def _vision_installed(model: str) -> bool:
    """Is the VLM actually pulled into the running Ollama? Capability (hwdetect) says the box COULD
    run one; this says one is present to run. A miss → we never call a missing model, we decline."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{resolve_base_url()}/api/tags")
            names = {m.get("name", "") for m in (r.json().get("models") or [])}
    except Exception:
        return False
    # match "qwen2.5vl:7b" against "qwen2.5vl:7b" and the bare "qwen2.5vl" (→ :latest) forms
    return model in names or any(n.split(":", 1)[0] == model.split(":", 1)[0] for n in names)


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


def _canned(q: "Question", conv_id: str, answer: str, human: bool):
    """Return a fixed answer in the shape the caller asked for (SSE if stream, else JSON), and
    persist the turn. Used for the no-VLM decline / OCR-fallback replies — no model runs."""
    _save_turn(conv_id, q.question or "[image]", answer, human)
    if q.stream:
        async def gen_canned():
            yield _sse({"type": "token", "text": answer})
            yield _sse({"type": "done", "answer": answer, "sources": [],
                        "conversation_id": conv_id, "memory_withheld": False})
        return StreamingResponse(gen_canned(), media_type="text/event-stream")
    return {"answer": answer, "sources": [], "conversation_id": conv_id}


async def _answer_about_image(q: "Question", human: bool):
    """Phase 2 image Q&A. Capability-gated (VLM only on capable HW + actually installed); otherwise
    an HONEST decline that falls back to Phase-1 Tesseract OCR ("I can't understand images here,
    but I read this text…"). Never pulls a model, never calls a missing one."""
    conv_id = q.conversation_id or str(uuid.uuid4())
    es = _reply_es(q.question or "")
    raw = _decode_image(q.image or "")
    if raw is None:
        return _canned(q, conv_id,
                       "No he podido leer esa imagen." if es else "I couldn't read that image.",
                       human)

    vm = vision_model(detect())                      # capability: can this box run a VLM, and which?
    model = vm["model"] if vm else None
    installed = bool(model) and await _vision_installed(model)

    if not (model and installed):
        # Honest fallback, two distinct cases — never mislabel a capable box as underpowered.
        # Either way, Phase-1 Tesseract OCR still reads any text off the image so the reply is useful.
        if model and not installed:              # capable HW, VLM just not pulled yet
            head = ("El modelo de visión aún no está descargado en este equipo." if es else
                    "The vision model isn't downloaded on this machine yet.")
        else:                                    # not a capable machine → understanding not offered
            head = ("No puedo entender imágenes en este equipo (hace falta una máquina más potente)."
                    if es else
                    "I can't understand images on this machine (that needs a more capable computer).")
        from ocr import ocr_image
        text = ocr_image(raw).strip()
        if text:
            join = (" Aun así, he leído el texto de la imagen:\n\n" if es else
                    " Still, here's the text I read in the image:\n\n")
            return _canned(q, conv_id, head + join + text, human)
        tail = (" Y no he encontrado texto legible en ella." if es else
                " And I couldn't find any readable text in it.")
        return _canned(q, conv_id, head + tail, human)

    # VLM path. System prompt still carries personal memory for the human session (P2 gate unchanged
    # inside build_chat_system); the image + question ride the user turn. No document RAG here.
    cfg = get_config()
    system  = await build_chat_system(cfg, human, q.question)
    history = _load_history(conv_id, int(cfg.get("max_history") or MAX_HISTORY), human)
    prompt  = (q.question or "").strip() or (
        "¿Qué hay en esta imagen? Descríbela." if es else "What's in this image? Describe it.")
    b64 = base64.b64encode(raw).decode()
    messages = (
        [{"role": "system", "content": system}]
        + history
        + [{"role": "user", "content": prompt, "images": [b64]}]
    )
    memory_withheld = (not human) and memory.has_facts()
    save_q = "[image] " + prompt          # mark that this turn carried an image, for history
    # Image-encode on CPU is slow (tens of seconds before the first token) → a generous timeout.
    req = ChatRequest(messages=messages, model=model, context_size=8192, timeout=600)

    if q.stream:
        async def gen():
            parts: list[str] = []
            try:
                async for delta in get_engine().chat_stream(req):
                    parts.append(delta)
                    yield _sse({"type": "token", "text": delta})
            except asyncio.CancelledError:
                raise
            except HTTPException as e:
                yield _sse({"type": "error", "detail": str(e.detail)})
                return
            except Exception:
                log.exception("[vision] streaming generation failed")
                yield _sse({"type": "error", "detail": "engine error"})
                return
            full = "".join(parts)
            _save_turn(conv_id, save_q, full, human)
            yield _sse({"type": "done", "answer": full, "sources": [],
                        "conversation_id": conv_id, "memory_withheld": memory_withheld})
        return StreamingResponse(gen(), media_type="text/event-stream")

    answer = await get_engine().chat(req)
    _save_turn(conv_id, save_q, answer, human)
    return {"answer": answer, "sources": [], "conversation_id": conv_id,
            "memory_withheld": memory_withheld}


@router.post("/api/ask")
async def ask(q: Question, x_vokter_human_session: str | None = Header(default=None)):
    human = is_local_human_session(x_vokter_human_session)

    # Phase 2 — image Q&A ("what's in this photo", read a diagram/chart). Routed to the VLM BEFORE
    # the text paths: an image question is never a "remember that…" and must not drag in document
    # RAG (we're asking about THIS image, not the store). Capability-gated + fall-closed inside.
    if q.image:
        return await _answer_about_image(q, human)

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
