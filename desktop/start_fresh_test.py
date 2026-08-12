"""Safety matrix for the [2] "start fresh" key cell + a regression that the
guardrail still halts exactly as before without the flag.

This is the EVIDENCE for the invariant: start-fresh only ever CREATES a key file
in the resolved data dir (O_EXCL), never overwrites/deletes, never touches the
keychain or any prior data. Fully dev-side — no VM, no display, no real keychain.
Run: `python3 start_fresh_test.py`.
"""
import contextlib
import hashlib
import io
import os
import pathlib
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import orchestrator as o
import keysource as ks
import datadir

# Prove we never touch the REAL dev key file (orchestrator resolved it at import).
REAL_DBKEY = pathlib.Path(o.DBKEY_FILE)
_real_before = REAL_DBKEY.read_bytes() if REAL_DBKEY.exists() else None

# Tripwire: start-fresh must NEVER seed/write the keychain (an UNREACHABLE slot
# may hold the real user's key). If any tested path calls it, the test explodes.
def _tripwire(*a, **k):
    raise AssertionError("start-fresh touched the keychain (_seed_keychain called)")
o._seed_keychain = _tripwire


def sha(p):
    p = pathlib.Path(p)
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None


def use_tmp(tmp):
    """Point the orchestrator's key file into a throwaway dir (never dev)."""
    o.DATA_DIR = pathlib.Path(tmp)
    o.DBKEY_FILE = pathlib.Path(tmp) / ".db_key"


def facts(file_state, file_key, kc_state, kc_key, db_present=False):
    return dict(file_state=file_state, file_key=file_key,
                kc_state=kc_state, kc_key=kc_key, db_present=db_present)


def candidate(tmp):
    """A fake prior-Vokter DB in another location; must survive byte-identical."""
    c = pathlib.Path(tmp) / "prior" / "vokter.db"
    c.parent.mkdir(parents=True, exist_ok=True)
    c.write_bytes(b"THE USER'S REAL OLD DATABASE - MUST NOT BE TOUCHED")
    return c, sha(c)


print("=== MATRIX: _start_fresh_key is create-only ===")

# 1. Readable file key present → reuse (idempotent, no rewrite).
with tempfile.TemporaryDirectory() as tmp:
    use_tmp(tmp)
    o.DBKEY_FILE.write_text("K1"); os.chmod(o.DBKEY_FILE, 0o600)
    kb = sha(o.DBKEY_FILE); c, ch = candidate(tmp)
    key = o._start_fresh_key(facts(ks.FILE_PRESENT, "K1", ks.KC_HAS_KEY, "KCX"))
    assert key == "K1"
    assert sha(o.DBKEY_FILE) == kb, "reuse must not rewrite the key file"
    assert sha(c) == ch
    print("  1 reuse existing file key (idempotent): reused, file+candidate byte-identical ✓")

# 2. No file, keychain HAS_KEY → adopt it (read keychain, write file O_EXCL).
with tempfile.TemporaryDirectory() as tmp:
    use_tmp(tmp)
    c, ch = candidate(tmp)
    key = o._start_fresh_key(facts(ks.FILE_ABSENT, None, ks.KC_HAS_KEY, "KC_REAL"))
    assert key == "KC_REAL"
    assert o.DBKEY_FILE.read_text() == "KC_REAL"
    assert oct(o.DBKEY_FILE.stat().st_mode & 0o777) == "0o600"
    assert sha(c) == ch
    print("  2 adopt keychain key (no file): wrote 0600 file, candidate byte-identical ✓")

# 3. No file, keychain UNREACHABLE → mint a NEW key to file (the only new cell).
with tempfile.TemporaryDirectory() as tmp:
    use_tmp(tmp)
    c, ch = candidate(tmp)
    key = o._start_fresh_key(facts(ks.FILE_ABSENT, None, ks.KC_UNAVAILABLE, None))
    assert key and len(key) >= 20 and o.DBKEY_FILE.read_text() == key
    assert sha(c) == ch
    print("  3 UNREACHABLE (no file): minted fresh key to file, candidate byte-identical ✓")

# 4. No file, keychain proven empty → mint a new key.
with tempfile.TemporaryDirectory() as tmp:
    use_tmp(tmp)
    c, ch = candidate(tmp)
    key = o._start_fresh_key(facts(ks.FILE_ABSENT, None, ks.KC_EMPTY, None))
    assert key and o.DBKEY_FILE.read_text() == key
    assert sha(c) == ch
    print("  4 KC_EMPTY (no file): minted fresh key, candidate byte-identical ✓")

