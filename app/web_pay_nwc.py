"""Tier-3 Phase 2B — NWC capped-session store + mandate envelope (the ONE path where Vokter holds a
fund-moving secret). OPT-IN, DEFAULT OFF, behind its OWN gate — see TIER3_PHASE2B_NWC_UNATTENDED.md.

The MODEL (state the core safety property exactly):
  The user's OWN wallet holds the funds + the key and ENFORCES the budget cap (NIP-47 connection budget).
  Vokter holds ONLY a budget-limited, revocable NWC connection secret — NEVER funds, NEVER the key. A
  stolen Vokter secret can authorize at most up-to-the-cap of pay_invoice requests the wallet already
  agreed to; the wallet REFUSES everything beyond. It cannot drain the wallet or move the key.

The connection secret is handled EXACTLY like the OAuth scoped token (oauth_broker.KeychainTokenStore):
  * keychain ONLY (service 'vokter-nwc'), NEVER returned to any caller/model/log,
  * asserted ABSENT in the TIER-7 leakage probe,
  * one-action revocable (delete here; the user ALSO revokes in their own wallet).

HONEST FLOOR (surfaced in the connect UI): even capped, the stored secret is the attack surface; the
wallet enforcing the cap is the real protection and Vokter CANNOT verify the wallet armed a budget;
revoking here does NOT recall an already-exfiltrated copy (only wallet-side revocation / budget
exhaustion does). This is why it is opt-in — the user chooses the trade. QR per-payment stays the default.
"""
from __future__ import annotations

import json
import os
import time

_SERVICE = "vokter-nwc"
_INDEX = "nwc/__index__"

# ───────────────────────── the TIER-7 ship gate (SEPARATE from web.pay's gate) ─────────────────────────
_GATE_ENV = "VOKTER_NWC_UNATTENDED_GATE"   # set to "passed" ONLY by the packaged TIER-7 fire-test, after
                                           # both halves (envelope-integrity + secret-non-leakage) are green
                                           # on the frozen .deb. Distinct from VOKTER_TIER3_SECRET_GATE so
                                           # unattended spend can NEVER ride login's / web.pay's green.


def unattended_gate_ok() -> bool:
    """True only once the TIER-7 fire-test has passed on THIS install. Default OFF → spend refused (503)."""
    return os.environ.get(_GATE_ENV, "").strip().lower() == "passed"


# The honest floor, one string — the connect UI MUST show it before a wallet is connected.
CONNECT_CAVEAT = (
    "Hands-off payments store ONE budget-limited connection to your wallet — never your wallet, key, or "
    "funds. The real protection is your wallet's own spending limit on this connection, which Vokter "
    "cannot verify is set — set a limit in your wallet too. If this connection secret is ever stolen it "
    "can spend up to that limit until you revoke it IN YOUR WALLET; revoking here stops Vokter but cannot "
    "recall a copy that already leaked. The default (scan a QR per payment) stores nothing — use this only "
    "if you want small, allow-listed payments without confirming each one."
)


def _conn_name(alias: str) -> str:
    return f"nwc/{(alias or 'default').strip()}"


def validate_nwc_uri(uri: str) -> bool:
    """A real NWC connection URI parses to a wallet pubkey + relay(s) + secret. We validate shape via
    nostr_sdk; we never log or echo the URI (it carries the connection secret)."""
    try:
        from nostr_sdk import NostrWalletConnectUri
        u = NostrWalletConnectUri.parse((uri or "").strip())
        return bool(u.public_key() and u.relays() and u.secret())
    except Exception:
        return False


# ───────────────────────── the mandate envelope (Mode A — allow-listed recurring payees) ─────────────────────────
# Conservative defaults (user-editable at connect). Mode B (any-verifiable / zaps) is deliberately NOT built
# — a later, tighter-capped slice (TIER3_PHASE2B_NWC_UNATTENDED.md §3c).
DEFAULTS = {
    "per_tx_max_sats": 10_000,     # per unattended payment
    "budget_sats": 50_000,         # rolling total across the window
    "window_sec": 7 * 86_400,      # 7 days — the mandate auto-expires; reconnect to renew
    "throttle_n": 20,              # max unattended payments per window (unit-agnostic second limit)
}


def normalize_payee(payee: str) -> str | None:
    """Lowercased name@domain for a valid Lightning Address, else None. The allow-list is keyed on this —
    the verifiable, human-typed anchor (never the model's claim)."""
    from web_pay import parse_lightning_address
    parsed = parse_lightning_address(payee)
    return f"{parsed[0]}@{parsed[1]}".lower() if parsed else None


def _new_envelope(allow_list, per_tx_max_sats, budget_sats, window_sec, throttle_n, now=None) -> dict:
    now = time.time() if now is None else now
    win = int(window_sec or DEFAULTS["window_sec"])
    norm = []
    for a in (allow_list or []):
        n = normalize_payee(a)
        if n and n not in norm:
            norm.append(n)
    return {
        "allow_list": norm,
        "per_tx_max_sats": int(per_tx_max_sats or DEFAULTS["per_tx_max_sats"]),
        "budget_sats": int(budget_sats or DEFAULTS["budget_sats"]),
        "window_sec": win,
        "throttle_n": int(throttle_n or DEFAULTS["throttle_n"]),
        "expires_at": now + win,
    }


# Spend ledger — (ts, alias, sats). In-memory, per-process: defense-in-depth beside the WALLET's own cap
# (the real control). A restart resets Vokter's view of the rolling budget; the wallet still enforces the
# true cap, so this is a deliberately-simple second limit, like web_pay's _LEDGER.
_SPEND_LEDGER: list[tuple[float, str, int]] = []


def _window_spend(alias: str, window_sec: int, now: float) -> tuple[int, int]:
    cut = now - window_sec
    rows = [r for r in _SPEND_LEDGER if r[0] >= cut and r[1] == alias]
    return sum(s for _, _, s in rows), len(rows)


def _record_spend(alias: str, sats: int) -> None:
    _SPEND_LEDGER.append((time.time(), alias, sats))


def envelope_check(store, alias: str, payee: str, amount_sats: int, now: float | None = None) -> tuple[bool, str]:
    """PURE (no I/O beyond reading the stored envelope): is this unattended spend INSIDE the mandate?
    Mode A — ALLOW iff: payee is a verifiable Lightning Address ON the allow-list ∧ ≤ per-tx max ∧ within
    the rolling budget ∧ within the auto-expiring window ∧ under the count throttle. Returns (ok, reason);
    reason is the downgrade message when ok is False. NOTHING here moves money — the handler re-resolves
    and runs confirm-integrity AFTER a genuine gateway ALLOW."""
    now = time.time() if now is None else now
    blob = store._blob(alias)
    if blob is None:
        return False, "no unattended connection — scan a QR to pay this time"
    env = blob.get("envelope") or {}
    norm = normalize_payee(payee)
    if norm is None:
        return False, "payee is not a verifiable Lightning Address — confirm this payment"
    if norm not in (env.get("allow_list") or []):
        return False, "payee is not on your hands-off allow-list — confirm this payment"
    if amount_sats <= 0:
        return False, "amount must be positive"
    if amount_sats > int(env.get("per_tx_max_sats", 0)):
        return False, "over your per-payment hands-off limit — confirm this payment"
    if now > float(env.get("expires_at", 0)):
        return False, "your hands-off window expired — reconnect your wallet to renew"
    spent, count = _window_spend(alias, int(env.get("window_sec", DEFAULTS["window_sec"])), now)
    if spent + amount_sats > int(env.get("budget_sats", 0)):
        return False, "over your rolling hands-off budget — confirm this payment"
    if count + 1 > int(env.get("throttle_n", 0)):
        return False, "over your hands-off payment-count limit — confirm this payment"
    return True, ""


class NWCStore:
    """The user's budget-limited NWC connection(s) in the OS keychain. The URI (which contains the
    connection secret) is NEVER returned to a caller/model — only booleans, the non-secret envelope, and
    the list of aliases. An index of aliases is kept because the keychain has no list op."""

    def _kc(self):
        from oauth_broker import import_keychain
        return import_keychain()

    def _idx(self) -> list[str]:
        raw = self._kc().get_key(service=_SERVICE, name=_INDEX)
        try:
            return json.loads(raw) if raw else []
        except Exception:
            return []

    def put(self, alias: str, uri: str, envelope: dict) -> None:
        alias = (alias or "default").strip()
        self._kc().set_key(json.dumps({"uri": uri, "envelope": envelope}),
                           service=_SERVICE, name=_conn_name(alias))
        # index stored under the fixed _INDEX key (not an alias) so it never collides with a connection
        self._kc().set_key(json.dumps(sorted(set(self._idx() + [alias]))), service=_SERVICE, name=_INDEX)

    def _blob(self, alias: str) -> dict | None:
        raw = self._kc().get_key(service=_SERVICE, name=_conn_name(alias))
        try:
            return json.loads(raw) if raw else None
        except Exception:
            return None

    def has(self, alias: str) -> bool:
        return self._blob(alias) is not None

    def envelope(self, alias: str) -> dict | None:
        b = self._blob(alias)
        return b.get("envelope") if b else None

    def _uri(self, alias: str) -> str | None:
        """INTERNAL ONLY — the connection secret. Never surfaced to a response/model/log."""
        b = self._blob(alias)
        return b.get("uri") if b else None

    def delete(self, alias: str) -> bool:
        """One-action revoke of Vokter's authority. (The user ALSO revokes in their own wallet — the only
        thing that kills an already-exfiltrated copy; see CONNECT_CAVEAT.)"""
        alias = (alias or "default").strip()
        ok = self._kc().delete_key(service=_SERVICE, name=_conn_name(alias))
        self._kc().set_key(json.dumps([a for a in self._idx() if a != alias]), service=_SERVICE, name=_INDEX)
        return ok

    def list_public(self) -> list[dict]:
        """The unattended menu — alias + the NON-SECRET envelope. NEVER the URI/secret."""
        out = []
        for a in self._idx():
            b = self._blob(a)
            if b is not None:
                out.append({"alias": a, "envelope": b.get("envelope") or {}})
        return out


