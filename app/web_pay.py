"""Tier-3 Phase 2 — web.pay: pay WITHOUT Vokter holding funds or keys (crypto-first, LNURL slice).

Sovereign-maximal: Vokter CONSTRUCTS a Lightning payment request from a human-verifiable payee (a
Lightning Address / LNURL-pay) and HANDS OFF to the user's OWN wallet (a `lightning:` deep-link). The
user confirms + signs IN THEIR wallet. Vokter stores NOTHING, holds no funds, holds no keys.

The threat model INVERTS vs login (TIER3_PHASE2_PAYMENTS.md §1): there is no outgoing secret (the invoice
is public). The attack is DESTINATION SUBSTITUTION — a prompt-injected model or hostile callback swaps in
the attacker's invoice. So the core guard is CONFIRM-INTEGRITY, enforced here by CODE, never the model:
  * Vokter NEVER accepts a raw invoice from the request — it ALWAYS resolves the LNURL from the payee's
    DOMAIN for the confirmed amount (the request carries only {payee, amount_sats}, no invoice).
  * The fetched invoice's AMOUNT must equal the confirmed amount (decoded from the BOLT11), and its
    description_hash must equal sha256(the LNURL metadata) (LUD-06) — a substituted/inflated invoice is
    REJECTED and never handed off.
Origin-binding: the payee is a Lightning Address whose DOMAIN is human-verifiable and shown in the confirm
sheet (the user typed it, never the model). Opaque destinations (raw BOLT11 / on-chain) are a later slice,
shipped only with an explicit "destination not verifiable" caveat.

Gateway: `guard("web.pay")` CONFIRM-human-only (BLOCK peer/mcp/autonomous). Per-tx/window/payee ceilings
are a SECOND limit (the real control is the human confirm + the user's wallet). GATED by the same ship
gate as login — GateNotPassed → 503 until the TIER-6 fire-test is green on the frozen .deb.
"""
from __future__ import annotations

import hashlib
import os
import re
import time

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from oauth_broker import GateNotPassed, ship_gate_ok   # the ONE ship gate, shared with login

router = APIRouter()

MSAT_PER_SAT = 1000


# ───────────────────────── bech32 + BOLT11 decode (for confirm-integrity) ─────────────────────────
_B32 = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
_B32_INV = {c: i for i, c in enumerate(_B32)}


def _bech32_decode(s: str) -> tuple[str, list[int]]:
    """(hrp, 5-bit data without the 6-group checksum). No checksum verify — a wallet does that; we only
    extract the amount + tagged fields to prove integrity against what the human confirmed."""
    s = s.strip().lower()
    pos = s.rfind("1")
    if pos < 1 or pos + 7 > len(s):
        raise ValueError("not a bech32 string")
    hrp = s[:pos]
    try:
        data = [_B32_INV[c] for c in s[pos + 1:]]
    except KeyError:
        raise ValueError("invalid bech32 char")
    return hrp, data[:-6]


def _5bit_to_bytes(vals: list[int]) -> bytes:
    acc = bits = 0
    out = bytearray()
    for x in vals:
        acc = (acc << 5) | x
        bits += 5
        if bits >= 8:
            bits -= 8
            out.append((acc >> bits) & 0xFF)
    return bytes(out)


def _hrp_amount_msat(hrp: str) -> int | None:
    """Amount from the BOLT11 human-readable part (lnbc<num><multiplier>). None = amountless invoice."""
    m = re.match(r"^ln(?:bc|tb|bcrt|sb)(\d+)?([munp])?$", hrp)
    if not m or m.group(1) is None:
        return None
    num = int(m.group(1)); mult = m.group(2)
    if mult is None:  return num * 10**11      # whole BTC
    if mult == "m":   return num * 10**8        # milli
    if mult == "u":   return num * 10**5        # micro
    if mult == "n":   return num * 10**2        # nano
    if mult == "p":   return num // 10          # pico (num must be a multiple of 10)
    return None


