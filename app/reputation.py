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
import time
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
from ratelimit import SlidingWindow, _int_env

log = logging.getLogger("vokter.reputation")

_NAMESPACE = "vokter.reputation"
# Labels Vokter will attest. Negative and positive; 'neutral' is never worth
# broadcasting.
ATTESTATION_LABELS = ("trusted", "blocked", "spam")


def _anchors() -> set[str]:
    """Hex pubkeys configured as trust anchors (e.g. AIRadar).

    An anchor's attestations weigh as if the human rated it 'trusted', WITHOUT
    writing to the local trust DB — so anchors never become weighting authors in
    a way that could cascade. Opt-in and human-controlled: if empty there is no
    anchor, so this never reintroduces a mandatory central authority.
    """
    out: set[str] = set()
    for item in (x.strip() for x in os.getenv("VOKTER_TRUST_ANCHORS", "").split(",") if x.strip()):
        try:
            out.add(PublicKey.parse(item).to_hex())
        except Exception:
            log.warning("Ignoring invalid VOKTER_TRUST_ANCHORS entry: %r", item)
    return out


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

    anchors = _anchors()
    trusted_says: dict[str, int] = {}
    others_say:   dict[str, int] = {}
    for author, a in latest.items():
        # Count only recognised labels — an author can't pollute the aggregation
        # (or inject an arbitrary string as a bucket key) with a made-up label.
        if a["label"] not in ATTESTATION_LABELS:
            continue
        # A weighting author is one the human trusts OR a configured anchor.
        weighs = get_trust(author) == "trusted" or author in anchors
        bucket = trusted_says if weighs else others_say
        bucket[a["label"]] = bucket.get(a["label"], 0) + 1

    return {
        "target": target_hex,
        "trusted_says": trusted_says,   # from agents you trust — the real signal
        "others_say": others_say,       # everyone else — Sybil-prone, informational
        "attestations": list(latest.values()),
    }


# ── Vouching: web-of-trust elevation for an otherwise-untrusted peer ──────────
# Kept modest so a revocation (an anchor later publishing 'spam') takes effect
# within the window rather than lingering for the whole positive TTL.
_VOUCH_TTL      = float(_int_env("VOKTER_VOUCH_TTL", 300))
# Global ceiling on relay lookups — a pubkey-rotating spammer hitting private
# verbs must not turn each message into a relay fan-out.
_vouch_lookups  = SlidingWindow(_int_env("VOKTER_VOUCH_LOOKUPS", 30), 60)
# target hex → (verdict, expiry_monotonic). Only REAL fetched verdicts are cached;
# a "couldn't check" (rate-limited / relay error) is never cached, so it retries.
_vouch_cache: dict[str, tuple[bool, float]] = {}
_vouch_last_sweep = 0.0


def _sweep_vouch_cache(now: float) -> None:
    # Bound memory under a rotating-pubkey flood; runs at most once per TTL.
    global _vouch_last_sweep
    if now - _vouch_last_sweep < _VOUCH_TTL:
        return
    for k in [k for k, (_, exp) in _vouch_cache.items() if exp <= now]:
        del _vouch_cache[k]
    _vouch_last_sweep = now


async def is_vouched(target_hex: str) -> bool:
    """True if a weighting author (human-'trusted' or a configured anchor) vouches
    'trusted' for target_hex and none label it 'blocked'/'spam'.

    Cache BEFORE budget so cached hits cost nothing; spend a lookup slot only on a
    miss; fail closed (return False, do NOT cache) when the budget is exhausted or
    the relay fetch errors — that's "couldn't check", which must stay retryable.
    A negative label only withholds elevation; it never auto-blocks.
    """
    if not _is_hex_pubkey(target_hex):
        return False

    now = time.monotonic()
    _sweep_vouch_cache(now)
    cached = _vouch_cache.get(target_hex)
    if cached is not None and cached[1] > now:
        return cached[0]

    if not _vouch_lookups.allow("*"):
        log.debug("Vouch lookup budget exhausted — not elevating %s", target_hex[:12])
        return False
    try:
        rep = await reputation_of(target_hex)
    except Exception:
        log.debug("Vouch lookup failed for %s — not elevating", target_hex[:12])
        return False

    ts = rep.get("trusted_says", {})
    verdict = ts.get("trusted", 0) > 0 and ts.get("blocked", 0) == 0 and ts.get("spam", 0) == 0
    _vouch_cache[target_hex] = (verdict, now + _VOUCH_TTL)
    return verdict


# ── Reliability: an OUTBOUND signal from trust anchors (e.g. AIRadar) ─────────
# Distinct from is_vouched/reputation_of (the inbound, NIP-32 trust layer). These
# are AIRadar-style reliability claims about a *provider* ("good uptime"), used
# when Vokter decides which provider to USE/contact. They deliberately grant NO
# inbound access — "reliable endpoint" is not "may read my human's private data".
RELIABILITY_KIND = 30421                 # parameterized-replaceable: one live event per provider
RELIABILITY_NS   = "airadar.reliability"
RELIABILITY_LABEL = "reliable"


async def reliability_of(target_hex: str) -> dict:
    """What configured trust anchors attest about a provider's reliability.

    Reads the anchors' kind-30421 events about target_hex, drops anything whose
    `expiry` tag has passed (a provider AIRadar stopped vouching for lets its
    event expire — that is the revocation), and returns the surviving claims.
    Anchors are opt-in (VOKTER_TRUST_ANCHORS); with none configured there is
    nothing to consult.
    """
    try:
        target = PublicKey.parse(target_hex)        # accept npub or hex
    except Exception as exc:
        raise ValueError(f"invalid target pubkey: {exc}") from exc
    target_hex = target.to_hex()

    anchors = _anchors()
    relays  = _relays()
    if not anchors or not relays:
        return {"target": target_hex, "reliable": False, "claims": []}

    keys   = Keys(secret_key=SecretKey.from_bytes(get_nostr_privkey()))
    client = Client(NostrSigner.keys(keys))
    try:
        for relay in relays:
            await client.add_relay(RelayUrl.parse(relay))
        await client.connect()

        f = (Filter().kind(Kind(RELIABILITY_KIND))
             .authors([PublicKey.parse(a) for a in anchors])
             .pubkey(target))
        events = await client.fetch_events(f, timedelta(seconds=8))

        now    = int(time.time())
        claims = []
        for ev in events.to_vec():
            if not ev.verify():                      # never trust an unsigned claim
                continue
            tags = {v[0]: v for v in (t.as_vec() for t in ev.tags().to_vec()) if v}
            L, l = tags.get("L"), tags.get("l")
            if not (L and len(L) >= 2 and L[1] == RELIABILITY_NS):
                continue
            if not (l and len(l) >= 2 and l[1] == RELIABILITY_LABEL):
                continue
            exp = tags.get("expiry")
            if exp and len(exp) >= 2 and exp[1].isdigit() and int(exp[1]) < now:
                continue                             # expired = revoked

            def _int(tag):
                v = tags.get(tag)
                return int(v[1]) if v and len(v) >= 2 and v[1].lstrip("-").isdigit() else None

            claims.append({
                "author":     ev.author().to_hex(),
                "score":      _int("score"),
                "uptime_pct": _int("uptime"),
                "expiry":     _int("expiry"),
            })
        return {"target": target_hex, "reliable": bool(claims), "claims": claims}
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass
