#!/usr/bin/env python3
"""Prove the database on disk is actually encrypted — not silently plaintext.

config.py falls back to an UNencrypted SQLite file (with only a printed warning)
if sqlcipher3 cannot be imported. So "the app started and chatted" is NOT proof
that principle #3 (real deletion / your data stays yours, encrypted) holds. This
checks the bytes on disk: a real SQLCipher file has NO readable header, while a
plain SQLite file starts with the ASCII magic "SQLite format 3\\x00".
"""
import sys
from pathlib import Path

RUNTIME = Path(__file__).resolve().parent / "runtime"
DB = RUNTIME / "data" / "vokter.db"

SQLITE_MAGIC = b"SQLite format 3\x00"


def main() -> int:
    if not DB.exists():
        print(f"FAIL: {DB} does not exist yet — run the orchestrator first.")
        return 2
    head = DB.read_bytes()[:16]
    if head == SQLITE_MAGIC:
        print("FAIL: database is PLAINTEXT (starts with the SQLite magic header).")
        print("      sqlcipher did not load — encryption is NOT active.")
        return 1
    print("OK: database header is not plaintext SQLite — SQLCipher is active.")
    print(f"    first 16 bytes: {head!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
