"""Tier-3 Phase 2B — NWC capped-session: the connection-secret is handled like the OAuth token, and the
mandate envelope (Mode A) decides unattended eligibility PURELY from the stored envelope.

Asserts (no network, no real wallet, no keychain):
  * the store never surfaces the URI/secret — list_public() carries alias + NON-SECRET envelope ONLY,
  * pay_via_nwc reads the URI only to hand it to the transport and returns STATUS ONLY (no secret),
  * a missing connection fails closed,
  * validate_nwc_uri rejects garbage,
  * envelope_check (Mode A) ALLOWs only in-envelope and downgrades every out-of-envelope term,
  * the gate helper is default-OFF.

Run:  uv run --python 3.12 --with fastapi --with pydantic python tests/web_pay_nwc_test.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import web_pay_nwc as wpn
from web_pay_nwc import InMemoryNWCStore, pay_via_nwc, validate_nwc_uri, envelope_check, _new_envelope

FAILS = []
def check(d, c):
    print(("PASS " if c else "FAIL ") + d)
    if not c:
        FAILS.append(d)

SECRET = "SECRET-never-to-model-abc123"
URI = f"nostr+walletconnect://deadbeef?relay=wss://r&secret={SECRET}"
PAYEE = "alice@shop.example"
OFFLIST = "mallory@evil.example"


def _env(allow=(PAYEE,), per_tx=5_000, budget=10_000, throttle=5, window=7 * 86400):
    return _new_envelope(list(allow), per_tx, budget, window, throttle)


def main():
    # ── the gate is default-OFF ──
    os.environ.pop("VOKTER_NWC_UNATTENDED_GATE", None)
    check("gate default-OFF", wpn.unattended_gate_ok() is False)
    os.environ["VOKTER_NWC_UNATTENDED_GATE"] = "passed"
    check("gate ON when passed", wpn.unattended_gate_ok() is True)
    os.environ.pop("VOKTER_NWC_UNATTENDED_GATE", None)

    s = InMemoryNWCStore()
    s.put("default", URI, _env())
    check("has() true after put", s.has("default"))
    check("envelope() carries the allow-list", PAYEE in (s.envelope("default") or {}).get("allow_list", []))

    # list_public exposes alias + envelope ONLY — never the URI/secret
    pub = s.list_public()
    check("list_public has the alias", any(e["alias"] == "default" for e in pub))
    check("list_public NEVER contains the secret/URI", SECRET not in str(pub) and "walletconnect" not in str(pub))
    check("list_public DOES carry the non-secret envelope", any(e.get("envelope", {}).get("allow_list") for e in pub))

    # pay_via_nwc: the URI reaches the TRANSPORT but the caller gets STATUS ONLY
    seen = {}
    wpn._nwc_transport = lambda uri, bolt11: (seen.update(uri=uri, bolt11=bolt11) or (True, "paid"))
    ok, status = pay_via_nwc(s, "default", "lnbc10u1xyz")
    check("pay_via_nwc returns (True,'paid')", (ok, status) == (True, "paid"))
    check("the return carries NO secret", SECRET not in str((ok, status)))
    check("the transport DID receive the URI (it flows there, not to the caller)", seen.get("uri") == URI)

    # missing connection → fail closed, transport never called
    seen.clear()
    ok2, status2 = pay_via_nwc(s, "nope", "lnbc10u1xyz")
    check("missing connection → (False,'no_connection')", (ok2, status2) == (False, "no_connection"))
    check("transport NOT called when no connection", seen == {})

    # ── the mandate envelope (Mode A) — PURE eligibility, from the stored envelope ──
    wpn._SPEND_LEDGER.clear()
    s2 = InMemoryNWCStore(); s2.put("default", URI, _env(per_tx=5_000, budget=2_000, throttle=2))
    ok, _ = envelope_check(s2, "default", PAYEE, 1000)
    check("envelope: in-envelope → ALLOW", ok is True)
    ok, why = envelope_check(s2, "default", OFFLIST, 1000)
    check("envelope: off allow-list → downgrade", ok is False and "allow-list" in why)
    ok, why = envelope_check(s2, "default", "not-an-address", 1000)
    check("envelope: non-verifiable payee → downgrade", ok is False and "verifiable" in why)
    ok, why = envelope_check(s2, "default", PAYEE, 9_999)
    check("envelope: over per-tx → downgrade", ok is False and "per-payment" in why)

    # budget + throttle are relative to the in-window ledger
    wpn._SPEND_LEDGER.clear(); wpn._record_spend("default", 1000)
    ok, why = envelope_check(s2, "default", PAYEE, 1500)   # 1000 + 1500 = 2500 > budget 2000
    check("envelope: over rolling budget → downgrade", ok is False and "budget" in why)
    wpn._SPEND_LEDGER.clear(); wpn._record_spend("default", 1000); wpn._record_spend("default", 1)
    ok, why = envelope_check(s2, "default", PAYEE, 100)    # count already 2, throttle 2 → 3rd over
    check("envelope: over count throttle → downgrade", ok is False and "count" in why)

    # expired window → downgrade
    wpn._SPEND_LEDGER.clear()
    e = _env(); e["expires_at"] = time.time() - 1
    s3 = InMemoryNWCStore(); s3.put("default", URI, e)
    ok, why = envelope_check(s3, "default", PAYEE, 1000)
    check("envelope: expired window → downgrade", ok is False and "expired" in why)

    # not opted-in (no connection) → downgrade
    ok, why = envelope_check(InMemoryNWCStore(), "default", PAYEE, 1000)
    check("envelope: no connection → downgrade", ok is False)

    # one-action revoke
    check("delete() removes it", s.delete("default") and not s.has("default"))

    # validate rejects garbage
    check("validate_nwc_uri rejects garbage", validate_nwc_uri("not-a-uri") is False)
    check("validate_nwc_uri rejects empty", validate_nwc_uri("") is False)

    print()
    if FAILS:
        print(f"❌ {len(FAILS)} FAILED: {FAILS}"); sys.exit(1)
    print("✅ NWC: secret stays in the store, status-only out, fail-closed, revocable; envelope is Mode-A tight")


if __name__ == "__main__":
    main()
