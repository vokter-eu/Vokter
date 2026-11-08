"""Tier-3 Phase 1 — Path 2 password-injector routine, structural guarantees (NO real credential).

Drives perform_password_login against a FAKE transport + a SENTINEL secret, asserting:
  * the ship gate blocks the routine until green (GateNotPassed);
  * WRONG resolved origin → refused: the secret is NEVER dereferenced and NEVER typed
    (password-manager pattern — origin read from the browser, not the model's claim);
  * OPAQUE/unreadable origin (data:/about:blank) → fail closed, never typed;
  * a matching origin types the password via the login-only keystroke path (type_secret), NEVER via
    Session.fill — and returns STATUS ONLY (the sentinel is never in the return);
  * a missing credential / failed keystroke → 'failed', still no leak;
  * KeychainCredentialStore is READ-ONLY (no put/set — Vokter never writes a user password).

Run:  desktop/runtime/venv/bin/python tests/web_login_injector_test.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import oauth_broker as ob
import web_login_injector as wli
from web_login_injector import InMemoryCredentialStore, KeychainCredentialStore, LoginSelectors, perform_password_login
from interactive_browser import Session

SENTINEL = "SENTINEL-PASSWORD-9c7f2a-never-to-model"
REGISTERED = "https://bank.example"
WRONG = "https://evil.example"
IDENTITY = "me"
SEL = LoginSelectors(username="#user", password="#pass", submit="#submit")

FAILS = []
def check(desc, cond):
    print(("PASS " if cond else "FAIL ") + desc)
    if not cond:
        FAILS.append(desc)


class FakeTransport:
    """Records what the routine drives + reports a resolved top-origin, so we assert the mechanism
    without a live browser. top_origin() is what the ROUTINE reads (never the model's claim)."""
    def __init__(self, origin, ok=True):
        self._origin = origin
        self.ok = ok
        self.typed = []       # (selector, value) the code actually tried to type — incl. the secret path
        self.clicked = []
    def top_origin(self): return self._origin
    def type_into(self, selector, value): self.typed.append((selector, value)); return self.ok
    def click(self, selector): self.clicked.append(selector); return self.ok
    def navigate(self, url): return self.ok
    def read_text(self): return ""


def seeded_store():
    s = InMemoryCredentialStore()
    s.put(REGISTERED, IDENTITY, SENTINEL)   # the sentinel is keyed on the REGISTERED origin
    return s


def main():
    # 1) ship gate blocks the routine until green
    os.environ.pop(ob._GATE_ENV, None)
    try:
        perform_password_login(Session(FakeTransport(REGISTERED)), seeded_store(),
                               registered_origin=REGISTERED, identity=IDENTITY, selectors=SEL)
        check("gate blocks the routine pre-green (raises GateNotPassed)", False)
    except ob.GateNotPassed:
        check("gate blocks the routine pre-green (raises GateNotPassed)", True)

    os.environ[ob._GATE_ENV] = "passed"   # open the gate (as the packaged selftest would)

    # 2) WRONG resolved origin → refused; secret NEVER dereferenced, NEVER typed
    t = FakeTransport(WRONG)
    r = perform_password_login(Session(t), seeded_store(),
                               registered_origin=REGISTERED, identity=IDENTITY, selectors=SEL)
    check("wrong origin → not 'succeeded'", r != "succeeded")
    check("  secret NEVER typed on wrong origin (transport untouched)", t.typed == [])
    check("  nothing clicked on wrong origin", t.clicked == [])

    # 3) OPAQUE / unreadable origin (data:/about:blank) → fail closed, never typed
    t = FakeTransport("null")   # chrome reports 'null' for a data:/opaque origin
    r = perform_password_login(Session(t), seeded_store(),
                               registered_origin=REGISTERED, identity=IDENTITY, selectors=SEL)
    check("opaque origin → not 'succeeded'", r != "succeeded")
    check("  secret NEVER typed on opaque origin", t.typed == [])

    # 4) matching origin → password typed via the login-only path; STATUS ONLY in the return
    t = FakeTransport(REGISTERED)
    r = perform_password_login(Session(t), seeded_store(), registered_origin=REGISTERED,
                               identity=IDENTITY, selectors=SEL, username="alice")
    check("matching origin → 'succeeded'", r == "succeeded")
    check("  password WAS typed into the password field (code path)", ("#pass", SENTINEL) in t.typed)
    check("  username typed into the username field (innocuous)", ("#user", "alice") in t.typed)
    check("  submit clicked", t.clicked == ["#submit"])
    check("  return is STATUS ONLY — sentinel never in the return value", SENTINEL not in r)

    # 5) missing credential → 'failed', password never typed (no None reaches a keystroke)
    t = FakeTransport(REGISTERED)
    r = perform_password_login(Session(t), InMemoryCredentialStore(),
                               registered_origin=REGISTERED, identity=IDENTITY, selectors=SEL)
    check("missing credential → 'failed'", r == "failed")
    check("  nothing typed when no credential exists", t.typed == [])

    # 6) failed keystroke → 'failed' (no value leak either way)
    t = FakeTransport(REGISTERED, ok=False)
    r = perform_password_login(Session(t), seeded_store(),
                               registered_origin=REGISTERED, identity=IDENTITY, selectors=SEL)
    check("failed keystroke → 'failed'", r == "failed")

    # 7) KeychainCredentialStore is READ-ONLY — no way to write a user password
    check("KeychainCredentialStore has NO put", not hasattr(KeychainCredentialStore, "put"))
    check("KeychainCredentialStore has NO set", not hasattr(KeychainCredentialStore, "set"))
    check("KeychainCredentialStore has NO set_key", not hasattr(KeychainCredentialStore, "set_key"))

    print()
    if FAILS:
        print(f"❌ {len(FAILS)} FAILED: {FAILS}")
        sys.exit(1)
    print("✅ password injector: gated, origin-bound, status-only (secret bypasses the model), fail-closed")


if __name__ == "__main__":
    main()
