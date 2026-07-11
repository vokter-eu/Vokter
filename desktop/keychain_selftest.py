#!/usr/bin/env python3
"""Self-test for keychain.py — Phase 3.2, step 1. Touches NO real state.

Everything runs against a THROWAWAY service name (vokter-selftest-<rand>), never
the real "vokter"/"db_key" slot, and every probe item self-deletes. It asserts,
before and after, that the real slot is left exactly as found — so running this
can never affect your database key. It talks only to GNOME Keyring (pinned); it
never reaches for KWallet, and never unlocks anything, so it raises no dialog.

What it proves:
  1. What the module talks to.
  2. is_available() is True on a working, unlocked keychain (real round-trip).
  3. set → get (equal) → delete → get (None) behave on a throwaway slot.
  4. UNAVAILABLE != EMPTY: with D-Bus made to fail, is_available() is False and
     get_key() is None (couldn't-ask), not mistaken for "no key yet".
  5. LOCKED != EMPTY and raises NO dialog: with the collection forced locked,
     is_available() is False and unlock() is never called.
  6. The real vokter/db_key slot is untouched, and no probe residue remains.

Exit code 0 = all checks passed.
"""
from __future__ import annotations

import secrets
import sys

import keychain

PASS = "OK  "
FAIL = "FAIL"


class _FakeLockedCollection:
    """Stands in for a present-but-locked collection. is_locked() → True, and it
    has NO unlock() — so if the code ever tried to unlock, it would crash loudly
    instead of silently popping a dialog."""

    def is_locked(self):
        return True


def main() -> int:
    ok = True

    def check(label: str, condition: bool, detail: str = "") -> None:
        nonlocal ok
        mark = PASS if condition else FAIL
        ok = ok and condition
        line = f"[{mark}] {label}"
        if detail:
            line += f"  —  {detail}"
        print(line)

    print("== keychain.py self-test (Phase 3.2 step 1) ==\n")
    print(f"talks to: {keychain.active_backend_name()}\n")

    svc = "vokter-selftest-" + secrets.token_hex(6)
    print(f"throwaway service: {svc}\n")

    # --- Guard: snapshot the REAL slot to prove we never touch it. -------------
    real_before = keychain.get_key()
    print(f"real slot ({keychain.SERVICE}/{keychain.KEY_NAME}) before: "
          f"{'<present>' if real_before is not None else '<empty/none>'}\n")

    # --- 1. Availability probe (positive, self-deleting round-trip) -----------
    check("is_available() is True on this working, unlocked keychain",
          keychain.is_available() is True,
          "write→read-back→delete of an ephemeral probe succeeded")

    # --- 2. Full round-trip on a throwaway slot -------------------------------
    name = "probe-key"
    value = "throwaway-" + secrets.token_urlsafe(16)
    try:
        keychain.set_key(value, service=svc, name=name)
        got = keychain.get_key(service=svc, name=name)
        check("stored value reads back identical", got == value,
              f"wrote {len(value)} chars, read {len(got) if got else 0}")
        deleted = keychain.delete_key(service=svc, name=name)
        check("delete_key() reports success", deleted is True)
        after = keychain.get_key(service=svc, name=name)
        check("value is gone after delete", after is None, f"get→{after!r}")
    finally:
        # Belt and braces: never leave a throwaway item behind, even on failure.
        keychain.delete_key(service=svc, name=name)

    # --- 3. UNAVAILABLE != EMPTY (D-Bus made to fail) -------------------------
    real_dbus_init = keychain.secretstorage.dbus_init

    def broken_dbus_init(*a, **k):
        raise ConnectionError("simulated: no D-Bus / no session bus")

    keychain.secretstorage.dbus_init = broken_dbus_init
    try:
        check("is_available() is False when the keychain is unreachable",
              keychain.is_available() is False,
              "a down keychain must NOT look like 'no key yet'")
        check("get_key() returns None on an unreachable keychain (ambiguous None)",
              keychain.get_key(service=svc, name=name) is None,
              "None here means 'couldn't ask', NOT 'no key' — hence is_available()")
    finally:
        keychain.secretstorage.dbus_init = real_dbus_init

    # --- 4. LOCKED != EMPTY, and raises NO dialog -----------------------------
    real_default_collection = keychain._default_collection

    def locked_collection(conn):
        return _FakeLockedCollection()

    keychain._default_collection = locked_collection
    try:
        # If the code tried to unlock, _FakeLockedCollection has no unlock() and
        # would raise → is_available() would still be False, never a dialog.
        check("is_available() is False when the collection is locked",
              keychain.is_available() is False,
              "locked = unavailable; unlock() is never called, so no dialog")
    finally:
        keychain._default_collection = real_default_collection

    # --- 5. Prove the REAL slot was never touched, and no residue remains ------
    real_after = keychain.get_key()
    check("real vokter/db_key slot unchanged by the whole test",
          real_after == real_before,
          f"before={real_before!r} after={real_after!r}")
    check("no throwaway probe residue left in the keychain",
          keychain.get_key(service=svc, name=name) is None)

    print()
    if ok:
        print("ALL CHECKS PASSED — pinned GNOME-Keyring tools + availability "
              "probe work, no dialog possible, and no real state was touched.")
        return 0
    print("SOME CHECKS FAILED — see above.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
