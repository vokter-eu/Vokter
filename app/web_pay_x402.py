"""Tier-3 Phase 2 — web.pay opaque-crypto slice: x402 (HTTP-402 agent rail) + on-chain via WalletConnect.

The LNURL slice (web_pay.py) binds a HUMAN-VERIFIABLE domain (a Lightning Address) — the destination is
origin-bindable and readable by eye. THIS slice covers destinations a human CANNOT validate by eye: a raw
EVM/BTC address (direct on-chain) or an x402 `payTo` address. Sovereign-maximal, exactly like LNURL —
Vokter CONSTRUCTS the payment request and HANDS OFF to the user's OWN wallet (a BIP21 / EIP-681 deep-link,
or a one-shot WalletConnect request). Vokter holds no funds, holds no keys, signs nothing, settles nothing;
the user confirms + signs IN their wallet. (A CAPPED x402 session that auto-settles within a budget is the
SAME pattern as the NWC capped-session rail in web_pay_nwc.py — deferred; this slice is per-payment hand-off.)

Two guards, both CODE (never the model):

  1. OPAQUE-DESTINATION CAVEAT. There is no domain to bind, so the confirm sheet MUST carry, as a
     backend-asserted field, `verifiable=False` + the explicit caveat *"destination not verifiable — you're
     trusting the agent's claim of who you're paying."* x402 additionally carries the resource ORIGIN (the
     domain that served the 402) which IS resolvable — the sheet shows "paying <origin> → <opaque address>" —
     but the payTo address itself is still opaque, so the caveat stands for both rails.

  2. CONFIRM-INTEGRITY via a CONFIRM-TOKEN. The two-phase handler is stateless and page JS drives BOTH
     calls (it renders the sheet from the phase-1 428 and initiates phase-2), so "reject a substituted
     address" is only REAL if phase-2 is cryptographically BOUND to what the human confirmed. Unlike LNURL
     (which re-resolves a public invoice from the payee's domain and re-checks LUD-06 both phases), an
     opaque address has no public source of truth to re-verify against — so phase-1 returns an HMAC over
     (asset|network|destination|amount|nonce|exp) keyed by a per-PROCESS secret, and phase-2 REQUIRES that
     token and recomputes the HMAC over the SUBMITTED fields. A swapped address or inflated amount no longer
     matches the token → REJECTED (422), never handed off. The page cannot forge a token for address B from
     the token it was handed for address A (it does not hold the process key).
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import time
from decimal import Decimal, InvalidOperation

# Per-PROCESS confirm-token key. Random at import → the renderer / page JS can never forge a token for a
# substituted destination, and tokens do not survive a restart (a stale sheet simply re-confirms). Never
# leaves the backend; it is NOT a spend secret (it only binds fields the human already saw).
_TOKEN_KEY = os.urandom(32)
_TOKEN_TTL_SEC = 300          # a confirm sheet is good for 5 minutes; past that, re-confirm.

_EVM_ADDR = re.compile(r"^0x[0-9a-fA-F]{40}$")
_BTC_ADDR = re.compile(r"^(bc1[02-9ac-hj-np-z]{6,87}|[13][a-km-zA-HJ-NP-Z1-9]{25,39})$")

# network label → EVM chain id (EIP-681). A numeric network passes through as-is.
_CHAIN_ID = {"ethereum": 1, "mainnet": 1, "base": 8453, "optimism": 10, "arbitrum": 42161, "polygon": 137}
_NATIVE = {"BTC": "bitcoin", "ETH": "ethereum"}   # assets we can build a direct-on-chain deep-link for


# ───────────────────────── confirm-integrity token (the load-bearing guard) ─────────────────────────
def _canon(asset: str, network: str, destination: str, amount: str) -> bytes:
    """The exact bytes the human confirmed. Addresses/amount are compared byte-for-byte — a single changed
    character (substituted address, inflated amount) yields a different digest and fails verification."""
    return "\x1f".join([asset, network, destination, amount]).encode()


def confirm_token(asset: str, network: str, destination: str, amount: str, ttl: int = _TOKEN_TTL_SEC) -> str:
    """Bind (asset, network, destination, amount) to a short-lived HMAC the page must echo in phase-2."""
    nonce = os.urandom(8).hex()
    exp = str(int(time.time()) + int(ttl))
    body = _canon(asset, network, destination, amount) + b"\x1f" + nonce.encode() + b"\x1f" + exp.encode()
    tag = hmac.new(_TOKEN_KEY, body, hashlib.sha256).hexdigest()
    return f"{nonce}.{exp}.{tag}"


def verify_confirm_token(token: str, asset: str, network: str, destination: str, amount: str) -> bool:
    """True iff `token` was issued by THIS process for EXACTLY these fields and has not expired. A swapped
    destination/amount recomputes a different tag → False (constant-time compare)."""
    try:
        nonce, exp, tag = (token or "").split(".")
        if int(exp) < int(time.time()):
            return False
    except (ValueError, AttributeError):
        return False
    body = _canon(asset, network, destination, amount) + b"\x1f" + nonce.encode() + b"\x1f" + exp.encode()
    want = hmac.new(_TOKEN_KEY, body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(want, tag)


# ───────────────────────── validation (addresses + amount) ─────────────────────────
def validate_destination(network: str, destination: str) -> bool:
    """Format-check the opaque destination. We do NOT (cannot) attest WHO it is — that's the caveat's job;
    we only refuse a malformed address so a typo/garbled hand-off never reaches a wallet."""
    d = (destination or "").strip()
    if (network or "").lower() in ("bitcoin", "btc"):
        return bool(_BTC_ADDR.match(d))
    return bool(_EVM_ADDR.match(d))   # EVM chains (and x402 asset/payTo) are 0x-addresses


def normalize_amount(amount: str) -> str:
    """Return the amount as a canonical positive decimal STRING, or raise ValueError. Kept as a string so
    the confirm-token binds the exact value the human saw (no float round-trip)."""
    try:
        d = Decimal((amount or "").strip())
    except (InvalidOperation, TypeError):
        raise ValueError("amount is not a number")
    if d <= 0:
        raise ValueError("amount must be positive")
    return format(d.normalize(), "f")


# ───────────────────────── x402 challenge parsing ─────────────────────────
def parse_x402_challenge(challenge: dict) -> dict:
    """Parse a server's HTTP-402 body (x402 `accepts` requirements) into the fields the sheet + hand-off
    need. Returns {asset, network, destination(payTo), amount(atomic), resource, resource_origin}. The
    amount is x402's atomic `maxAmountRequired` (already in the asset's smallest unit)."""
    if not isinstance(challenge, dict):
        raise ValueError("x402 challenge must be an object")
    accepts = challenge.get("accepts")
    req = accepts[0] if isinstance(accepts, list) and accepts else challenge
    if not isinstance(req, dict):
        raise ValueError("x402 challenge has no payment requirements")
    pay_to = str(req.get("payTo") or req.get("pay_to") or "")
    asset = str(req.get("asset") or "")
    network = str(req.get("network") or "")
    amount = str(req.get("maxAmountRequired") or req.get("amount") or "")
    resource = str(req.get("resource") or challenge.get("resource") or "")
    if not (pay_to and asset and network and amount):
        raise ValueError("x402 requirements missing payTo/asset/network/amount")
    origin = ""
    m = re.match(r"^https?://([^/]+)", resource)
    if m:
        origin = m.group(1).lower()
    return {"asset": asset, "network": network, "destination": pay_to,
            "amount": normalize_amount(amount), "resource": resource, "resource_origin": origin}


# ───────────────────────── hand-off construction (never a signer) ─────────────────────────
def _chain_id(network: str) -> int | None:
    n = (network or "").strip().lower()
    if n.isdigit():
        return int(n)
    return _CHAIN_ID.get(n)


def build_onchain_handoff(asset: str, network: str, destination: str, amount: str) -> dict:
    """Build a per-payment hand-off for a DIRECT on-chain payment in a NATIVE asset (BTC / ETH). Returns a
    descriptor {scheme, uri} the user's wallet opens; Vokter signs/settles nothing. Token transfers are not
    built here (we don't fabricate a contract address) — route those through x402, where the token contract
    is in the challenge. Raises ValueError for anything we can't construct an honest deep-link for."""
    a = (asset or "").upper()
    if a == "BTC":
        return {"scheme": "bip21", "uri": f"bitcoin:{destination}?amount={amount}"}
    if a == "ETH":
        cid = _chain_id(network) or 1
        wei = format(Decimal(amount) * (10 ** 18), "f").split(".")[0]
        return {"scheme": "eip681", "uri": f"ethereum:{destination}@{cid}?value={wei}"}
    raise ValueError(f"direct on-chain hand-off supports BTC/ETH only (got {asset}); use x402 for tokens")


# ───────────────────────── count throttle (defense-in-depth; unit-agnostic) ─────────────────────────
# Opaque amounts span assets/units (BTC, ETH, atomic USDC) with no common denominator and no price oracle,
# so the sat-denominated ceilings of the LNURL path don't translate. The honest second limit here is a
# COUNT throttle — a cap on how many opaque hand-offs per window, overall and per destination. The PRIMARY
# control remains the mandatory per-payment human-confirm + the user's own wallet.
def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _oc_window():    return _env_int("VOKTER_PAY_OPAQUE_WINDOW_SEC", 86_400)
def _oc_max_total(): return _env_int("VOKTER_PAY_OPAQUE_MAX_WINDOW", 20)
def _oc_max_dest():  return _env_int("VOKTER_PAY_OPAQUE_MAX_DEST", 10)

_OC_LEDGER: list[tuple[float, str]] = []   # (ts, destination) — spend metadata, NOT a secret


def check_opaque_ceilings(destination: str) -> str | None:
    """Human-readable refusal if another opaque hand-off to `destination` would breach the count throttle."""
    now = time.time(); cut = now - _oc_window()
    while _OC_LEDGER and _OC_LEDGER[0][0] < cut:
        _OC_LEDGER.pop(0)
    if len(_OC_LEDGER) + 1 > _oc_max_total():
        return f"exceeds the {_oc_window() // 3600}h opaque-payment count ceiling ({_oc_max_total()})"
    if sum(1 for _, d in _OC_LEDGER if d == destination) + 1 > _oc_max_dest():
        return f"exceeds the per-destination opaque-payment count ceiling ({_oc_max_dest()})"
    return None


def record_opaque(destination: str) -> None:
    _OC_LEDGER.append((time.time(), destination))


CAVEAT = ("destination not verifiable — you're trusting the agent's claim of who you're paying")


def build_x402_handoff(parsed: dict) -> dict:
    """Build the hand-off for an x402 payment. The `asset` is the ERC-20 token CONTRACT and `amount` is
    atomic, so we emit an EIP-681 token-transfer the user's wallet signs, plus the raw requirement for an
    x402-native wallet. Vokter does not sign the EIP-3009 authorization and does not settle — the wallet
    does, then the wallet (or the renderer) retries the resource with the signed X-PAYMENT."""
    asset = parsed["asset"]; network = parsed["network"]
    dest = parsed["destination"]; amount = parsed["amount"]
    cid = _chain_id(network) or 1
    uri = f"ethereum:{asset}@{cid}/transfer?address={dest}&uint256={amount}"
    return {"scheme": "x402", "uri": uri,
            "requirement": {"scheme": "exact", "network": network, "asset": asset,
                            "payTo": dest, "maxAmountRequired": amount, "resource": parsed.get("resource", "")}}