def decode_bolt11(invoice: str) -> dict:
    """Extract {amount_msat, description_hash, payment_hash} from a BOLT11 invoice. Enough to prove
    confirm-integrity (amount + LUD-06 metadata binding); the wallet does full validation + signing."""
    hrp, data = _bech32_decode(invoice)
    amount_msat = _hrp_amount_msat(hrp)
    # data = [7-group timestamp] + tagged fields (type[1] + len[2] + value[len]) + signature. Walk fields.
    fields: dict[int, list[int]] = {}
    i = 7
    while i + 3 <= len(data):
        t = data[i]; ln = data[i + 1] * 32 + data[i + 2]; i += 3
        if i + ln > len(data):
            break
        fields.setdefault(t, data[i:i + ln]); i += ln
    desc_hash = _5bit_to_bytes(fields[23]).hex() if 23 in fields else None   # tag 'h'
    pay_hash = _5bit_to_bytes(fields[1]).hex() if 1 in fields else None       # tag 'p'
    return {"amount_msat": amount_msat, "description_hash": desc_hash, "payment_hash": pay_hash}


# ───────────────────────── Lightning Address / LNURL-pay (payee resolution) ─────────────────────────
_LN_ADDR = re.compile(r"^([a-z0-9._%+\-]+)@([a-z0-9.\-]+\.[a-z]{2,})$", re.I)


def parse_lightning_address(addr: str) -> tuple[str, str] | None:
    """('name','domain') for a valid Lightning Address, else None. The DOMAIN is the human-verifiable
    payee shown in the confirm sheet — the origin-binding anchor for the crypto path."""
    m = _LN_ADDR.match((addr or "").strip())
    return (m.group(1), m.group(2).lower()) if m else None


def _http_get_json(url: str) -> dict:
    """GET → JSON. Patchable seam so the hermetic TIER-6 fire-test drives the full handler with a mock
    LNURL server (no network). HTTPS enforced (LNURL requires it) except localhost for tests."""
    import json
    import httpx
    if not url.startswith("https://") and not url.startswith("http://127.0.0.1") and not url.startswith("http://localhost"):
        raise ValueError("LNURL endpoints must be https")
    r = httpx.get(url, timeout=20, follow_redirects=True)
    if r.status_code // 100 != 2:
        raise ValueError(f"LNURL endpoint HTTP {r.status_code}")
    return json.loads(r.text)


def resolve_and_invoice(payee: str, amount_sats: int) -> dict:
    """Resolve the Lightning Address → LNURL-pay params → request an invoice for amount_sats, then PROVE
    confirm-integrity on it (amount + LUD-06 metadata hash). Returns {domain, bolt11, amount_sats} on
    success; raises ValueError (rejected) on any mismatch — a substituted/inflated invoice never returns.
    Vokter stores NOTHING; the bolt11 is a public payment request handed to the user's wallet."""
    parsed = parse_lightning_address(payee)
    if not parsed:
        raise ValueError("not a valid Lightning Address (expected name@domain)")
    name, domain = parsed
    params = _http_get_json(f"https://{domain}/.well-known/lnurlp/{name}")
    if params.get("tag") != "payRequest" or "callback" not in params or "metadata" not in params:
        raise ValueError("domain did not return a valid LNURL-pay request")
    amount_msat = amount_sats * MSAT_PER_SAT
    if not (int(params.get("minSendable", 0)) <= amount_msat <= int(params.get("maxSendable", 0))):
        raise ValueError("amount outside the payee's min/max sendable")
    cb = params["callback"]; sep = "&" if "?" in cb else "?"
    inv = _http_get_json(f"{cb}{sep}amount={amount_msat}")
    bolt11 = inv.get("pr")
    if not bolt11:
        raise ValueError("payee callback returned no invoice")
    # ── CONFIRM-INTEGRITY (code, never the model): the invoice must match what the human will confirm ──
    dec = decode_bolt11(bolt11)
    if dec["amount_msat"] != amount_msat:
        raise ValueError("REJECTED: invoice amount does not match the confirmed amount")
    want = hashlib.sha256(params["metadata"].encode()).hexdigest()
    if dec["description_hash"] != want:
        raise ValueError("REJECTED: invoice is not bound to this payee's request (LUD-06 metadata mismatch)")
    return {"domain": domain, "bolt11": bolt11, "amount_sats": amount_sats}


