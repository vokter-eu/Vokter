"""Tier-3 Phase 1 — the LIVE two-phase web.login handler, end-to-end (hermetic), runnable off the VM.

Drives the real web_login() HTTP handler through both phases with a fake transport (no cage) and
asserts the full TIER-5 check set: enriched 428, phase-2 'succeeded' with the sentinel actually typed,
wrong-origin refusal, non-human 403-before-navigation, and no secret in the 428 body / return / stdout /
DB tables / gateway target. The physical keystroke-through-the-cage is proven separately (TIER-4 positive).

Run:  uv run --python 3.12 --with fastapi --with pydantic python tests/web_login_e2e_test.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import containment_selftest as cs

ok, checks = cs._login_handler_e2e()
for k, v in checks.items():
    print(("PASS " if v else "FAIL ") + k)
print()
if not ok:
    print("❌ FAILED:", [k for k, v in checks.items() if not v]); sys.exit(1)
print("✅ TIER-5 e2e: live two-phase web.login works end to end and leaks nothing")
