"""
Agent message dispatch — core, protocol-agnostic.

Every protocol adapter (Nostr NIP-17, A2A JSON-RPC over HTTP, MCP, ...) does the
same job: turn an incoming agent message into a plain-text payload plus a stable
context key, then call dispatch_message() here. This module owns the verb
routing, the conversation continuity, and — critically — the trust boundary.
Adapters hold no business logic; they only translate their transport.

Trust boundary
--------------
Verbs split into two sets:

  * PUBLIC ('introduce'/'hello'/'whoami') — return only Vokter's public agent
    card. Safe for any caller, authenticated or not.

  * Everything else ('ask', 'browse', 'wallet_balance', 'plan') touches the
    human's private data or money. These require trusted=True.

An adapter passes trusted=True ONLY when it has established that the human
authorised the caller (Nostr: the authenticated sender passed the allowlist
gate; HTTP: a valid bearer token). The default is trusted=False, so a new or
misconfigured transport fails closed — it can never leak private data by
omission. This is what makes the 'dataSharing: none-without-permission'
guarantee on the agent card true at the endpoint, not just in advertising.
"""
import json
import logging

import httpx

from auth import admin_headers

log = logging.getLogger("vokter.dispatch")

_BASE    = "http://localhost:8080"
_TIMEOUT = httpx.Timeout(connect=5.0, read=300.0, write=10.0, pool=5.0)
# This dispatcher is part of the trusted server, so it authenticates to the
# local admin API (gated by the H1 middleware) with the admin token.
_http    = httpx.AsyncClient(timeout=_TIMEOUT, headers=admin_headers())

# Verbs any caller may use — they reveal only the public agent card.
_PUBLIC_VERBS = {"introduce", "hello", "whoami"}

# Conversation continuity: context key (sender pubkey, A2A contextId, ...) →
# Vokter conversation_id. Process-local — do not run multiple uvicorn workers.
_conversations: dict[str, str] = {}

_UNTRUSTED_REPLY = (
    "I only share my public identity card with unauthenticated callers. "
    "Querying my human's data, wallet, or running tasks requires authorisation. "
    'Send {"tool": "introduce"} to read my agent card.'
)


def _parse_verb(text: str) -> tuple[str, dict]:
    """Resolve (tool, args) from a message: JSON {"tool","args"} or plain text."""
    try:
        obj  = json.loads(text)
        tool = (obj.get("tool") or "ask").lower().strip()
        args = obj.get("args") or {}
    except (json.JSONDecodeError, AttributeError):
        tool, args = "ask", {"question": text}

    # A bare greeting (plain text or JSON) is the public handshake.
    if text.strip().lower() in _PUBLIC_VERBS:
        tool = "introduce"
    return tool, args


async def dispatch_message(text: str, context_key: str, *, trusted: bool = False) -> str:
    """Route an incoming agent message to a local tool and return a text answer.

    text         — the message payload (plain text or {"tool","args"} JSON)
    context_key  — stable per-peer key for conversation continuity
    trusted      — whether the adapter has authorised this caller for private
                   tools. Defaults to False (fail closed).
    """
    tool, args = _parse_verb(text)

    try:
        if tool in _PUBLIC_VERBS:
            r = await _http.get(f"{_BASE}/api/agent/card")
            r.raise_for_status()
            return json.dumps(r.json())

        # Past this point every verb touches private data or money.
        if not trusted:
            log.info("Untrusted caller requested %r — refused", tool)
            return _UNTRUSTED_REPLY

        if tool == "ask":
            payload = {"question": args.get("question") or text}
            conv_id = _conversations.get(context_key)
            if conv_id:
                payload["conversation_id"] = conv_id
            r = await _http.post(f"{_BASE}/api/ask", json=payload)
            r.raise_for_status()
            data = r.json()
            _conversations[context_key] = data["conversation_id"]
            return data["answer"]

        if tool == "browse":
            r = await _http.post(f"{_BASE}/api/browse", json={"url": args.get("url", "")})
            r.raise_for_status()
            d = r.json()
            return f"Stored {d['chunks']} chunks from {d['doc']}."

        if tool == "wallet_balance":
            r = await _http.get(f"{_BASE}/api/wallet/balance")
            r.raise_for_status()
            d = r.json()
            return f"{d['balance']:,} {d['unit']} ({d['adapter']})"

        if tool == "plan":
            answer = "[no answer returned]"
            async with _http.stream(
                "POST", f"{_BASE}/api/plan", json={"goal": args.get("goal") or text},
            ) as resp:
                resp.raise_for_status()
                buf = ""
                async for chunk in resp.aiter_text():
                    buf += chunk
                    lines = buf.split("\n")
                    buf   = lines.pop()
                    for line in lines:
                        if not line.startswith("data: "):
                            continue
                        try:
                            ev = json.loads(line[6:])
                            if ev.get("type") == "done":
                                answer = ev.get("answer", answer)
                        except json.JSONDecodeError:
                            pass
            return answer

        return (
            f"Unknown tool: {tool!r}. "
            "Available: introduce, ask, browse, wallet_balance, plan"
        )

    except httpx.HTTPStatusError as exc:
        log.warning("API error for tool=%r: %s", tool, exc.response.text[:200])
        return f"Error: the local API returned {exc.response.status_code}"
    except Exception as exc:
        log.exception("Tool call failed for tool=%r", tool)
        return f"Error processing your request: {exc}"
