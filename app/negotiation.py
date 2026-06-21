"""
Agent-to-agent negotiation — Vokter as an economic actor (rule-based v1).

Vokter plays two roles:
  * SELLER (responder): the human configures a listing (item, opening price, and
    a secret floor). When a peer haggles, Vokter concedes from the opening toward
    — but never disclosing — the floor, and never sells below it.
  * BUYER (initiator): drives rounds against a peer, raising its bid toward a
    budget, and surfaces the converged deal for the HUMAN to approve. v1 stops
    before money moves — reaching agreement is the deliverable, settlement is not.

Security / correctness (mirrors the patterns shipped this session):
  * Session state is bound to the AUTHENTICATED peer, not the peer-chosen
    session_id alone — only the peer that opened a session may advance it
    (cf. nostr_outbound.resolve binding to sender).
  * Offers carry `valid_until`. An `accept` is a binding deal ONLY if it echoes
    the seller's last live offer exactly AND is unexpired — otherwise the seller
    replies `requote`. This survives human-confirmation latency on the buyer side.
  * Sessions are swept on a TTL and capped per peer (cf. ratelimit / vouch cache),
    so abandoned negotiations can't leak memory.
  * The floor is never serialised. Concessions step down from the OPENING with
    jitter (not midpoint-to-floor, which makes the floor computable in 2–3 rounds).
    NOTE: even so, a determined untrusted buyer can estimate the floor from the
    concession shape — negotiation is a TRUSTED verb, so v1 only haggles between
    peers that already trust each other. Opening the seller path to unknown
    merchant agents is future work.
"""
import json
import logging
import random
import time
import uuid
from contextlib import closing
from dataclasses import dataclass

from db import get_db

log = logging.getLogger("vokter.negotiate")

OFFER_TTL       = 300.0   # seconds an offer stays valid (covers human approval)
SESSION_TTL     = 1800.0  # abandoned sessions are swept after this
MAX_OPEN_PER_PEER = 5
STEP_FRAC       = 0.15    # base concession per round, as a fraction of opening
JITTER          = 0.25    # ± this fraction on each step, so steps aren't predictable


@dataclass
class _Session:
    peer:        str       # authenticated peer — only this peer may advance
    item:        str
    floor:       int       # secret reserve — never serialised
    opening:     int
    last_offer:  int       # seller's current standing offer
    valid_until: float
    rounds:      int
    max_rounds:  int
    status:      str        # 'open' | 'agreed' | 'rejected'
    created:     float


_sessions: dict[str, _Session] = {}
_last_sweep = 0.0


def _sweep(now: float) -> None:
    global _last_sweep
    if now - _last_sweep < SESSION_TTL:
        return
    for sid in [s for s, v in _sessions.items()
                if v.status != "open" or now - v.created > SESSION_TTL]:
        del _sessions[sid]
    _last_sweep = now


def _open_for_peer(peer: str) -> int:
    return sum(1 for v in _sessions.values() if v.peer == peer and v.status == "open")


def _listing(item: str) -> dict | None:
    with closing(get_db()) as db:
        row = db.execute(
            "SELECT item, opening, floor, max_rounds, unit FROM negotiation_listings WHERE item = ?",
            (item,),
        ).fetchone()
    if not row:
        return None
    return {"item": row[0], "opening": row[1], "floor": row[2],
            "max_rounds": row[3], "unit": row[4]}


def _concession_step(opening: int) -> int:
    factor = STEP_FRAC * (1 + random.uniform(-JITTER, JITTER))
    return max(1, round(opening * factor))


def _offer_msg(sid: str, s: _Session) -> str:
    return json.dumps({
        "action": "offer", "session_id": sid, "item": s.item,
        "amount": s.last_offer, "valid_until": int(s.valid_until),
    })