# ───────────────────────── gateway ceilings (second limit; in-memory, env-tunable) ─────────────────────────
def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _max_tx():     return _env_int("VOKTER_PAY_MAX_TX_SATS", 50_000)       # per transaction
def _max_window(): return _env_int("VOKTER_PAY_MAX_WINDOW_SATS", 200_000)  # rolling window total
def _window_sec(): return _env_int("VOKTER_PAY_WINDOW_SEC", 86_400)        # window length (24h)
def _max_payee():  return _env_int("VOKTER_PAY_MAX_PAYEE_SATS", 100_000)   # per-payee total in window

_LEDGER: list[tuple[float, str, int]] = []   # (ts, payee_domain, sats) — spend metadata, NOT a secret


def _ledger_sweep(now: float) -> None:
    cut = now - _window_sec()
    while _LEDGER and _LEDGER[0][0] < cut:
        _LEDGER.pop(0)


def check_ceilings(domain: str, sats: int) -> str | None:
    """Return a human-readable refusal if `sats` to `domain` would breach a ceiling, else None. A SECOND
    limit beside the human confirm + the user's wallet — defense-in-depth, not the primary control."""
    now = time.time(); _ledger_sweep(now)
    if sats <= 0:
        return "amount must be positive"
    if sats > _max_tx():
        return f"exceeds the per-transaction ceiling ({_max_tx()} sats)"
    if sum(s for _, _, s in _LEDGER) + sats > _max_window():
        return f"exceeds the {_window_sec() // 3600}h spend ceiling ({_max_window()} sats)"
    if sum(s for _, d, s in _LEDGER if d == domain) + sats > _max_payee():
        return f"exceeds the per-payee ceiling for {domain} ({_max_payee()} sats)"
    return None


def _ledger_record(domain: str, sats: int) -> None:
    _LEDGER.append((time.time(), domain, sats))


def _nwc_store():
    """The production NWC connection store (keychain). Patchable in the hermetic TIER-6 fire-test."""
    from web_pay_nwc import NWCStore
    return NWCStore()


# ───────────────────────── gateway-choked two-phase route ─────────────────────────
class WebPayRequest(BaseModel):
    payee: str             # a Lightning Address (name@domain) — the human-verifiable destination. Vokter
                           # constructs the payment request itself from this domain, so the model/page
                           # cannot substitute a destination (confirm-integrity).
    amount_sats: int       # the amount to pay, in satoshis.
    via: str = "handoff"   # "handoff" = deep-link to the user's wallet (stores nothing); "nwc" = pay over
                           # the user's budget-limited NWC connection (the capped-session rail).
    nwc_alias: str = "default"   # which NWC connection to use when via="nwc".
    confirm: bool = False  # the human's explicit per-payment approval (MANDATORY — crypto is irreversible).
    # Deliberately no payable field (no raw request, no signing material): nothing payable rides a caller.


