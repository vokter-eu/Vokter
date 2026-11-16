"""Tier-3 Phase 2B — web.pay.unattended: autonomous payments WITHIN A BUDGET, the SAFE (non-custodial) way.

OPT-IN, DEFAULT OFF, its OWN gate (TIER-7). This is the ONE path where an autonomous caller can move money
with no per-payment human confirm — authorized by the human's UPFRONT mandate (the envelope), not by the
model. See TIER3_PHASE2B_NWC_UNATTENDED.md. The QR per-payment path (web_pay.py) stays the default and is
untouched by this module.

THREE AND-conditions before a sat moves (any one OFF → fall back to the QR/confirm path):
  1. the TIER-7 gate is green on this install (unattended_gate_ok()),
  2. the user opted in — a wallet is connected + a budget envelope is set (NWCStore.has(alias)),
  3. THIS spend is inside the envelope (web_pay_nwc.envelope_check): Mode A — verifiable Lightning Address
     ON the allow-list ∧ ≤ per-tx max ∧ within budget ∧ within the window ∧ under the count throttle.

The envelope is enforced at the GATEWAY: the handler computes the in-envelope decision (pure, from the
stored envelope) and passes it as a MandateGrant target; guard() grants a GENUINE ALLOW only when it is
in-envelope (nonhuman rule allow_if_within_mandate). Out-of-envelope → a real BLOCK, which the handler
turns into a 428 DOWNGRADE (confirm via web.pay) — the spend code is reached ONLY on a true ALLOW, never by
catching a BLOCK. Opaque/on-chain destinations are NEVER eligible here (no domain to allow-list, irreversible).
"""
from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

import web_pay_nwc as nwc
from web_pay import resolve_and_invoice   # reuse the SAME confirm-integrity used by the QR path (code, not model)

router = APIRouter()

_store = nwc.NWCStore()


# ───────────────────────── the gated spend route (the web.pay.unattended action) ─────────────────────────
class UnattendedPayRequest(BaseModel):
    payee: str                 # a Lightning Address (name@domain) — must be on the user's allow-list.
    amount_sats: int           # the amount to pay, in satoshis.
    alias: str = "default"     # which connected wallet to spend from.
    confirm: bool = False       # only meaningful for a HUMAN caller (a human present confirms explicitly).


def _downgrade(reason: str) -> HTTPException:
    """Outside the envelope (or not opted-in) → ask the human to confirm via the normal web.pay path.
    NEVER a silent send; the caller retries on /api/web/pay with a human confirm."""
    return HTTPException(428, detail={"error": "confirmation_required", "action": "web.pay",
                                      "downgrade": True, "reason": reason})


@router.post("/api/web/pay/unattended")
def web_pay_unattended(req: UnattendedPayRequest, x_vokter_human_session: str | None = Header(default=None)):
    """Pay WITHIN the user's pre-set budget, WITHOUT a per-payment confirm — iff gated ∧ opted-in ∧
    in-envelope. The user's OWN wallet holds funds+key and enforces the cap (NIP-47); Vokter holds only a
    budget-limited, revocable connection secret (never funds, never the key). Returns STATUS ONLY — never
    the connection secret or a preimage."""
    from chat import is_local_human_session
    from safety import HUMAN, MandateGrant, enforce_http

    is_human = is_local_human_session(x_vokter_human_session)
    # Over this LOCAL route, a non-human caller is the local planner/scheduler ("autonomous"); peers (A2A/
    # Nostr) and MCP enter via agent_routes / the MCP server, NOT this route — and the gateway rule denies
    # them regardless (allow_if_within_mandate is fail-closed for any non-MandateGrant target).
    context = HUMAN if is_human else "autonomous"

    # (1) the TIER-7 gate — fully dormant until the fire-test is green on this install.
    if not nwc.unattended_gate_ok():
        raise HTTPException(503, "hands-off payments unavailable: the TIER-7 self-test has not passed on this install")

    # (2) opted-in? No connection → there is nothing to authorize → downgrade to a human confirm.
    if not _store.has(req.alias):
        raise _downgrade("hands-off is not set up — confirm this payment")

    # (3) in-envelope? PURE check against the stored envelope; the result drives a GENUINE gateway verdict.
    ok, reason = nwc.envelope_check(_store, req.alias, req.payee, req.amount_sats)
    target = MandateGrant(f"{req.amount_sats} sats to {req.payee} (unattended)", within=ok)
    try:
        # The envelope bounds EVERY caller of this endpoint — autonomous AND human. A human's `confirm` is
        # honored ONLY when the spend is ALSO in-envelope (`and ok`); a human who wants to pay OUTSIDE the
        # envelope is routed to web.pay (QR, per-payment confirm), NEVER an unbounded spend over the stored
        # NWC connection. The spend code below runs ONLY if this returns without raising — a BLOCK is never
        # caught-and-ignored (it becomes a downgrade, not a payment).
        enforce_http("web.pay.unattended", target, context=context, confirmed=(is_human and req.confirm and ok))
    except HTTPException:
        if is_human and ok:
            raise                                  # in-envelope human, just needs to confirm → 428 surfaces
        raise _downgrade(reason or "outside your hands-off budget — confirm this payment")

    # ── CONFIRM-INTEGRITY (code, never the model): re-resolve the allow-listed payee, fetch the invoice,
    #    and prove the amount + LUD-06 binding (same resolve_and_invoice the QR path uses). A substituted
    #    destination can't pass — the payee is pinned to the human's allow-list, resolved over TLS here. ──
    try:
        res = resolve_and_invoice(req.payee, req.amount_sats)
    except ValueError as e:
        raise HTTPException(422, str(e))           # mismatch → rejected, nothing sent

    # Pay over the user's OWN wallet (it signs + enforces its budget). Vokter holds no key/funds and gets
    # back STATUS ONLY — never the connection secret or a preimage.
    paid, status = nwc.pay_via_nwc(_store, req.alias, res["bolt11"])
    if not paid:
        raise HTTPException(502, f"wallet declined or unreachable ({status})")
    nwc._record_spend(req.alias, req.amount_sats)
    return {"status": "paid", "payee_domain": res["domain"], "amount_sats": req.amount_sats,
            "alias": req.alias, "unattended": True}


