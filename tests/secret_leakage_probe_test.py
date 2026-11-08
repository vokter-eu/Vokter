"""Tier-3 Phase 1 — the TIER-4 secret-non-leakage NEGATIVE probe, runnable off the clean VM.

The POSITIVE half (real cage keystroke delivery) only runs inside the frozen .deb on the clean VM
(containment_selftest TIER 4, needs bwrap+pasta+nft+browser). This exercises the NEGATIVE half — the
full perform_password_login routine must leak the sentinel into none of: its return, a throwaway DB's
tables, or captured stdout/stderr — so the regression guard is green everywhere, not only in the VM.

Run (dev, keyless/plaintext throwaway DB):
    uv run --python 3.12 --with fastapi --with pydantic python tests/secret_leakage_probe_test.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import containment_selftest as cs

ok, detail = cs._login_leakage_negative()
print("detail (no sentinel):", detail)

fails = []
if detail["routine_status"] not in ("succeeded", "failed"):
    fails.append("routine returned a non-status value")
if detail["db_leak"]:
    fails.append("sentinel leaked into DB tables")
if detail["stdout_leak"]:
    fails.append("sentinel leaked into stdout/stderr")
if not detail["request_shape_clean"]:
    fails.append("web.login request shape carries a secret field")
if not ok:
    fails.append("overall non-leakage assertion failed")

# Belt-and-suspenders: the sentinel must not appear in this process's view of the detail either.
if cs._SENT_NEG in str(detail):
    fails.append("sentinel present in the probe's own detail dict")

if fails:
    print("❌ FAILED:", fails); sys.exit(1)
print("✅ TIER-4 negative probe: password never in return / DB / stdout; request shape secret-free")
