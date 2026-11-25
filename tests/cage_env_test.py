"""A4 hermetic regression: the contained-browser env (interactive_browser.clean_env) must be built by
INCLUSION and carry NO VOKTER_* secret — so a Chromium escape reading /proc/self/environ in the cage
finds neither the SQLCipher key nor the P2 human-session token. Pure function, no cage needed.

  VOKTER_PASTA=pasta desktop/runtime/venv/bin/python tests/cage_env_test.py

Bites: the old clean_env() = dict(os.environ) copied the whole backend env in → these FAIL; the
inclusion-allowlist version → PASS.
"""
import os
import sys

# Secrets that MUST NOT reach the cage, set loudly in this process's env before clean_env is built.
os.environ["VOKTER_DB_KEY"] = "SENTINEL-db-key-must-not-leak"
os.environ["VOKTER_HUMAN_SESSION_TOKEN"] = "SENTINEL-human-token-must-not-leak"
os.environ["VOKTER_EMAIL_PASSWORD"] = "SENTINEL-smtp-must-not-leak"
os.environ["VOKTER_TIER3_SECRET_GATE"] = "passed"          # a non-secret VOKTER_* — must also be excluded
os.environ.setdefault("PATH", "/usr/bin:/bin")
os.environ.setdefault("HOME", "/root")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import interactive_browser as ib

env = ib.clean_env()
fails = []
def check(desc, cond):
    print(("PASS " if cond else "FAIL ") + desc)
    if not cond:
        fails.append(desc)

leaked = [k for k in env if k.startswith("VOKTER_")]
check("no VOKTER_* variable reaches the cage env", leaked == [])
check("VOKTER_DB_KEY (SQLCipher key) absent from cage env", "VOKTER_DB_KEY" not in env)
check("VOKTER_HUMAN_SESSION_TOKEN absent from cage env", "VOKTER_HUMAN_SESSION_TOKEN" not in env)
check("VOKTER_EMAIL_PASSWORD absent from cage env", "VOKTER_EMAIL_PASSWORD" not in env)
# no secret VALUE leaks under any key name
blob = repr(sorted(env.items()))
check("no sentinel secret value present under any key", "SENTINEL-" not in blob)
# still usable: chrome/bwrap need at least PATH
check("PATH is present (cage can still start)", bool(env.get("PATH")))

print()
if fails:
    print("❌ cage-env A4: FAILED", fails); sys.exit(1)
print("✅ cage-env A4: clean_env() leaks no secret into the contained browser")
