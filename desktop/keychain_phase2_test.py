#!/usr/bin/env python3
"""Phase 2 test — the reversible mirror. Runs on DISPOSABLE material only.

Nothing here touches the real .db_key or vokter.db. It builds a throwaway data
dir with a disposable key and a disposable *encrypted* DB, points the real
orchestrator/verify code at it via monkeypatch + env, uses a throwaway keychain
service, and cleans everything up. Run under the SYSTEM python3 (has
secretstorage); it shells out to the venv python only to mint the encrypted DB
(needs sqlcipher3).

Proves:
  A. orchestrator.ensure_db_key() mirrors the file key into the keychain, yet
     still RETURNS the file key (keychain is only a mirror), and verify reports
     status=match.
  B. verify can FAIL: with the keychain holding a DIFFERENT value and no mirror
     in between, verify reports status=mismatch and a non-zero exit.
  C. Reversibility: with the keychain unavailable OR raising, ensure_db_key()
     still returns the file key and never throws — mirror is skipped/errored.
  D. Dev/Docker stay file-only: app/ never imports keychain.

Exit 0 = all passed.
"""
from __future__ import annotations

import os
import secrets
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
VENV_PY = HERE / "runtime" / "venv" / "bin" / "python"
REPO = HERE.parent

import keychain
import orchestrator

PASS = "OK  "
FAIL = "FAIL"
_ok = True


def check(label: str, condition: bool, detail: str = "") -> None:
    global _ok
    _ok = _ok and condition
    line = f"[{PASS if condition else FAIL}] {label}"
    if detail:
        line += f"  —  {detail}"
    print(line)


def mint_encrypted_db(db_path: Path, key: str) -> None:
    """Create a real, NON-EMPTY SQLCipher DB with `key`, using the venv python."""
    script = (
        "import sys, sqlcipher3.dbapi2 as s\n"
        "db, key = sys.argv[1], sys.argv[2]\n"
        "c = s.connect(db)\n"
        "c.execute(\"PRAGMA key='%s'\" % key.replace(\"'\", \"''\"))\n"
        "c.execute('CREATE TABLE t(x TEXT)')\n"
        "c.execute(\"INSERT INTO t VALUES('hello')\")\n"
        "c.commit(); c.close()\n"
    )
    subprocess.run([str(VENV_PY), "-c", script, str(db_path), key], check=True)


def run_verify(env_extra: dict[str, str]) -> tuple[int, str]:
    env = os.environ.copy()
    env.update(env_extra)
    p = subprocess.run(
        [sys.executable, str(HERE / "verify_encryption.py")],
        env=env, capture_output=True, text=True,
    )
    return p.returncode, p.stdout + p.stderr


