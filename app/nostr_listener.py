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
import logging
import os

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
    Tag,
)

from agent_dispatch import dispatch_message, is_public_request
from identity import get_nostr_privkey
from known_agents import get_trust, is_blocked, record_interaction
from nostr_outbound import CORR_TAG, resolve
from ratelimit import inbound_allowed

log = logging.getLogger("vokter.nostr")


def _corr_id(tags) -> str | None:
    """Return the correlation id carried by a rumor, if any."""
    for tag in tags.to_vec():
        vec = tag.as_vec()
        if len(vec) >= 2 and vec[0] == CORR_TAG:
            return vec[1]
    return None


def _inbound_trusted(sender_hex: str, allowed: set[str] | None) -> bool:
    """Decide PRIVATE-tool access for an inbound peer — local checks only.

    No network here: a per-message web-of-trust lookup would let a pubkey-rotating
    spammer force one relay query per message. Trust is granted by explicit local
    signals: the human's allowlist, a 'trusted' rating, or the trust-all override
    (only sane on a private relay). Everyone else gets the public card only.
    Auto-elevation by who-vouches-for-whom is a separate, deliberate layer.
    """
    if os.getenv("VOKTER_NOSTR_TRUST_ALL") == "1":
        return True
    if allowed is not None and sender_hex in allowed:
        return True
    return get_trust(sender_hex) == "trusted"

_RECONNECT_DELAY = 30  # seconds between reconnect attempts


def _configured_relays() -> list[str]:
    raw = os.getenv("VOKTER_NOSTR_RELAYS", "")
    return [r.strip() for r in raw.split(",") if r.strip()]


def _allowed_pubkeys() -> set[str] | None:
    """Return the set of permitted hex pubkeys, or None for unrestricted."""
    raw  = os.getenv("VOKTER_NOSTR_ALLOWED_PUBKEYS", "")
    keys = {k.strip() for k in raw.split(",") if k.strip()}
    return keys if keys else None


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

        # Reputation: a blocked peer is dropped silently, before any work or DB
        # write — don't let a blocked spammer cost us anything. Block wins even
        # over a correlated reply below.
        if is_blocked(sender_hex):
            log.debug("DM from blocked pubkey %s — dropped", sender.to_bech32())
            return

        plaintext = unwrapped.rumor().content()
        corr_id   = _corr_id(unwrapped.rumor().tags())

        # Is this the reply to a conversation WE initiated (call_nostr)? If so,
        # resolve the waiting Future and stop — we already authorised this peer
        # by contacting it, so this path intentionally bypasses the inbound
        # allowlist gate. resolve() still checks the sender matches our request.
        if corr_id and resolve(corr_id, sender_hex, plaintext):
            log.debug("Correlated reply from %s (corr=%s)", sender.to_bech32(), corr_id)
            return

        # Trust is decided locally (no network). Compute it FIRST so a flood of
        # untrusted spam can never throttle our own trusted/allowlisted agents.
        trusted = _inbound_trusted(sender_hex, self._allowed)

        if not trusted:
            # Rate-limit untrusted peers before any reply — this also covers
            # public-card answers, so an unknown peer can't turn us into a
            # reflector. (Correlated replies bypassed this above; trusted peers
            # are exempt so spam can't deny them service.)
            if not inbound_allowed(sender_hex):
                log.debug("Rate-limited %s — dropped", sender.to_bech32())
                return

            # An untrusted peer asking for something private gets SILENCE, not a
            # refusal — replying would reflect a free message off us and confirm
            # we are online. Public 'hello'/'introduce' is still answered below.
            if not is_public_request(plaintext):
                log.info("Untrusted private request from %s — ignored", sender.to_bech32())
                return

        # Record only peers we actually engage with as trusted — don't let
        # anonymous public-card pings flood the address book.
        if trusted:
            record_interaction(
                sender_hex, transport="nostr", direction="inbound",
                npub=sender.to_bech32(),
            )

        log.info("DM from %s (trusted=%s): %r", sender.to_bech32(), trusted, plaintext[:120])
        response = await dispatch_message(plaintext, sender_hex, trusted=trusted)
        log.debug("Replying: %r", response[:120])

        # Echo the request's correlation tag so the initiator can pair our reply
        # with its pending call (no-op for peers that didn't send one).
        reply_tags = [Tag.parse([CORR_TAG, corr_id])] if corr_id else []
        try:
            await self._client.send_private_msg(sender, response, reply_tags)
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
        log.info("Nostr private-tool allowlist: %d pubkey(s)", len(allowed))
    elif os.getenv("VOKTER_NOSTR_TRUST_ALL") == "1":
        log.warning(
            "VOKTER_NOSTR_TRUST_ALL=1 — every Nostr sender is granted private "
            "tools. Only safe on a private relay."
        )
    else:
        log.info(
            "No allowlist — unknown peers get the public card only; private "
            "tools require an allowlisted or 'trusted' peer."
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
