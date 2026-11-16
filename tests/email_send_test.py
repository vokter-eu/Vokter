"""Tier-3 Phase 3 — outbound email: the per-task unlinkable alias, the disclosure-whitelist body builder,
the confirm-integrity token, and the keychain transport (credential never surfaced).

Asserts (no network, no real SMTP, no keychain):
  * the per-task alias is deterministic within a task, unlinkable across tasks, never the social npub,
  * the alias↔task map reply-routes (task_for_alias) — the bidirectional hook,
  * build_body INCLUDES only whitelisted fields (fails closed; blocked classes dropped),
  * confirm-integrity: a substituted recipient/body no longer matches the token,
  * the transport store returns NON-SECRET status only (never the password),
  * the gate helper is default-OFF.

Run:  VOKTER_DB=/tmp/email_unit.db uv run --python 3.12 --with fastapi --with pydantic python tests/email_send_test.py
"""
import os
import sys
import tempfile

# a throwaway DB BEFORE importing anything that binds config.DB_PATH (the alias map needs a DB).
os.environ.setdefault("VOKTER_DB", os.path.join(tempfile.mkdtemp(prefix="vkt_email_unit_"), "probe.db"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import email_send as es

FAILS = []
def check(d, c):
    print(("PASS " if c else "FAIL ") + d)
    if not c:
        FAILS.append(d)

SECRET = "SMTP-password-never-to-model-abc"
DOMAIN = "unit-mail.example"
LEAK = "LEAKED-MEMORY-never-in-body"


def main():
    # gate default-OFF
    os.environ.pop("VOKTER_EMAIL_SEND_GATE", None)
    check("gate default-OFF", es.email_gate_ok() is False)
    os.environ["VOKTER_EMAIL_SEND_GATE"] = "passed"
    check("gate ON when passed", es.email_gate_ok() is True)
    os.environ.pop("VOKTER_EMAIL_SEND_GATE", None)

    # transport store — provision in memory; status NEVER carries the password
    es._transport = es.InMemoryTransport()
    es._transport.put({"host": "mx", "port": 587, "username": "u", "password": SECRET,
                       "from_domain": DOMAIN, "starttls": True})
    st = es._transport.status()
    check("transport status has from_domain", st.get("from_domain") == DOMAIN)
    check("transport status NEVER carries the password", SECRET not in str(st))
    check("transport get() DOES hold the password (internal only)", es._transport.get().get("password") == SECRET)

    # per-task alias: deterministic within a task, unlinkable across tasks, not the social npub
    a1 = es.ensure_alias("task-1"); a1b = es.ensure_alias("task-1"); a2 = es.ensure_alias("task-2")
    check("alias deterministic within a task", a1 == a1b)
    check("alias unlinkable across tasks", a1 != a2)
    check("alias is on Vokter's sending domain", a1.endswith("@" + DOMAIN))
    check("alias is NOT an npub / social identity", not a1.startswith("npub"))

    # reply-routing (bidirectional): the alias maps back to its task
    check("task_for_alias routes a reply back to its task", es.task_for_alias(a1) == "task-1")
    check("task_for_alias unknown → None", es.task_for_alias("nobody@" + DOMAIN) is None)

    # disclosure whitelist — INCLUSION only; non-whitelisted + blocked-class fields are absent (fails closed)
    disc = {"greeting": "Hello", "party_size": "2", "leaked": LEAK, "identity": "passport-xyz"}
    body = es.build_body(disc, ["greeting", "party_size", "identity"])  # 'leaked' omitted; 'identity' blocked
    check("body includes whitelisted fields", "greeting: Hello" in body and "party_size: 2" in body)
    check("body EXCLUDES non-whitelisted field (fails closed)", LEAK not in body)
    check("body EXCLUDES blocked-class field even if whitelisted", "passport" not in body)

    # confirm-integrity token: binds alias+recipient+subject+body
    tok = es._confirm_token(a1, "m@x.example", "S", body)
    check("token verifies for the exact message", es._verify_confirm_token(tok, a1, "m@x.example", "S", body))
    check("token REJECTS a substituted recipient", not es._verify_confirm_token(tok, a1, "attacker@evil", "S", body))
    check("token REJECTS a substituted body", not es._verify_confirm_token(tok, a1, "m@x.example", "S", body + "!"))
    check("empty token is rejected", not es._verify_confirm_token("", a1, "m@x.example", "S", body))

    # one-action transport removal
    check("transport delete() removes it", es._transport.delete() and es._transport.get() is None)

    print()
    if FAILS:
        print(f"❌ {len(FAILS)} FAILED: {FAILS}"); sys.exit(1)
    print("✅ email.send: per-task unlinkable alias + reply-routing, whitelist fails closed, "
          "confirm-integrity holds, transport credential stays in the store")


if __name__ == "__main__":
    main()
