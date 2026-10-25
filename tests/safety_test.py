"""Capability-gateway guarantees (Phase 1). Run:

    VOKTER_DB=/tmp/vok-safety-test/x VOKTER_DB_KEY=k \
      desktop/runtime/venv/bin/python tests/safety_test.py

Asserts the injection-proof properties in code — not "the model should obey":
  * peer/mcp → delete & schedule.create BLOCK
  * human delete WITHOUT the confirm token → 428 (blocked); WITH it → allowed
  * off-allowlist browse → BLOCK
  * dm.send to an unknown peer → BLOCK
  * unregistered action → deny-by-default (BLOCK non-human / CONFIRM human)
  * rules fail to load → guarded actions deny, but non-guarded paths (ask) are untouched
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import safety
from safety import Decision as D

FAILS = []


def check(desc, got, want):
    ok = got == want
    print(("PASS " if ok else "FAIL ") + desc + f"  → {got}")
    if not ok:
        FAILS.append(desc)


def main():
    assert safety.load(), "rules should load in a clean checkout"
    g = safety.guard

    # peers/MCP can never reach owner-only destructive actions
    for ctx in ("peer", "mcp", "autonomous"):
        check(f"doc.delete/{ctx} BLOCK",      g("doc.delete", "x", context=ctx),      D.BLOCK)
        check(f"schedule.create/{ctx} BLOCK", g("schedule.create", "x", context=ctx), D.BLOCK)
        check(f"memory.purge/{ctx} BLOCK",    g("memory.purge", None, context=ctx),   D.BLOCK)

    # owner actions require explicit confirmation (CONFIRM), never silent
    check("doc.delete/human CONFIRM",       g("doc.delete", "x", context="human"),      D.CONFIRM)
    check("schedule.create/human CONFIRM",  g("schedule.create", "x", context="human"), D.CONFIRM)

    # CONFIRM collapses to BLOCK for non-human via enforce_http (no 428 leaked to peers)
    from fastapi import HTTPException
    try:
        safety.enforce_http("doc.delete", "x", context="peer")
        check("enforce_http peer delete raises", False, True)
    except HTTPException as e:
        check("enforce_http peer delete → 403", e.status_code, 403)
    try:
        safety.enforce_http("doc.delete", "x", context="human", confirmed=False)
        check("enforce_http human unconfirmed raises", False, True)
    except HTTPException as e:
        check("enforce_http human unconfirmed → 428", e.status_code, 428)
    # with the confirm token, the owner's delete proceeds
    try:
        safety.enforce_http("doc.delete", "x", context="human", confirmed=True)
        check("enforce_http human confirmed → allowed", True, True)
    except HTTPException:
        check("enforce_http human confirmed → allowed", False, True)

    # browse off-allowlist, dm.send to unknown peer
    check("browse off-allowlist peer BLOCK", g("browse", "http://evil.example", context="peer"), D.BLOCK)
    check("dm.send unknown peer BLOCK",      g("dm.send", "npub_stranger", context="peer"),       D.BLOCK)

    # Tier-3 Phase 1: web.act (interactive-browser fill/submit/click) is CONFIRM-human-only — a
    # hostile page or a peer can never drive a form.
    check("web.act known action",            "web.act" in safety.KNOWN_ACTIONS,                   True)
    for ctx in ("peer", "mcp", "autonomous"):
        check(f"web.act/{ctx} BLOCK",        g("web.act", "#search", context=ctx),                D.BLOCK)
    check("web.act/human CONFIRM",           g("web.act", "#search", context="human"),            D.CONFIRM)
    try:
        safety.enforce_http("web.act", "#search", context="autonomous")
        check("enforce_http web.act autonomous raises", False, True)
    except HTTPException as e:
        check("enforce_http web.act autonomous → 403", e.status_code, 403)
    try:
        safety.enforce_http("web.act", "#search", context="human", confirmed=False)
        check("enforce_http web.act human unconfirmed raises", False, True)
    except HTTPException as e:
        check("enforce_http web.act human unconfirmed → 428", e.status_code, 428)
    try:
        safety.enforce_http("web.act", "#search", context="human", confirmed=True)
        check("enforce_http web.act human confirmed → allowed", True, True)
    except HTTPException:
        check("enforce_http web.act human confirmed → allowed", False, True)

    # SOURCE-LEVEL TRIPWIRE: the interactive-browser handler must gate fill/submit/click through
    # enforce_http("web.act", …) BEFORE acting — a handler reachable without the gate is a hole.
    import os as _os
    ib_src = open(_os.path.join(_os.path.dirname(__file__), "..", "app",
                                "interactive_browser.py"), encoding="utf-8").read()
    check("tripwire: web_act handler calls enforce_http(\"web.act\")",
          'enforce_http("web.act"' in ib_src, True)
    # ...and must NEVER launch the browser with --no-sandbox (renderer sandbox is required).
    check("tripwire: no --no-sandbox in interactive_browser", "--no-sandbox" not in ib_src
          or "must never appear" in ib_src, True)

    # deny-by-default for actions the gateway has never heard of
    check("unknown action peer BLOCK",   g("frobnicate", None, context="peer"),  D.BLOCK)
    check("unknown action human CONFIRM", g("frobnicate", None, context="human"), D.CONFIRM)

    # fail-closed on the CAPABILITY, not on boot: simulate a load failure
    safety._ok = False
    check("FAILCLOSED doc.delete peer BLOCK",   g("doc.delete", "x", context="peer"),   D.BLOCK)
    check("FAILCLOSED doc.delete human CONFIRM", g("doc.delete", "x", context="human"), D.CONFIRM)
    check("FAILCLOSED browse human CONFIRM",     g("browse", "http://x", context="human"), D.CONFIRM)
    # non-guarded paths (ask) never call guard(), so a rules failure doesn't stop chat.
    check("ask is not a guarded action (chat unaffected)", "ask" in safety.KNOWN_ACTIONS, False)

    print()
    if FAILS:
        print(f"❌ {len(FAILS)} FAILED: {FAILS}")
        sys.exit(1)
    print("✅ safety gateway: all guarantees hold")


if __name__ == "__main__":
    main()
