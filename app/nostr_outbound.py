"""
Outbound Nostr — Vokter initiating a conversation with another agent (NIP-17).

Unlike A2A-over-HTTP (a synchronous request/response), Nostr is fire-and-forget:
we publish a gift-wrapped DM and the reply arrives later as a *separate* inbound
event, handled by the listener. To turn that into a request/response call we
correlate the two halves with a custom rumor tag.

Flow
----
  * call_nostr() registers a pending Future under a random correlation id,
    publishes the DM carrying ["vkt-corr", corr_id], then awaits the Future.
  * The peer (another Vokter) echoes the same ["vkt-corr", corr_id] tag on its
    reply (see nostr_listener).
  * The already-running listener catches that reply, sees the corr tag, and calls
    resolve() here — completing the Future. A correlated reply is NOT re-dispatched
    as a fresh command.

Why a tag and not the message body: the body is the text the peer's LLM reads;
keeping correlation in a tag leaves the payload clean. Tag survival across the
NIP-59 gift wrap (custom tag → rumor → unwrap) is verified against live relays.

Security: the correlation id is a uuid4, and resolve() only completes a pending
request when the reply's NIP-17-authenticated sender matches the pubkey we wrote
to — so a third party can neither satisfy nor hijack a Future that isn't theirs.

Process-local registry: the listener and this module share _pending only because
they run in the same process. Do not run multiple uvicorn workers.
"""
import asyncio
import logging
import os
import uuid
from dataclasses import dataclass

from nostr_sdk import (
    Client,
    Keys,
    NostrSigner,
    PublicKey,
    RelayUrl,
    SecretKey,
    Tag,
)

from identity import get_nostr_privkey
from known_agents import is_blocked, record_interaction

log = logging.getLogger("vokter.nostr.out")

# The rumor tag that pairs a reply with the request that asked for it.
CORR_TAG = "vkt-corr"

# A Nostr round-trip is relay → peer listener → the peer's local LLM inference →
# relay → our listener. Align the wait with the HTTP path's read timeout (300s)
# so a slow `ask` on the far side doesn't look like a failure.
DEFAULT_TTL = float(os.getenv("VOKTER_NOSTR_REPLY_TTL", "300"))


@dataclass
class _Pending:
    future: asyncio.Future
    sender_hex: str  # the pubkey we wrote to; only it may resolve this request


# corr_id → pending request awaiting its reply.
_pending: dict[str, _Pending] = {}


def resolve(corr_id: str, sender_hex: str, content: str) -> bool:
    """Complete the Future for corr_id if it is pending and the sender matches.

    Returns True if this message was a correlated reply we consumed — in which
    case the listener must NOT dispatch it as a fresh incoming command.
    """
    p = _pending.get(corr_id)
    if p is None or p.sender_hex != sender_hex:
        return False
    if not p.future.done():            # ignore duplicate replies
        p.future.set_result(content)
    _pending.pop(corr_id, None)
    return True


def _relays() -> list[str]:
    raw = os.getenv("VOKTER_NOSTR_RELAYS", "")
    return [r.strip() for r in raw.split(",") if r.strip()]


def _to_pubkey(target: str) -> PublicKey:
    """Accept 'nostr:npub...', a bare npub, or hex — return a PublicKey."""
    t = target.strip()
    if t.startswith("nostr:"):
        t = t[len("nostr:"):].strip()
    try:
        return PublicKey.parse(t)      # handles both bech32 (npub) and hex
    except Exception as exc:
        raise ValueError(f"invalid Nostr target: {exc}") from exc


async def call_nostr(target: str, text: str, *, ttl: float = DEFAULT_TTL) -> str:
    """Send a NIP-17 DM to a peer and wait (up to ttl seconds) for its reply.

    Raises ValueError (bad target / blocked / no relays) or TimeoutError.
    """
    relays = _relays()
    if not relays:
        raise ValueError("no Nostr relays configured (VOKTER_NOSTR_RELAYS)")

    recipient     = _to_pubkey(target)
    recipient_hex = recipient.to_hex()
    if is_blocked(recipient_hex):
        raise ValueError("this agent is blocked")

    corr_id = uuid.uuid4().hex
    future  = asyncio.get_running_loop().create_future()
    # Register BEFORE sending so a fast reply can never race ahead of the Future.
    _pending[corr_id] = _Pending(future=future, sender_hex=recipient_hex)

    keys   = Keys(secret_key=SecretKey.from_bytes(get_nostr_privkey()))
    client = Client(NostrSigner.keys(keys))
    try:
        for relay_url in relays:
            await client.add_relay(RelayUrl.parse(relay_url))
        await client.connect()

        await client.send_private_msg(recipient, text, [Tag.parse([CORR_TAG, corr_id])])
        record_interaction(
            recipient_hex, transport="nostr", direction="outbound",
            npub=recipient.to_bech32(),
        )
        log.info(
            "Nostr DM sent to %s (corr=%s) — awaiting reply",
            recipient.to_bech32(), corr_id,
        )
        return await asyncio.wait_for(future, timeout=ttl)
    except asyncio.TimeoutError:
        raise TimeoutError(
            f"no reply within {ttl:.0f}s — peer offline or not running Vokter"
        )
    finally:
        _pending.pop(corr_id, None)
        try:
            await client.disconnect()
        except Exception:
            pass
