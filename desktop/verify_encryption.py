#!/usr/bin/env python3
"""Prove the database on disk is actually encrypted — and (Phase 2) that the
file key and the OS-keychain mirror agree.

config.py falls back to an UNencrypted SQLite file (with only a printed warning)
if sqlcipher3 cannot be imported. So "the app started and chatted" is NOT proof
that principle #3 (real deletion / your data stays yours, encrypted) holds. This
checks the bytes on disk: a real SQLCipher file has NO readable header, while a
plain SQLite file starts with the ASCII magic "SQLite format 3\\x00".

Phase 2 adds a second check: if the DB key has been mirrored into the OS
keychain, the mirror MUST equal the file key. A mismatch is a real problem (a
later phase would read the wrong key); "no keychain" or "not mirrored yet" are
fine and never fail — the file stays the source of truth.

Overridable via env (used by the disposable Phase-2 test; defaults are the real
desktop paths):
  VOKTER_VERIFY_DB                 → path to the DB file
  VOKTER_VERIFY_KEY_FILE           → path to the .db_key file
  VOKTER_VERIFY_KEYCHAIN_SERVICE   → keychain service name
  VOKTER_VERIFY_KEYCHAIN_NAME      → keychain key name
"""
import os
import sys
from pathlib import Path

RUNTIME = Path(__file__).resolve().parent / "runtime"

DB = Path(os.environ.get("VOKTER_VERIFY_DB", str(RUNTIME / "data" / "vokter.db")))
KEY_FILE = Path(os.environ.get("VOKTER_VERIFY_KEY_FILE", str(RUNTIME / "data" / ".db_key")))

SQLITE_MAGIC = b"SQLite format 3\x00"


def check_db_encrypted(db: Path) -> tuple[bool, str]:
    """(ok, message). ok=False only if the DB exists and is plaintext SQLite."""
    if not db.exists():
        return False, f"{db} does not exist yet — run the orchestrator first."
    head = db.read_bytes()[:16]
    if head == SQLITE_MAGIC:
        return False, ("database is PLAINTEXT (SQLite magic header) — "
                       "sqlcipher did not load, encryption is NOT active.")
    return True, f"database header is not plaintext SQLite — SQLCipher is active. head={head!r}"


def check_key_match(key_file: Path, service: str, name: str) -> tuple[str, str]:
    """Compare the file key against the keychain mirror.

    Returns (status, message). status ∈:
      'match'          → keychain mirror equals the file key (good)
      'no-mirror'      → keychain reachable but has no key yet (fine, not failed)
      'no-keychain'    → keychain unavailable/uncheckable (fine, file-only mode)
      'no-file'        → the key file itself is missing (can't compare)
      'mismatch'       → keychain holds a DIFFERENT key (FAIL — dangerous)
    Only 'mismatch' is a hard failure. Normalization (.strip()) matches how
    ensure_db_key() reads the file, so a stray newline never fakes a mismatch.
    """
    if not key_file.exists():
        return "no-file", f"key file {key_file} is missing — nothing to compare."
    file_key = key_file.read_text().strip()

    try:
        import keychain
    except Exception as exc:  # secretstorage missing etc. — encryption check still ran
        return "no-keychain", f"keychain module not importable ({exc!r}); file-only."

    if not keychain.is_available():
        return "no-keychain", "keychain unavailable — mirror not checkable (file-only is fine)."

    kc_key = keychain.get_key(service=service, name=name)
    if kc_key is None:
        return "no-mirror", "keychain reachable but holds no mirror yet (fine)."
    if kc_key == file_key:
        return "match", "keychain mirror EQUALS the file key ✓"
    return "mismatch", "keychain mirror DIFFERS from the file key — DANGER."


def main() -> int:
    service = os.environ.get("VOKTER_VERIFY_KEYCHAIN_SERVICE", "vokter")
    name = os.environ.get("VOKTER_VERIFY_KEYCHAIN_NAME", "db_key")

    print("== verify_encryption (Phase 2) ==")
    print(f"  DB:       {DB}")
    print(f"  key file: {KEY_FILE}")
    print(f"  keychain: {service}/{name}\n")

    rc = 0

    enc_ok, enc_msg = check_db_encrypted(DB)
    print(f"[{'OK  ' if enc_ok else 'FAIL'}] encryption: {enc_msg}")
    if not enc_ok:
        rc = 1

    status, msg = check_key_match(KEY_FILE, service, name)
    # Only a real mismatch fails the run; everything else is informational.
    failed = status == "mismatch"
    mark = "FAIL" if failed else ("OK  " if status == "match" else "----")
    print(f"[{mark}] key match: {msg}  (status={status})")
    if failed:
        rc = 1

    print()
    print("RESULT:", "PASS" if rc == 0 else "FAIL")
    return rc


if __name__ == "__main__":
    sys.exit(main())