def main() -> int:
    if not VENV_PY.exists():
        print(f"FAIL: need the venv python at {VENV_PY} to mint an encrypted DB.")
        return 2

    svc = "vokter-phase2-" + secrets.token_hex(6)      # throwaway keychain service
    tmp = Path(tempfile.mkdtemp(prefix="vokter-phase2-"))
    key_file = tmp / ".db_key"
    db_file = tmp / "vokter.db"
    disposable_key = secrets.token_urlsafe(32)

    # Repoint EVERYTHING at disposable material.
    saved_service = keychain.SERVICE
    saved_data_dir = orchestrator.DATA_DIR
    saved_dbkey_file = orchestrator.DBKEY_FILE
    saved_is_available = keychain.is_available
    keychain.SERVICE = svc
    orchestrator.DATA_DIR = tmp
    orchestrator.DBKEY_FILE = key_file

    print("== Phase 2 test (disposable material) ==")
    print(f"  tmp dir:  {tmp}")
    print(f"  keychain: {svc}/{keychain.KEY_NAME}\n")

    try:
        # Fixture: disposable 0600 key file + a real encrypted DB using it.
        fd = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(disposable_key)
        mint_encrypted_db(db_file, disposable_key)
        # Make sure the keychain starts clean for this throwaway service.
        keychain.delete_key(service=svc)

        verify_env = {
            "VOKTER_VERIFY_DB": str(db_file),
            "VOKTER_VERIFY_KEY_FILE": str(key_file),
            "VOKTER_VERIFY_KEYCHAIN_SERVICE": svc,
        }

        # --- A. Mirror on boot, but still return the file key -----------------
        returned = orchestrator.ensure_db_key()
        check("ensure_db_key() returns the FILE key (not the keychain)",
              returned == disposable_key,
              "file stays the source of truth")
        check("keychain now holds the mirrored key",
              keychain.get_key(service=svc) == disposable_key,
              "file→keychain copy landed")
        rc, out = run_verify(verify_env)
        check("verify reports status=match and PASSes", rc == 0 and "status=match" in out,
              f"rc={rc}")

        # --- B. verify can FAIL on a deliberate mismatch ----------------------
        # Put a DIFFERENT value in the keychain and DON'T mirror — verify must
        # catch it. (In the normal flow mirror reconciles, so this branch only
        # fires when mirror was skipped/failed — we force it here on purpose.)
        keychain.set_key("a-different-value-" + secrets.token_hex(4), service=svc)
        rc, out = run_verify(verify_env)
        check("verify DETECTS a mismatch (non-zero exit, status=mismatch)",
              rc != 0 and "status=mismatch" in out,
              f"rc={rc}")
        # Reconcile again via a real mirror, proving mirror fixes the drift.
        st = keychain.mirror(disposable_key, service=svc)
        check("mirror() reconciles the drift back to a match",
              st == keychain.MIRROR_DONE and keychain.get_key(service=svc) == disposable_key,
              f"status={st}")

        # --- C. Reversibility: keychain unavailable / raising -----------------
        keychain.is_available = lambda **k: False
        st = keychain.mirror(disposable_key, service=svc)
        returned2 = orchestrator.ensure_db_key()
        check("keychain UNAVAILABLE → mirror skipped, boot still returns file key",
              st == keychain.MIRROR_SKIPPED and returned2 == disposable_key,
              f"status={st}")

        def _boom(**k):
            raise RuntimeError("simulated keychain blow-up")
        keychain.is_available = _boom
        st = keychain.mirror(disposable_key, service=svc)
        returned3 = orchestrator.ensure_db_key()
        check("keychain RAISING → mirror error is swallowed, boot unaffected",
              st == keychain.MIRROR_ERROR and returned3 == disposable_key,
              f"status={st}")
        keychain.is_available = saved_is_available

        # --- D. Dev/Docker stay file-only: app/ never imports keychain --------
        app_hits = subprocess.run(
            ["grep", "-rn", "keychain", str(REPO / "app")],
            capture_output=True, text=True,
        ).stdout.strip()
        check("app/ (dev + Docker path) never references keychain",
              app_hits == "", app_hits or "no references")

        return 0 if _ok else 1
    finally:
        # Cleanup: throwaway keychain slot, temp dir, restore patched globals.
        try:
            keychain.is_available = saved_is_available
            keychain.delete_key(service=svc)
        except Exception:
            pass
        keychain.SERVICE = saved_service
        orchestrator.DATA_DIR = saved_data_dir
        orchestrator.DBKEY_FILE = saved_dbkey_file
        for p in (db_file, key_file):
            try:
                p.unlink()
            except FileNotFoundError:
                pass
        try:
            tmp.rmdir()
        except OSError:
            pass
        leftover = keychain.get_key(service=svc)
        print(f"\ncleanup: throwaway keychain slot now → {leftover!r} "
              f"(None = clean); temp dir removed: {not tmp.exists()}")


if __name__ == "__main__":
    rc = main()
    print("\n" + ("ALL CHECKS PASSED" if rc == 0 else "SOME CHECKS FAILED"))
    sys.exit(rc)
