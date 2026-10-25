"""Tier-3 Phase 1 — the DETERMINISTIC INJECTOR skeleton (INNOCUOUS fields only, NO secrets).

Asserts the mechanism's structural guarantees without a live browser (a fake CDP transport):
  * an innocuous fill types the value via the transport and returns only 'succeeded'|'failed' —
    the value is NEVER in the return (the property the secret phases inherit: value bypasses model);
  * a SECRET field kind (password/pan/cvv/…) is REFUSED — the transport is never touched, so the
    value never even reaches the keystroke path;
  * an unknown kind is refused (deny-by-default);
  * click returns status only.

Run:  python3 tests/injector_test.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import interactive_browser as ib
from interactive_browser import InjectorError, Session

FAILS = []
def check(desc, cond):
    print(("PASS " if cond else "FAIL ") + desc)
    if not cond:
        FAILS.append(desc)


class FakeTransport:
    """Records what the injector drives, so we can assert the mechanism without a browser."""
    def __init__(self, ok=True):
        self.ok = ok
        self.typed = []     # (selector, value) the code actually tried to type
        self.clicked = []
        self.navigated = []
    def navigate(self, url): self.navigated.append(url); return self.ok
    def type_into(self, selector, value): self.typed.append((selector, value)); return self.ok
    def click(self, selector): self.clicked.append(selector); return self.ok
    def read_text(self): return "page text"


def main():
    t = FakeTransport(ok=True)
    s = Session(t)

    # innocuous fill → status only; value reached the transport (code), NOT the return
    r = s.fill("#search", "winter boots", kind="search")
    check("innocuous fill returns status 'succeeded'", r == "succeeded")
    check("value handed to transport (code types it)", t.typed == [("#search", "winter boots")])
    check("return is STATUS ONLY — value never surfaced", "winter boots" not in r)

    # failed keystroke → 'failed' (still no value leak)
    s2 = Session(FakeTransport(ok=False))
    check("failed fill returns 'failed'", s2.fill("#q", "x", kind="text") == "failed")

    # SECRET kinds are refused BEFORE any transport call — the value never reaches a keystroke
    for secret_kind in ("password", "pan", "cvv", "otp", "credential"):
        tt = FakeTransport()
        ss = Session(tt)
        try:
            ss.fill("#pw", "hunter2", kind=secret_kind)
            check(f"secret kind '{secret_kind}' refused", False)
        except InjectorError:
            check(f"secret kind '{secret_kind}' refused", True)
            check(f"  transport NEVER touched for '{secret_kind}'", tt.typed == [])

    # unknown kind → deny-by-default
    try:
        Session(FakeTransport()).fill("#x", "v", kind="mystery")
        check("unknown kind refused", False)
    except InjectorError:
        check("unknown kind refused", True)

    # click returns status only
    check("click returns 'succeeded'", s.click("#submit") == "succeeded")

    # the secret kinds really are absent from the allowed set (belt-and-suspenders)
    check("INNOCUOUS ∩ SECRET is empty", not (ib.INNOCUOUS_KINDS & ib.SECRET_KINDS))

    print()
    if FAILS:
        print(f"❌ {len(FAILS)} FAILED: {FAILS}")
        sys.exit(1)
    print("✅ injector skeleton: innocuous-only, status-only (value bypasses the model), deny-by-default")


if __name__ == "__main__":
    main()