def handle_inbound(peer: str, args: dict) -> str:
    """Seller-side state machine. Returns a JSON message for the peer.

    peer  — the authenticated caller (Nostr sender pubkey / A2A context). The
            binding that stops a third party advancing someone else's session.
    args  — {action, session_id, item, amount}.
    """
    now = time.time()
    _sweep(now)

    action = str(args.get("action", "")).lower()
    sid    = str(args.get("session_id") or "")

    if action == "quote":
        item    = str(args.get("item") or "")
        listing = _listing(item)
        if not listing:
            return json.dumps({"action": "reject", "reason": "not for sale", "item": item})
        if not sid:
            return json.dumps({"action": "reject", "reason": "missing session_id"})
        if sid in _sessions:
            return json.dumps({"action": "reject", "reason": "session_id in use"})
        if _open_for_peer(peer) >= MAX_OPEN_PER_PEER:
            return json.dumps({"action": "reject", "reason": "too many open negotiations"})

        s = _Session(
            peer=peer, item=item, floor=listing["floor"], opening=listing["opening"],
            last_offer=listing["opening"], valid_until=now + OFFER_TTL, rounds=0,
            max_rounds=listing["max_rounds"], status="open", created=now,
        )
        _sessions[sid] = s
        log.info("Negotiation %s opened by %s for %r @ %d", sid[:8], peer[:12], item, s.opening)
        return _offer_msg(sid, s)

    # Every other action advances an existing session — peer-bound.
    s = _sessions.get(sid)
    if s is None or s.peer != peer:
        return json.dumps({"action": "reject", "reason": "unknown session"})
    if s.status != "open":
        return json.dumps({"action": "reject", "reason": f"session {s.status}"})

    if action == "accept":
        amount = args.get("amount")
        # Binding only if it echoes our live offer exactly and hasn't expired.
        if amount != s.last_offer or now >= s.valid_until:
            s.last_offer  = s.last_offer            # unchanged; re-quote with fresh validity
            s.valid_until = now + OFFER_TTL
            return json.dumps({"action": "requote", "reason": "offer changed or expired",
                               "session_id": sid, "item": s.item,
                               "amount": s.last_offer, "valid_until": int(s.valid_until)})
        s.status = "agreed"
        log.info("Negotiation %s AGREED with %s: %r @ %d", sid[:8], peer[:12], s.item, amount)
        return json.dumps({"action": "accepted", "session_id": sid, "item": s.item, "amount": amount})

    if action == "counter":
        bid = args.get("amount")
        if not isinstance(bid, int):
            return json.dumps({"action": "reject", "reason": "counter needs an integer amount"})
        s.rounds += 1

        # Buyer met or beat our standing offer → deal at our offer.
        if bid >= s.last_offer:
            s.status = "agreed"
            log.info("Negotiation %s AGREED with %s: %r @ %d", sid[:8], peer[:12], s.item, s.last_offer)
            return json.dumps({"action": "accepted", "session_id": sid, "item": s.item, "amount": s.last_offer})

        # Out of rounds: take a profitable bid, else walk.
        if s.rounds >= s.max_rounds:
            if bid >= s.floor:
                s.status = "agreed"
                return json.dumps({"action": "accepted", "session_id": sid, "item": s.item, "amount": bid})
            s.status = "rejected"
            return json.dumps({"action": "reject", "reason": "no agreement", "session_id": sid})

        # Concede a step from the standing offer, never below the floor.
        new_offer = max(s.floor, s.last_offer - _concession_step(s.opening))
        # If conceding lands at/under the buyer's bid, just take the bid.
        if new_offer <= bid:
            s.status = "agreed"
            return json.dumps({"action": "accepted", "session_id": sid, "item": s.item, "amount": bid})
        s.last_offer  = new_offer
        s.valid_until = now + OFFER_TTL
        return _offer_msg(sid, s)

    return json.dumps({"action": "reject", "reason": f"unknown action {action!r}"})


# ── Buyer side (thin driver) ─────────────────────────────────────────────────

async def _send(target: str, payload: dict, *, token: str | None) -> dict:
    """Send one negotiation message to a peer and parse its JSON reply."""
    text = json.dumps({"tool": "negotiate", "args": payload})
    target = target.strip()
    if target.startswith("nostr:"):
        from nostr_outbound import call_nostr
        reply = await call_nostr(target, text)
    else:
        from agent_client import call_a2a
        reply = await call_a2a(target, text, token=token)
    try:
        return json.loads(reply)
    except (json.JSONDecodeError, TypeError):
        return {"action": "error", "reason": "peer reply was not negotiation JSON", "raw": reply}


async def negotiate_with(
    target: str, item: str, max_budget: int, *,
    open_bid: int | None = None, max_rounds: int = 4, token: str | None = None,
) -> dict:
    """Drive a negotiation as the buyer. Raises offers toward max_budget and
    returns the converged deal WITHOUT accepting — the human approves first.

    Returns {status: 'deal'|'no_deal'|'error', ...}. On 'deal', the caller sends
    a separate, human-confirmed accept_offer().
    """
    session_id = uuid.uuid4().hex
    msg = await _send(target, {"action": "quote", "session_id": session_id, "item": item}, token=token)
    if msg.get("action") != "offer":
        return {"status": "no_deal", "reason": msg.get("reason", "no opening offer"), "peer_said": msg}

    bid = open_bid if isinstance(open_bid, int) else max(1, max_budget // 2)
    for _ in range(max_rounds):
        amount = msg.get("amount")
        if isinstance(amount, int) and amount <= max_budget:
            return {"status": "deal", "session_id": session_id, "item": item,
                    "amount": amount, "valid_until": msg.get("valid_until"), "target": target}
        # Raise our bid toward the budget without exceeding it.
        bid = min(max_budget, bid + max(1, (max_budget - bid) // 2))
        msg = await _send(target, {"action": "counter", "session_id": session_id,
                                   "item": item, "amount": bid}, token=token)
        act = msg.get("action")
        if act == "accepted":
            return {"status": "deal", "session_id": session_id, "item": item,
                    "amount": msg.get("amount"), "valid_until": None, "target": target,
                    "already_agreed": True}
        if act in ("reject", "error"):
            return {"status": "no_deal", "reason": msg.get("reason", "rejected"), "peer_said": msg}

    return {"status": "no_deal", "reason": "no agreement within budget", "peer_said": msg}


async def accept_offer(target: str, session_id: str, amount: int, *, token: str | None = None) -> dict:
    """Send the human-confirmed acceptance of a converged deal."""
    return await _send(target, {"action": "accept", "session_id": session_id, "amount": amount}, token=token)
