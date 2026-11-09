"""Tier-3 Phase 2 — NWC capped-session: the connection-secret is handled like the OAuth token.

Asserts (no network, no real wallet):
  * the store never surfaces the URI/secret — list_public() carries alias + cap ONLY,
  * pay_via_nwc reads the URI only to hand it to the transport and returns STATUS ONLY (no secret),
  * a missing connection fails closed,
  * validate_nwc_uri rejects garbage.

Run:  uv run --python 3.12 --with fastapi --with pydantic python tests/web_pay_nwc_test.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import web_pay_nwc as wpn
from web_pay_nwc import InMemoryNWCStore, pay_via_nwc, validate_nwc_uri

FAILS = []
def check(d, c):
    print(("PASS " if c else "FAIL ") + d)
    if not c:
        FAILS.append(d)

SECRET = "SECRET-never-to-model-abc123"
URI = f"nostr+walletconnect://deadbeef?relay=wss://r&secret={SECRET}"


def main():
    s = InMemoryNWCStore()
    s.put("default", URI, unattended_cap_sats=0)
    check("has() true after put", s.has("default"))
    check("cap() reads the stored cap", s.cap("default") == 0)

    # list_public exposes alias + cap ONLY — never the URI/secret
    pub = s.list_public()
    check("list_public has the alias", any(e["alias"] == "default" for e in pub))
    check("list_public NEVER contains the secret/URI", SECRET not in str(pub) and "walletconnect" not in str(pub))

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

    # one-action revoke
    check("delete() removes it", s.delete("default") and not s.has("default"))

    # validate rejects garbage
    check("validate_nwc_uri rejects garbage", validate_nwc_uri("not-a-uri") is False)
    check("validate_nwc_uri rejects empty", validate_nwc_uri("") is False)

    print()
    if FAILS:
        print(f"❌ {len(FAILS)} FAILED: {FAILS}"); sys.exit(1)
    print("✅ NWC: connection secret stays in the store, status-only out, fail-closed, revocable")


if __name__ == "__main__":
    main()