# ───────────────────────── opt-in provisioning (HUMAN-only — the deliberate enable/disable) ─────────────────────────
class UnattendedConnectRequest(BaseModel):
    uri: str                        # the NWC connection URI (carries the connection secret) — keychain only.
    alias: str = "default"
    allow_list: list[str] = []      # Mode A: the verifiable Lightning Addresses hands-off may pay.
    per_tx_max_sats: int = 0        # 0 → conservative default (web_pay_nwc.DEFAULTS).
    budget_sats: int = 0
    window_sec: int = 0
    throttle_n: int = 0


def _require_human(x_vokter_human_session: str | None) -> None:
    from chat import is_local_human_session
    if not is_local_human_session(x_vokter_human_session):
        raise HTTPException(403, "provisioning a hands-off wallet is owner-only (local human session)")


@router.post("/api/web/pay/unattended/connect")
def unattended_connect(req: UnattendedConnectRequest, x_vokter_human_session: str | None = Header(default=None)):
    """Opt in: store a budget-limited connection + envelope. HUMAN-only. The URI (secret) goes to the
    keychain and is NEVER echoed back; the response carries the NON-SECRET envelope + the honest caveat."""
    _require_human(x_vokter_human_session)
    if not nwc.validate_nwc_uri(req.uri):
        raise HTTPException(400, "not a valid NWC connection URI (expected nostr+walletconnect://…)")
    env = nwc._new_envelope(req.allow_list, req.per_tx_max_sats, req.budget_sats, req.window_sec, req.throttle_n)
    if not env["allow_list"]:
        raise HTTPException(400, "add at least one Lightning Address to the hands-off allow-list (Mode A)")
    _store.put(req.alias, req.uri, env)
    return {"status": "connected", "alias": req.alias, "envelope": env, "caveat": nwc.CONNECT_CAVEAT}


class UnattendedRevokeRequest(BaseModel):
    alias: str = "default"


@router.post("/api/web/pay/unattended/revoke")
def unattended_revoke(req: UnattendedRevokeRequest, x_vokter_human_session: str | None = Header(default=None)):
    """One-action revoke of Vokter's authority. HUMAN-only. NOTE (surfaced in the UI): this does NOT recall
    an already-exfiltrated copy — revoke IN YOUR WALLET too to kill a leaked secret."""
    _require_human(x_vokter_human_session)
    ok = _store.delete(req.alias)
    return {"status": "revoked" if ok else "not_found", "alias": req.alias,
            "also_revoke_in_wallet": True, "caveat": nwc.CONNECT_CAVEAT}


@router.get("/api/web/pay/unattended/list")
def unattended_list(x_vokter_human_session: str | None = Header(default=None)):
    """The hands-off menu — alias + NON-SECRET envelope only (never the URI/secret). HUMAN-only."""
    _require_human(x_vokter_human_session)
    return {"connections": _store.list_public(), "caveat": nwc.CONNECT_CAVEAT}
