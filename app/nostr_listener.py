"""
Vokter Nostr listener — Phase 6 interoperability.

Layer 3 identity: a stable secp256k1 keypair derived from the master key.
Listens for NIP-17 private direct messages (NIP-59 gift wrap) addressed to
Vokter's Nostr public key and routes them to the local tool registry via HTTP.

Unlike NIP-04, gift wrap hides metadata: the outer event (kind 1059) is signed
by a throwaway key, so relays cannot see who is talking to whom. The real
sender is revealed only after unwrapping and is cryptographically authenticated
by the inner seal signature.

DM format accepted:
  Plain text   → treated as a question for the 'ask' tool
  JSON         → {"tool": "hello|browse|ask|wallet_balance|plan", "args": {...}}
  'hello'      → returns Vokter's A2A agent card (public identity + capabilities)

Start: disabled unless VOKTER_NOSTR_RELAYS is set.
Relay format: comma-separated WSS URLs.
  e.g. VOKTER_NOSTR_RELAYS=wss://relay.damus.io,wss://nos.lol

Set VOKTER_NOSTR_ALLOWED_PUBKEYS to a comma-separated list of hex pubkeys to
restrict who can send commands. Leave unset to accept DMs from any pubkey
(only safe on a private relay).

Architecture: protocol adapter only — all business logic runs in the
FastAPI app, called via HTTP on localhost:8080.
"""
import asyncio
import json
import logging
import os

import httpx
from nostr_sdk import (
    Client,
    Filter,
    HandleNotification,
    Keys,
    Kind,
    NostrSigner,
    RelayMessage,
    RelayUrl,
    SecretKey,
)

from identity import get_nostr_privkey

log = logging.getLogger("vokter.nostr")

_BASE            = "http://localhost:8080"
_RECONNECT_DELAY = 30  # seconds between reconnect attempts
_HTTP_TIMEOUT    = httpx.Timeout(connect=5.0, read=300.0, write=10.0, pool=5.0)
_http            = httpx.AsyncClient(timeout=_HTTP_TIMEOUT)

# Conversation continuity: map sender hex pubkey → last conversation_id.
_conversations: dict[str, str] = {}


def _configured_relays() -> list[str]:
    raw = os.getenv("VOKTER_NOSTR_RELAYS", "")
    return [r.strip() for r in raw.split(",") if r.strip()]


def _allowed_pubkeys() -> set[str] | None:
    """Return the set of permitted hex pubkeys, or None for unrestricted."""
    raw  = os.getenv("VOKTER_NOSTR_ALLOWED_PUBKEYS", "")
    keys = {k.strip() for k in raw.split(",") if k.strip()}
    return keys if keys else None


async def _tool_call(sender_hex: str, text: str) -> str:
    """Route a decrypted DM to the local REST API and return the response."""
    try:
        obj  = json.loads(text)
        tool = (obj.get("tool") or "ask").lower().strip()
        args = obj.get("args") or {}
    except (json.JSONDecodeError, AttributeError):
        tool, args = "ask", {"question": text}

    # A bare greeting (plain text or {"tool":"hello"}) is the handshake.
    if text.strip().lower() in ("hello", "whoami"):
        tool = "hello"

    try:
        if tool in ("hello", "whoami"):
            # Agent-to-agent handshake: return Vokter's public A2A agent card.
            r = await _http.get(f"{_BASE}/api/agent/card")
            r.raise_for_status()
            return json.dumps(r.json())

        if tool == "ask":
            payload = {"question": args.get("question") or text}
            conv_id = _conversations.get(sender_hex)
            if conv_id:
                payload["conversation_id"] = conv_id
            r = await _http.post(f"{_BASE}/api/ask", json=payload)
            r.raise_for_status()
            data = r.json()
            _conversations[sender_hex] = data["conversation_id"]
            return data["answer"]

        if tool == "browse":
            r = await _http.post(
                f"{_BASE}/api/browse",
                json={"url": args.get("url", "")},
            )
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
                "POST", f"{_BASE}/api/plan",
                json={"goal": args.get("goal") or text},
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
            "Available: ask, browse, wallet_balance, plan"
        )

    except httpx.HTTPStatusError as exc:
        log.warning("API error for tool=%r: %s", tool, exc.response.text[:200])
        return f"Error: the local API returned {exc.response.status_code}"
    except Exception as exc:
        log.exception("Tool call failed for tool=%r", tool)
        return f"Error processing your request: {exc}"