class InMemoryNWCStore:
    """Test double — provision a sentinel connection without a keychain. Never used in production."""
    def __init__(self):
        self._d: dict[str, dict] = {}

    def put(self, alias, uri, envelope):
        self._d[(alias or "default").strip()] = {"uri": uri, "envelope": envelope}

    def _blob(self, alias):
        return self._d.get((alias or "default").strip())

    def has(self, alias):
        return (alias or "default").strip() in self._d

    def envelope(self, alias):
        b = self._blob(alias)
        return b.get("envelope") if b else None

    def _uri(self, alias):
        b = self._blob(alias)
        return b.get("uri") if b else None

    def delete(self, alias):
        return self._d.pop((alias or "default").strip(), None) is not None

    def list_public(self):
        return [{"alias": a, "envelope": v.get("envelope") or {}} for a, v in self._d.items()]


# ───────────────────────── the NWC transport (real nostr_sdk; patchable seam) ─────────────────────────
def _nwc_transport(uri_str: str, bolt11: str) -> tuple[bool, str]:
    """Send a pay_invoice over the user's NWC connection (NIP-47, via nostr_sdk). The WALLET signs and
    enforces its budget; we get only success/failure. Returns (ok, status) — NEVER the uri/secret/preimage.
    Patched by the hermetic TIER-7 fire-test with a mock wallet that ENFORCES a budget.

    Written against nostr_sdk 0.44.2's Nwc API and VALIDATED in a real Coinos round-trip (2026-11-17:
    baseline pay, wallet-authoritative budget stop, kill-mid-pay, Vokter revoke). The prior code imported
    `NostrWalletConnect` (does NOT exist in 0.44.2 → ImportError) and built `PayInvoiceRequest(invoice=...)`
    (needs id+invoice+amount) — an unpinned range let that slip through; app/requirements.txt now pins
    `nostr-sdk==0.44.2`.
    ⚠️ RESIDUAL — BEFORE ACTIVATION: the pin reduces drift but this call is validated only against the
    DEV-VENV 0.44.2. The frozen-.deb build MUST be confirmed on 0.44.2 and this transport re-validated
    there, or the import/signature-mismatch class of bug can recur. NWC stays DORMANT/gated until then."""
    import asyncio
    from nostr_sdk import Nwc, NostrWalletConnectUri, PayInvoiceRequest

    async def _go():
        client = Nwc(NostrWalletConnectUri.parse(uri_str))
        # amount=None → pay the BOLT11's own amount; id=None → no caller-chosen request id. The WALLET
        # enforces its connection budget and raises on over-budget / decline / unreachable.
        await client.pay_invoice(PayInvoiceRequest(id=None, invoice=bolt11, amount=None))

    try:
        asyncio.run(_go())
        return True, "paid"
    except Exception:
        return False, "failed"   # status only — never surface the exception (no secret, no detail leak)


def pay_via_nwc(store, alias: str, bolt11: str) -> tuple[bool, str]:
    """Pay `bolt11` over the stored connection. Reads the URI (secret) ONLY to hand it to the transport,
    drops the reference immediately, and returns status only — the secret never reaches the caller/model."""
    uri = store._uri(alias)
    if not uri:
        return False, "no_connection"
    try:
        return _nwc_transport(uri, bolt11)
    finally:
        del uri   # minimize the secret's lifetime (a copy may transit nostr_sdk)