@router.post("/api/web/pay")
def web_pay(req: WebPayRequest, x_vokter_human_session: str | None = Header(default=None)):
    """Pay `amount_sats` to a Lightning Address WITHOUT Vokter holding funds or keys. Two phases, stateless:

      Phase 1 (confirm=false) — CONSENT. enforce_http BLOCKs non-human (403) BEFORE anything. Resolve the
        payee's LNURL, request an invoice, PROVE confirm-integrity (amount + LUD-06), check the ceilings,
        and return a 428 enriched with {payee_domain, amount_sats, verifiable} for the confirm sheet.
      Phase 2 (confirm=true) — the human approved THIS payment. Re-resolve + re-verify integrity + re-check
        ceilings, record the spend, and return a `lightning:` HAND-OFF for the user's wallet to sign.
        Vokter stores nothing and signs nothing; the wallet holds the key and the funds.

    A substituted/inflated invoice is REJECTED (503-domain error, never handed off). GateNotPassed → 503
    (the TIER-6 fire-test has not passed on this install). Per-payment human-confirm is mandatory.
    """
    from chat import is_local_human_session
    from safety import HUMAN, enforce_http

    context = HUMAN if is_local_human_session(x_vokter_human_session) else "autonomous"
    target = f"{req.amount_sats} sats to {req.payee}"   # gateway-recorded target — no secret (public payee)

    if not req.confirm:
        try:
            enforce_http("web.pay", target, context=context, confirmed=False)
        except HTTPException as e:
            if e.status_code != 428:
                raise                                   # non-human → 403 propagates; nothing resolved
        if not ship_gate_ok():                          # fully dormant until the TIER-6 fire-test is green
            raise HTTPException(503, "payment unavailable: the containment self-test has not passed on this install")
        parsed = parse_lightning_address(req.payee)
        if parsed is None:
            raise HTTPException(400, "payee must be a Lightning Address (name@domain)")
        breach = check_ceilings(parsed[1], req.amount_sats)
        if breach:
            raise HTTPException(403, f"payment refused: {breach}")
        if req.via == "nwc" and not _nwc_store().has(req.nwc_alias):
            raise HTTPException(404, "no NWC connection — connect your wallet in Settings first")
        try:
            res = resolve_and_invoice(req.payee, req.amount_sats)   # integrity-checked here too
        except ValueError as e:
            raise HTTPException(422, str(e))
        raise HTTPException(428, detail={
            "error": "confirmation_required", "action": "web.pay",
            "via": "nwc" if req.via == "nwc" else "handoff",
            "payee_domain": res["domain"], "amount_sats": req.amount_sats, "verifiable": True})

    # Phase 2 — confirmed. Per-payment human-confirm already happened (the 428 the human approved).
    enforce_http("web.pay", target, context=context, confirmed=True)
    if not ship_gate_ok():
        raise HTTPException(503, "payment unavailable: the containment self-test has not passed on this install")
    if parse_lightning_address(req.payee) is None:
        raise HTTPException(400, "payee must be a Lightning Address (name@domain)")
    domain = parse_lightning_address(req.payee)[1]
    breach = check_ceilings(domain, req.amount_sats)
    if breach:
        raise HTTPException(403, f"payment refused: {breach}")
    try:
        res = resolve_and_invoice(req.payee, req.amount_sats)       # re-verify integrity on the fresh invoice
    except ValueError as e:
        raise HTTPException(422, str(e))                            # substituted/inflated → rejected, no send

    if req.via == "nwc":
        # Pay over the user's budget-limited NWC connection. The WALLET signs + enforces its cap; the
        # connection secret stays in the keychain and NEVER reaches this response or the model (status only).
        from web_pay_nwc import pay_via_nwc
        store = _nwc_store()
        if not store.has(req.nwc_alias):
            raise HTTPException(404, "no NWC connection — connect your wallet in Settings first")
        ok, status = pay_via_nwc(store, req.nwc_alias, res["bolt11"])
        if ok:
            _ledger_record(domain, req.amount_sats)
        return {"status": "paid" if ok else "failed", "via": "nwc",
                "payee_domain": domain, "amount_sats": req.amount_sats}

    # Default: hand off to the user's OWN wallet — Vokter stores nothing, signs nothing. bolt11 is public.
    _ledger_record(domain, req.amount_sats)
    return {"status": "ready", "handoff": f"lightning:{res['bolt11']}",
            "payee_domain": domain, "amount_sats": req.amount_sats}


