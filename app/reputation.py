"""
Portable reputation — signed Nostr attestations (NIP-32 labels).

Vokter can publish a signed, public claim about another agent ("I vouch for X",
"X is a spammer"). Because it is signed by Vokter's stable Nostr identity and
gossiped to relays, anyone can later weigh it in a web of trust — reputation
becomes a graph of signed claims, not a central platform score. This is the
portable layer above the local block/trust list in known_agents.

PRIVACY: publishing an attestation is PUBLIC and reveals the human's judgement
of a peer. It is therefore strictly OPT-IN and explicit — rating an agent
locally (set_trust) never broadcasts. Only an explicit call here publishes.

Format — NIP-32 (kind 1985):
  L tag = namespace "vokter.reputation"
  l tag = the label + namespace
  p tag = the agent being labelled (hex pubkey)
  content = optional human-readable note

Fire-and-forget: publishing needs no reply, so (unlike conversational outbound)
there is no correlation problem — a short-lived client connects, sends, leaves.
"""
import logging
import os

from nostr_sdk import (
    Client,
    EventBuilder,
    Keys,
    Kind,
    NostrSigner,
    RelayUrl,
    SecretKey,
    Tag,
)

from identity import get_nostr_privkey

log = logging.getLogger("vokter.reputation")

_NAMESPACE = "vokter.reputation"
# Labels Vokter will attest. Negative and positive; 'neutral' is never worth
# broadcasting.
ATTESTATION_LABELS = ("trusted", "blocked", "spam")


def _relays() -> list[str]:
    raw = os.getenv("VOKTER_NOSTR_RELAYS", "")
    return [r.strip() for r in raw.split(",") if r.strip()]


def _is_hex_pubkey(s: str) -> bool:
    return len(s) == 64 and all(c in "0123456789abcdef" for c in s.lower())


async def publish_attestation(target_hex: str, label: str, note: str = "") -> str | None:
    """Publish a signed NIP-32 reputation label about target_hex.

    Returns the event id (hex) on success, or None if no relays are configured.
    Raises ValueError on an invalid target or label.
    """
    if not _is_hex_pubkey(target_hex):
        raise ValueError("target must be a 64-char hex Nostr pubkey")
    if label not in ATTESTATION_LABELS:
        raise ValueError(f"label must be one of: {', '.join(ATTESTATION_LABELS)}")

    relays = _relays()
    if not relays:
        log.info("No VOKTER_NOSTR_RELAYS — attestation not published")
        return None

    keys   = Keys(secret_key=SecretKey.from_bytes(get_nostr_privkey()))
    signer = NostrSigner.keys(keys)
    client = Client(signer)
    try:
        for relay in relays:
            await client.add_relay(RelayUrl.parse(relay))
        await client.connect()

        tags = [
            Tag.parse(["L", _NAMESPACE]),
            Tag.parse(["l", label, _NAMESPACE]),
            Tag.parse(["p", target_hex]),
        ]
        event = await EventBuilder(Kind(1985), note).tags(tags).sign(signer)
        await client.send_event(event)
        log.info("Published '%s' attestation about %s", label, target_hex[:12])
        return event.id().to_hex()
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass
