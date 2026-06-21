"""
A2A (Agent2Agent) JSON-RPC transport — Phase 6 interoperability.

A2A over HTTP is the cross-vendor lingua franca for agent-to-agent
communication: exposing it lets ANY A2A-capable agent talk to Vokter by web,
not only Nostr peers. Discovery is the agent card at
/.well-known/agent-card.json (served by agent_routes); this module adds the
JSON-RPC endpoint that card points to.

  POST /a2a   — JSON-RPC 2.0. Supported method: message/send.

Trust: unauthenticated callers may only use the public 'introduce' handshake.
A caller presenting the bearer token from VOKTER_A2A_TOKEN is trusted for the
private tools (ask/wallet/plan/browse). The trust *enforcement* lives in
agent_dispatch and fails closed; this adapter only reads the Authorization
header to decide trust and translates JSON-RPC ↔ dispatch_message.

Adapter only — no business logic here.
"""
import hmac
import json
import uuid

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from agent_dispatch import dispatch_message
from config import A2A_MAX_BODY, A2A_TOKEN
from ratelimit import a2a_allowed

router = APIRouter()

# JSON-RPC 2.0 error codes
_PARSE_ERROR      = -32700
_INVALID_REQUEST  = -32600
_METHOD_NOT_FOUND = -32601
_INVALID_PARAMS   = -32602
_RATE_LIMITED     = -32000   # server-error range (no standard JSON-RPC code)


def _err(id_, code: int, message: str, status: int = 200) -> JSONResponse:
    return JSONResponse(
        {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}},
        status_code=status,
    )


class _BodyTooLarge(Exception):
    pass


async def _read_capped(request: Request, cap: int) -> bytes:
    """Read the body, aborting once it exceeds cap bytes. Streamed so an
    oversized body is rejected without being fully buffered — a Content-Length
    header is bypassable (chunked / lying), so this is the real cap."""
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > cap:
            raise _BodyTooLarge
        chunks.append(chunk)
    return b"".join(chunks)


def _extract_text(message: dict) -> str:
    """Concatenate the text parts of an A2A Message (file/data parts ignored)."""
    parts = message.get("parts") or []
    texts = [
        p.get("text", "")
        for p in parts
        if isinstance(p, dict) and p.get("kind") == "text"
    ]
    return "\n".join(t for t in texts if t).strip()


def _is_trusted(request: Request) -> bool:
    """A caller is trusted only if it presents the configured bearer token."""
    if not A2A_TOKEN:
        return False
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    # Constant-time compare so the token can't be recovered by timing.
    return scheme.lower() == "bearer" and hmac.compare_digest(token, A2A_TOKEN)


@router.post("/a2a")
async def a2a_rpc(request: Request):
    # Trust check first (header-only, cheap): trusted peers are exempt from the
    # rate limit so a flood can't knock over your own fleet, and untrusted load
    # is shed before the capped read + the expensive LLM dispatch.
    trusted = _is_trusted(request)
    if not trusted and not a2a_allowed():
        return _err(None, _RATE_LIMITED, "Rate limit exceeded", status=429)

    try:
        raw = await _read_capped(request, A2A_MAX_BODY)
    except _BodyTooLarge:
        return _err(None, _INVALID_REQUEST, "Request body too large", status=413)

    try:
        body = json.loads(raw)
    except Exception:
        return _err(None, _PARSE_ERROR, "Parse error")

    if not isinstance(body, dict) or body.get("jsonrpc") != "2.0":
        req_id = body.get("id") if isinstance(body, dict) else None
        return _err(req_id, _INVALID_REQUEST, "Invalid JSON-RPC 2.0 request")

    req_id = body.get("id")
    method = body.get("method")
    if method != "message/send":
        return _err(req_id, _METHOD_NOT_FOUND, f"Method not found: {method}")

    message = (body.get("params") or {}).get("message") or {}
    text = _extract_text(message)
    if not text:
        return _err(req_id, _INVALID_PARAMS,
                    "message.parts must contain a non-empty text part")

    # contextId carries conversation continuity; generate one for a new thread.
    context_id = message.get("contextId") or str(uuid.uuid4())
    answer = await dispatch_message(text, context_id, trusted=trusted)

    # Respond with a direct A2A Message (kind="message").
    return JSONResponse({
        "jsonrpc": "2.0",
        "id": req_id,
        "result": {
            "role": "agent",
            "parts": [{"kind": "text", "text": answer}],
            "messageId": str(uuid.uuid4()),
            "contextId": context_id,
            "kind": "message",
        },
    })