# ───────────────────────── NWC connection provisioning (human-only; the URI is a secret) ─────────────────────────
class NWCConnectRequest(BaseModel):
    uri: str                       # the user's nostr+walletconnect:// URI (contains the connection secret).
    alias: str = "default"         # a label for this connection.
    unattended_cap_sats: int = 0   # 0 = confirm every payment (default, conservative). >0 = the EXPLICIT
                                   # opt-in ceiling for unattended-within-budget (not wired to bypass the
                                   # per-payment confirm in this slice — a CEILING, not a consent bypass).


def _require_human(x_vokter_human_session: str | None):
    from chat import is_local_human_session
    if not is_local_human_session(x_vokter_human_session):
        raise HTTPException(403, "only the local human session can manage wallet connections")


@router.post("/api/web/pay/nwc/connect")
def nwc_connect(req: NWCConnectRequest, x_vokter_human_session: str | None = Header(default=None)):
    """Store the user's budget-limited NWC connection in the keychain. Human-only (the URI carries the
    connection secret). The URI is NEVER echoed back — the response confirms the alias + cap only."""
    _require_human(x_vokter_human_session)
    from web_pay_nwc import validate_nwc_uri
    if not validate_nwc_uri(req.uri):
        raise HTTPException(400, "not a valid nostr+walletconnect:// connection URI")
    _nwc_store().put(req.alias, req.uri, req.unattended_cap_sats)
    return {"status": "connected", "alias": (req.alias or "default").strip(),
            "unattended_cap_sats": int(req.unattended_cap_sats or 0)}   # deliberately NO uri in the response


@router.post("/api/web/pay/nwc/revoke")
def nwc_revoke(req: dict, x_vokter_human_session: str | None = Header(default=None)):
    """One-action local revocation (also delete the connection in your wallet). Human-only."""
    _require_human(x_vokter_human_session)
    removed = _nwc_store().delete(str((req or {}).get("alias", "default")))
    return {"status": "revoked" if removed else "absent"}


@router.get("/api/web/pay/nwc")
def nwc_list(x_vokter_human_session: str | None = Header(default=None)):
    """List connected wallets — alias + unattended cap ONLY, never the URI/secret. Human-only."""
    _require_human(x_vokter_human_session)
    return _nwc_store().list_public()


# ───────────────────────── opaque-crypto rail: x402 + direct on-chain (confirm-token bound) ─────────────────────────
class OnchainPayRequest(BaseModel):
    rail: str = "onchain"          # "onchain" = direct BIP21/EIP-681 deep-link; "x402" = HTTP-402 agent rail.
    asset: str = ""                # "BTC"/"ETH" for onchain; an ERC-20 contract (0x…) for x402.
    network: str = ""              # "bitcoin"/"ethereum"/"base"/… or a numeric EVM chain id.
    destination: str = ""          # the opaque payTo address (for x402, resolved from the challenge).
    amount: str = ""               # exact amount, as a STRING (bound byte-for-byte by the confirm-token).
    challenge: dict | None = None  # for rail="x402": the server's HTTP-402 body (accepts[...] requirements).
    confirm: bool = False          # the human's explicit per-payment approval (MANDATORY — irreversible).
    confirm_token: str = ""        # echoed from the phase-1 428 — BINDS phase-2 to the confirmed address+amount.


