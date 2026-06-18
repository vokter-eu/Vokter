"""
Vokter Nostr listener — Phase 6 interoperability.

Layer 3 identity: a stable secp256k1 keypair derived from the master key.
Listens for NIP-04 encrypted DMs addressed to Vokter's Nostr public key
and routes them to the local tool registry via HTTP.

DM format accepted:
  Plain text   → treated as a question for the 'ask' tool
  JSON         → {"tool": "browse|ask|wallet_balance|plan", "args": {...}}

Start: disabled unless VOKTER_NOSTR_RELAYS is set.
Relay format: comma-separated WSS URLs.
  e.g. VOKTER_NOSTR_RELAYS=wss://relay.damus.io,wss://nos.lol

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
    SecretKey,
)

from identity import get_nostr_privkey

log = logging.getLogger("vokter.nostr")

_BASE            = "http://localhost:8080"
_RECONNECT_DELAY = 30  # seconds between reconnect attempts
_HTTP_TIMEOUT    = httpx.Timeout(connect=5.0, read=300.0, write=10.0, pool=5.0)


def _configured_relays() -> list[str]:
    raw = os.getenv("VOKTER_NOSTR_RELAYS", "")
    return [r.strip() for r in raw.split(",") if r.strip()]


async def _tool_call(text: str) -> str:
    """Route a decrypted DM to the local REST API and return the response."""
    # Try JSON tool call first; fall back to plain-text question
    try:
        obj  = json.loads(text)
        tool = (obj.get("tool") or "ask").lower().strip()
        args = obj.get("args") or {}
    except (json.JSONDecodeError, AttributeError):
        tool, args = "ask", {"question": text}

    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        try:
            if tool in ("ask", ""):
                r = await client.post(
                    f"{_BASE}/api/ask",
                    json={"question": args.get("question") or text},
                )
                return r.json()["answer"]

            if tool == "browse":
                r = await client.post(
                    f"{_BASE}/api/browse",
                    json={"url": args.get("url", "")},
                )
                d = r.json()
                return f"Stored {d['chunks']} chunks from {d['doc']}."

            if tool == "wallet_balance":
                r = await client.get(f"{_BASE}/api/wallet/balance")
                d = r.json()
                return f"{d['balance']:,} {d['unit']} ({d['adapter']})"

            if tool == "plan":
                answer = "[no answer returned]"
                async with client.stream(
                    "POST", f"{_BASE}/api/plan",
                    json={"goal": args.get("goal") or text},
                ) as resp:
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

        except Exception as exc:
            log.exception("Tool call failed for tool=%r", tool)
            return f"Error processing your request: {exc}"


class _DMHandler(HandleNotification):
    def __init__(self, client: Client, keys: Keys):
        self._client = client
        self._keys   = keys

    async def handle(self, relay_url: str, subscription_id, event) -> None:
        if event.kind().as_u16() != 4:
            return

        sender = event.author()
        try:
            plaintext = await self._client.nip04_decrypt(sender, event.content())
        except Exception:
            log.debug("Could not decrypt DM from %s — ignoring", sender.to_bech32())
            return

        log.info("DM from %s: %r", sender.to_bech32(), plaintext[:120])
        response = await _tool_call(plaintext)
        log.debug("Replying: %r", response[:120])

        try:
            await self._client.send_direct_msg(sender, response, None)
        except Exception as exc:
            log.error("Failed to send reply to %s: %s", sender.to_bech32(), exc)

    async def handle_msg(self, relay_url: str, msg: RelayMessage) -> None:
        pass  # relay protocol housekeeping — nothing to do


async def start() -> None:
    """
    Background asyncio task.  Connect to configured Nostr relays, subscribe
    to NIP-04 DMs addressed to Vokter's public key, and handle them.
    Reconnects automatically on error.  Exits cleanly on CancelledError.
    """
    relays = _configured_relays()
    if not relays:
        log.info("VOKTER_NOSTR_RELAYS not set — Nostr listener disabled")
        return

    privkey_bytes = get_nostr_privkey()
    secret_key    = SecretKey.from_slice(privkey_bytes)
    keys          = Keys(secret_key=secret_key)
    npub          = keys.public_key().to_bech32()
    log.info("Nostr identity: %s", npub)
    log.info("Nostr relays:   %s", relays)

    while True:
        client: Client | None = None
        try:
            signer = NostrSigner.keys(keys)
            client = Client(signer)

            for relay_url in relays:
                await client.add_relay(relay_url)
            await client.connect()

            dm_filter = Filter().kind(Kind(4)).pubkey(keys.public_key())
            await client.subscribe([dm_filter], None)
            log.info("Nostr listener ready — npub: %s", npub)

            await client.handle_notifications(_DMHandler(client, keys))

        except asyncio.CancelledError:
            log.info("Nostr listener shutting down")
            return
        except Exception:
            log.exception(
                "Nostr listener error — reconnecting in %ds", _RECONNECT_DELAY
            )
            await asyncio.sleep(_RECONNECT_DELAY)
        finally:
            if client is not None:
                try:
                    await client.disconnect()
                except Exception:
                    pass
