"""
Outbound A2A client — Vokter initiating a conversation with another agent.

Two operations, both over standard A2A-over-HTTP:
  * fetch_card(url)        — GET the peer's /.well-known/agent-card.json
  * call_a2a(url, text)    — POST a JSON-RPC message/send and return the reply

Every successful contact is recorded in the known-agents registry.

SSRF gate
---------
These functions fetch URLs the human (or, later, the planner) supplies, so a
hostile target could try to make Vokter hit internal services. We allow LAN
addresses on purpose — the two-device test points one Vokter at another on the
local network — but block the dangerous targets: loopback (Vokter's own admin
API on localhost), link-local / cloud metadata (169.254.169.254), multicast and
reserved ranges. fetch_card (arbitrary GET) is the sharp vector; call_a2a is
gated identically.
"""
import ipaddress
import json
import socket
import uuid
from urllib.parse import urlparse, urlunparse

import httpx

from known_agents import is_blocked, record_interaction

_TIMEOUT = httpx.Timeout(connect=5.0, read=60.0, write=10.0, pool=5.0)


def _guard_url(url: str) -> None:
    """Raise ValueError if url is not a safe outbound http(s) target."""
    if is_blocked(url):
        raise ValueError("this agent is blocked")
    p = urlparse(url)
    if p.scheme not in ("http", "https"):
        raise ValueError("only http(s) targets are allowed")
    host = p.hostname
    if not host:
        raise ValueError("missing host")

    port = p.port or (443 if p.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise ValueError(f"could not resolve host: {exc}") from exc

    for *_, sockaddr in infos:
        ip = ipaddress.ip_address(sockaddr[0])
        # LAN (is_private) is allowed; everything dangerous is not.
        if (
            ip.is_loopback
            or ip.is_link_local      # 169.254.0.0/16 (incl. metadata) + fe80::/10
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise ValueError(f"blocked address: {ip}")


def _card_url(url: str) -> str:
    """Derive the well-known agent-card URL from any URL on the peer's host."""
    p = urlparse(url)
    return urlunparse((p.scheme, p.netloc, "/.well-known/agent-card.json", "", "", ""))


async def fetch_card(url: str) -> dict:
    """Fetch and record a peer's A2A agent card."""
    _guard_url(url)
    card_url = _card_url(url)
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.get(card_url)
        r.raise_for_status()
        card = r.json()

    record_interaction(
        url,
        transport="a2a-http",
        direction="outbound",
        name=card.get("name") if isinstance(card, dict) else None,
        card_json=json.dumps(card),
    )
    return card


async def call_a2a(
    url: str, text: str, *, token: str | None = None, context_id: str | None = None
) -> str:
    """Send an A2A message/send to a peer and return its text reply."""
    _guard_url(url)
    message: dict = {
        "role": "user",
        "parts": [{"kind": "text", "text": text}],
        "messageId": str(uuid.uuid4()),
    }
    if context_id:
        message["contextId"] = context_id

    payload = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "message/send",
        "params": {"message": message},
    }
    headers = {"Authorization": f"Bearer {token}"} if token else {}

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.post(url, json=payload, headers=headers)
        r.raise_for_status()
        body = r.json()

    if isinstance(body, dict) and body.get("error"):
        raise RuntimeError(f"peer returned error: {body['error']}")

    result = (body or {}).get("result") or {}
    # Reply may be a direct Message (parts) or a Task (artifacts[].parts).
    parts = result.get("parts")
    if not parts and result.get("artifacts"):
        parts = result["artifacts"][0].get("parts", [])
    reply = "\n".join(
        p.get("text", "")
        for p in (parts or [])
        if isinstance(p, dict) and p.get("kind") == "text"
    ).strip()

    record_interaction(url, transport="a2a-http", direction="outbound")
    return reply or "[peer returned no text]"