@router.post("/api/web/pay/onchain")
def web_pay_onchain(req: OnchainPayRequest, x_vokter_human_session: str | None = Header(default=None)):
    """Pay an OPAQUE crypto destination (a raw on-chain address, or an x402 payTo) WITHOUT Vokter holding
    funds or keys. Two phases, stateless — same shape as the LNURL route, with two differences a human can't
    paper over:

      * The destination is NOT human-verifiable (no domain). The phase-1 428 carries, as a backend-asserted
        field, `verifiable=False` + a caveat the confirm sheet MUST show. (x402 also carries the resolvable
        resource `origin`, but its payTo is still opaque.)
      * There is no public invoice to re-verify against, so CONFIRM-INTEGRITY rides a CONFIRM-TOKEN: phase-1
        issues an HMAC over (asset|network|destination|amount); phase-2 requires it and recomputes it over
        the SUBMITTED fields. A substituted address or inflated amount → 422, never handed off.

    GateNotPassed → 503 (TIER-6 not green on this install). Per-payment human-confirm is mandatory.
    """
    from chat import is_local_human_session
    from safety import HUMAN, enforce_http
    import web_pay_x402 as x4

    context = HUMAN if is_local_human_session(x_vokter_human_session) else "autonomous"

    def _resolve() -> dict:
        """Normalise the request into {asset, network, destination, amount, resource_origin} for BOTH rails."""
        if req.rail == "x402":
            p = x4.parse_x402_challenge(req.challenge or {})
            return p
        if not x4.validate_destination(req.network, req.destination):
            raise ValueError("destination is not a valid address for this network")
        return {"asset": req.asset, "network": req.network, "destination": req.destination,
                "amount": x4.normalize_amount(req.amount), "resource": "", "resource_origin": ""}

    target = f"{req.amount or '?'} {req.asset or '?'} to {req.destination or '(x402)'} via {req.rail}"

    if not req.confirm:
        try:
            enforce_http("web.pay", target, context=context, confirmed=False)
        except HTTPException as e:
            if e.status_code != 428:
                raise                                   # non-human → 403 propagates; nothing resolved
        if not ship_gate_ok():
            raise HTTPException(503, "payment unavailable: the containment self-test has not passed on this install")
        if req.rail not in ("onchain", "x402"):
            raise HTTPException(400, "rail must be 'onchain' or 'x402'")
        try:
            p = _resolve()
        except ValueError as e:
            raise HTTPException(422, str(e))
        if req.rail == "onchain" and not x4.validate_destination(p["network"], p["destination"]):
            raise HTTPException(422, "destination is not a valid address for this network")
        breach = x4.check_opaque_ceilings(p["destination"])
        if breach:
            raise HTTPException(403, f"payment refused: {breach}")
        token = x4.confirm_token(p["asset"], p["network"], p["destination"], p["amount"])
        raise HTTPException(428, detail={
            "error": "confirmation_required", "action": "web.pay", "rail": req.rail,
            "asset": p["asset"], "network": p["network"], "destination": p["destination"],
            "amount": p["amount"], "resource_origin": p.get("resource_origin", ""),
            "verifiable": False, "caveat": x4.CAVEAT, "confirm_token": token})

    # Phase 2 — confirmed. The human approved the exact fields the phase-1 428 displayed.
    enforce_http("web.pay", target, context=context, confirmed=True)
    if not ship_gate_ok():
        raise HTTPException(503, "payment unavailable: the containment self-test has not passed on this install")
    try:
        p = _resolve()
    except ValueError as e:
        raise HTTPException(422, str(e))
    # ── CONFIRM-INTEGRITY: phase-2 fields must match the confirm-token the human approved (code, not model) ──
    if not x4.verify_confirm_token(req.confirm_token, p["asset"], p["network"], p["destination"], p["amount"]):
        raise HTTPException(422, "REJECTED: destination/amount do not match what you confirmed (confirm-integrity)")
    breach = x4.check_opaque_ceilings(p["destination"])
    if breach:
        raise HTTPException(403, f"payment refused: {breach}")
    try:
        handoff = x4.build_x402_handoff(p) if req.rail == "x402" else \
            x4.build_onchain_handoff(p["asset"], p["network"], p["destination"], p["amount"])
    except ValueError as e:
        raise HTTPException(422, str(e))
    x4.record_opaque(p["destination"])
    return {"status": "ready", "rail": req.rail, "asset": p["asset"], "network": p["network"],
            "destination": p["destination"], "amount": p["amount"],
            "resource_origin": p.get("resource_origin", ""), "verifiable": False, "caveat": x4.CAVEAT,
            "handoff": handoff}