# 5. UNREADABLE file present → REFUSE (None), never overwrite it (may be real).
with tempfile.TemporaryDirectory() as tmp:
    use_tmp(tmp)
    o.DBKEY_FILE.write_text("REAL_KEY_WE_CANNOT_READ"); before = sha(o.DBKEY_FILE)
    c, ch = candidate(tmp)
    key = o._start_fresh_key(facts(ks.FILE_UNREADABLE, None, ks.KC_HAS_KEY, "KCX"))
    assert key is None, "must refuse rather than overwrite an unreadable key file"
    assert sha(o.DBKEY_FILE) == before, "unreadable key file must be byte-identical (preserved)"
    assert sha(c) == ch
    print("  5 UNREADABLE file: refused (None), existing key file byte-identical ✓")

# 6. Resolved data dir does NOT exist yet (real first-boot on a clean machine) →
#    the fresh path must create it and still write the key, not crash on a missing
#    parent. (The bug tempfile-backed cases can't surface: they always pre-exist.)
with tempfile.TemporaryDirectory() as tmp:
    missing = pathlib.Path(tmp) / "does" / "not" / "exist"
    o.DATA_DIR = missing
    o.DBKEY_FILE = missing / ".db_key"
    assert not missing.exists(), "precondition: dir must be absent"
    key = o._start_fresh_key(facts(ks.FILE_ABSENT, None, ks.KC_UNAVAILABLE, None))
    assert key and o.DBKEY_FILE.read_text() == key, "must create the dir and write the key"
    print("  6 resolved dir ABSENT (clean first boot): created dir, wrote key, no crash ✓")

print("\n=== REGRESSION + fresh integration at the guardrail gate ===")


def triggered_guard(tmp):
    return datadir.Guardrail(
        triggered=True, resolved_dir=pathlib.Path(tmp), resolved_has_db=False,
        keychain=datadir.KeychainState.HAS_KEY,
        candidates=[("carpeta X", pathlib.Path(tmp) / "prior")],
    )


class DieCalled(Exception):
    pass

def _die(msg):
    raise DieCalled(msg)
o.die = _die

# REGRESSION: WITHOUT the flag → halts EXACTLY as today (message unchanged), emits
# the structured [guardrail] line for the UI, and creates NO key file.
with tempfile.TemporaryDirectory() as tmp:
    use_tmp(tmp)
    os.environ.pop(o.START_FRESH_ENV, None)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        try:
            o._handle_guardrail(triggered_guard(tmp), facts(ks.FILE_ABSENT, None, ks.KC_HAS_KEY, "KCX"))
            raise SystemExit("BUG: should have died")
        except DieCalled as e:
            diemsg = str(e)
    out = buf.getvalue()
    assert "refusing to start an EMPTY Vokter" in diemsg, diemsg
    assert o.GUARDRAIL_PREFIX in out, "structured [guardrail] line must be emitted"
    assert '"keychain":"has_key"' in out, "structured facts must carry the keychain state"
    assert "VOKTER NO ARRANCA EN VACÍO" in out, "human guardrail message must still be logged"
    assert not o.DBKEY_FILE.exists(), "halt must create NO key file"
    print("  regression: no flag → same die message, [guardrail] emitted, zero key file ✓")

# FRESH: WITH the flag → proceeds create-only, returns a usable key, no die.
with tempfile.TemporaryDirectory() as tmp:
    use_tmp(tmp)
    os.environ[o.START_FRESH_ENV] = "1"
    try:
        key = o._handle_guardrail(triggered_guard(tmp), facts(ks.FILE_ABSENT, None, ks.KC_HAS_KEY, "KC_REAL"))
    finally:
        os.environ.pop(o.START_FRESH_ENV, None)
    assert key == "KC_REAL", "fresh path returns a usable key (adopted keychain key)"
    assert o.DBKEY_FILE.exists()
    print("  fresh: flag → proceeds create-only, usable key, no die ✓")

# The real dev key file must be exactly as we found it.
_real_after = REAL_DBKEY.read_bytes() if REAL_DBKEY.exists() else None
assert _real_after == _real_before, "the REAL dev key file was touched!"
print("\n  dev key file untouched (byte-identical) ✓")
print("ALL GREEN — start-fresh safety matrix + regression")
