"""Tier-3 Phase 2 — the LIVE web.pay (LNURL) handler, end-to-end (hermetic), runnable off the VM.

Drives the real web_pay() handler both phases with a mock LNURL (no network, no funds), asserting the
full TIER-6 check set: enriched 428, phase-2 hand-off of a confirm-integrity-verified invoice, inflated-
amount and substituted-invoice REJECTED, non-human 403, and no extra callback secret in the response/logs.

Run:  uv run --python 3.12 --with fastapi --with pydantic python tests/web_pay_e2e_test.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import containment_selftest as cs

ok, checks = cs._pay_handler_e2e()
for k, v in checks.items():
    print(("PASS " if v else "FAIL ") + k)
print()
if not ok:
    print("❌ FAILED:", [k for k, v in checks.items() if not v]); sys.exit(1)
print("✅ TIER-6 e2e: live web.pay hands off a confirmed invoice, rejects substitution, leaks nothing")