class _DMHandler(HandleNotification):
    def __init__(self, client: Client, allowed: set[str] | None):
        self._client  = client
        self._allowed = allowed

    async def handle(self, relay_url: str, subscription_id, event) -> None:
        if event.kind().as_u16() != 1059:  # NIP-59 gift wrap
            return

        # The gift wrap is signed by a throwaway key, so event.author() is NOT
        # the real sender. Unwrap first; the inner seal authenticates the true
        # sender, and only then is the allowlist meaningful.
        try:
            unwrapped = await self._client.unwrap_gift_wrap(event)
        except Exception:
            log.debug("Could not unwrap gift wrap — ignoring")
            return

        sender     = unwrapped.sender()
        sender_hex = sender.to_hex()

        if self._allowed is not None and sender_hex not in self._allowed:
            log.debug("DM from unlisted pubkey %s — ignored", sender.to_bech32())
            return

        plaintext = unwrapped.rumor().content()
        log.info("DM from %s: %r", sender.to_bech32(), plaintext[:120])
        response = await _tool_call(sender_hex, plaintext)
        log.debug("Replying: %r", response[:120])

        try:
            await self._client.send_private_msg(sender, response, [])
        except Exception as exc:
            log.error("Failed to send reply to %s: %s", sender.to_bech32(), exc)

    async def handle_msg(self, relay_url: str, msg: RelayMessage) -> None:
        pass  # relay protocol housekeeping — nothing to do


async def start() -> None:
    """
    Background asyncio task.  Connect to configured Nostr relays, subscribe
    to NIP-17 gift-wrapped DMs addressed to Vokter's public key, and handle
    them.  Reconnects automatically on error.  Exits cleanly on CancelledError.
    """
    relays = _configured_relays()
    if not relays:
        log.info("VOKTER_NOSTR_RELAYS not set — Nostr listener disabled")
        return

    allowed = _allowed_pubkeys()

    privkey_bytes = get_nostr_privkey()
    secret_key    = SecretKey.from_bytes(privkey_bytes)
    keys          = Keys(secret_key=secret_key)
    npub          = keys.public_key().to_bech32()
    log.info("Nostr identity: %s", npub)
    log.info("Nostr relays:   %s", relays)
    if allowed:
        log.info("Nostr allowlist: %d pubkey(s)", len(allowed))
    else:
        log.warning(
            "VOKTER_NOSTR_ALLOWED_PUBKEYS not set — "
            "accepting DMs from any Nostr pubkey"
        )

    while True:
        client: Client | None = None
        try:
            signer = NostrSigner.keys(keys)
            client = Client(signer)

            for relay_url in relays:
                await client.add_relay(RelayUrl.parse(relay_url))
            await client.connect()

            # kind 1059 = NIP-59 gift wrap. limit(0) = only new wraps, no backlog.
            gw_filter = Filter().kind(Kind(1059)).pubkey(keys.public_key()).limit(0)
            await client.subscribe(gw_filter)
            log.info("Nostr listener ready — npub: %s", npub)

            await client.handle_notifications(_DMHandler(client, allowed))

        except asyncio.CancelledError:
            log.info("Nostr listener shutting down")
            return
        except Exception:
            log.exception(
                "Nostr listener error — reconnecting in %ds", _RECONNECT_DELAY
            )
            try:
                await asyncio.sleep(_RECONNECT_DELAY)
            except asyncio.CancelledError:
                log.info("Nostr listener shutting down during reconnect wait")
                return
        finally:
            if client is not None:
                try:
                    await client.disconnect()
                except Exception:
                    pass
