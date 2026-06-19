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
from datetime import timedelta

from nostr_sdk import (
    Client,
    EventBuilder,
    Filter,
    Keys,
    Kind,
    NostrSigner,
    PublicKey,
    RelayUrl,
    SecretKey,
    Tag,
)

from identity import get_nostr_privkey
from known_agents import get_trust

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


async def fetch_attestations(target_hex: str, *, timeout_secs: int = 8) -> list[dict]:
    """Fetch NIP-32 reputation labels about target_hex from the relays.

    Only signature-verified events in our namespace are returned. Each item:
    {author (hex), label, note, created_at}.
    """
    if not _is_hex_pubkey(target_hex):
        raise ValueError("target must be a 64-char hex Nostr pubkey")
    relays = _relays()
    if not relays:
        return []

    keys   = Keys(secret_key=SecretKey.from_bytes(get_nostr_privkey()))
    client = Client(NostrSigner.keys(keys))
    try:
        for relay in relays:
            await client.add_relay(RelayUrl.parse(relay))
        await client.connect()

        f = Filter().kind(Kind(1985)).pubkey(PublicKey.parse(target_hex))
        events = await client.fetch_events(f, timedelta(seconds=timeout_secs))

        out: list[dict] = []
        for ev in events.to_vec():
            if not ev.verify():                       # never trust an unsigned claim
                continue
            label, ns_ok = None, False
            for tg in (t.as_vec() for t in ev.tags().to_vec()):
                if len(tg) >= 2 and tg[0] == "L" and tg[1] == _NAMESPACE:
                    ns_ok = True
                elif len(tg) >= 2 and tg[0] == "l" and (len(tg) < 3 or tg[2] == _NAMESPACE):
                    label = tg[1]
            if ns_ok and label:
                out.append({
                    "author": ev.author().to_hex(),
                    "label": label,
                    "note": ev.content(),
                    "created_at": ev.created_at().as_secs(),
                })
        return out
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass


async def reputation_of(target_hex: str) -> dict:
    """Aggregate attestations about a peer into a web-of-trust signal.

    Sybil defence: anyone can publish labels from throwaway keys, so raw counts
    are meaningless. We split what AGENTS THE HUMAN TRUSTS say (weight these)
    from what everyone else says (informational only), keeping each author's
    latest label.
    """
    latest: dict[str, dict] = {}
    for a in await fetch_attestations(target_hex):
        cur = latest.get(a["author"])
        if cur is None or a["created_at"] > cur["created_at"]:
            latest[a["author"]] = a

    trusted_says: dict[str, int] = {}
    others_say:   dict[str, int] = {}
    for author, a in latest.items():
        # Count only recognised labels — an author can't pollute the aggregation
        # (or inject an arbitrary string as a bucket key) with a made-up label.
        if a["label"] not in ATTESTATION_LABELS:
            continue
        bucket = trusted_says if get_trust(author) == "trusted" else others_say
        bucket[a["label"]] = bucket.get(a["label"], 0) + 1

    return {
        "target": target_hex,
        "trusted_says": trusted_says,   # from agents you trust — the real signal
        "others_say": others_say,       # everyone else — Sybil-prone, informational
        "attestations": list(latest.values()),
    }
