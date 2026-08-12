#!/usr/bin/env python3
"""Phase 3.2 · step 3 — tests for the key-source decision. DISPOSABLE only.

Two parts, nothing real is touched:

  PART 1 — the decision table (pure): every cell of the 5 situations, plus the
  mint-gate negatives and the kill switch. `opens_db` is a fake that also
  ASSERTS it is never called in the shortcut cases (1/2/3/5) — proving those
  decide without opening the DB. No files, no keychain, no I/O at all.

  PART 2 — the real validator (`key_opens_db`): mints a THROWAWAY encrypted DB
  with the venv python and checks the right key opens it and a wrong key does
  not. If the frozen binary is present, its `--verify-key` mode is exercised as
  an informational cross-check (does not gate the result).

Exit 0 = all gating checks passed.
"""
from __future__ import annotations

import os
import secrets
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import keysource as ks
from keysource import (FILE_ABSENT, FILE_PRESENT, FILE_UNREADABLE, KC_EMPTY,
                       KC_HAS_KEY, KC_UNAVAILABLE, SRC_FILE, SRC_KEYCHAIN,
                       SRC_MINTED)

HERE = Path(__file__).resolve().parent
VENV_PY = HERE / "runtime" / "venv" / "bin" / "python"
FROZEN_BIN = HERE / "freeze" / "dist" / "vokter-backend" / "vokter-backend"

_ok = True


def check(label: str, condition: bool, detail: str = "") -> None:
    global _ok
    _ok = _ok and condition
    line = f"[{'OK  ' if condition else 'FAIL'}] {label}"
    if detail:
        line += f"  —  {detail}"
    print(line)


class _Opener:
    """Fake opens_db. `truth` maps a key → does-it-open. Records every call so a
    test can assert the shortcut cases never touch it."""

    def __init__(self, truth: dict[str, bool] | None = None):
        self.truth = truth or {}
        self.calls: list[str] = []

    def __call__(self, key: str) -> bool:
        self.calls.append(key)
        return self.truth.get(key, False)


def d(opener: _Opener | None = None, **facts) -> ks.Decision:
    """decide() with sensible defaults; pass an opener to arbitrate 4a/4b."""
    opener = opener or _Opener()
    base = dict(file_state=FILE_PRESENT, file_key="FILEKEY", db_present=True,
                kc_state=KC_UNAVAILABLE, kc_key=None, opens_db=opener, override=None)
    base.update(facts)
    return ks.decide(**base)


def part1_decision_table() -> None:
    print("== PART 1 — decision table (pure, disposable) ==\n")

    # ---- SITUATION 1: keychain key == file key → use keychain, no opening ----
    op = _Opener()
    r = d(op, kc_state=KC_HAS_KEY, kc_key="FILEKEY", file_key="FILEKEY")
    check("S1 estado estable: usa LLAVERO, no acuña/siembra/recrea",
          r.situation == "1" and r.source == SRC_KEYCHAIN and r.key == "FILEKEY"
          and not (r.mint or r.seed_keychain or r.recreate_file or r.fail))
    check("S1 decide SIN abrir la DB (atajo por comparación)", op.calls == [],
          f"opens_db llamado {len(op.calls)} veces")

    # ---- SITUATION 2: migration — keychain empty, file present ---------------
    op = _Opener()
    r = d(op, kc_state=KC_EMPTY, file_state=FILE_PRESENT, file_key="FILEKEY", db_present=True)
    check("S2 migración (TU CASO): usa FICHERO + siembra llavero, NO acuña",
          r.situation == "2" and r.source == SRC_FILE and r.key == "FILEKEY"
          and r.seed_keychain and not r.mint and not r.fail)
    check("S2 decide SIN abrir la DB", op.calls == [])

    # ---- SITUATION 3: keychain unavailable, file present → file, never mint --
    op = _Opener()
    r = d(op, kc_state=KC_UNAVAILABLE, file_state=FILE_PRESENT, file_key="FILEKEY")
    check("S3 llavero caído: usa FICHERO (regla de oro), NO acuña, NO siembra",
          r.situation == "3" and r.source == SRC_FILE and not r.mint and not r.seed_keychain)
    check("S3 decide SIN abrir la DB", op.calls == [])

    # ---- SITUATION 4a: disagreement, arbitrated by opening the DB ------------
    op = _Opener({"KCKEY": True})   # keychain key opens it
    r = d(op, kc_state=KC_HAS_KEY, kc_key="KCKEY", file_key="FILEKEY", db_present=True)
    check("S4a discrepancia, el LLAVERO abre la DB: lo usa con AVISO",
          r.situation == "4a" and r.source == SRC_KEYCHAIN and r.warn and not r.mint)
    op = _Opener({"KCKEY": False, "FILEKEY": True})  # only file opens it
    r = d(op, kc_state=KC_HAS_KEY, kc_key="KCKEY", file_key="FILEKEY", db_present=True)
    check("S4a discrepancia, el llavero NO abre: cae al FICHERO con AVISO",
          r.situation == "4a" and r.source == SRC_FILE and r.warn and not r.mint)
    op = _Opener({"KCKEY": False, "FILEKEY": False})  # neither opens it
    r = d(op, kc_state=KC_HAS_KEY, kc_key="KCKEY", file_key="FILEKEY", db_present=True)
    check("S4a discrepancia y NINGUNA abre la DB: fallo ruidoso, NO acuña",
          r.situation == "4a" and r.fail and not r.mint)

    # ---- SITUATION 4b: keychain has key, no file ----------------------------
    op = _Opener({"KCKEY": True})
    r = d(op, kc_state=KC_HAS_KEY, kc_key="KCKEY", file_state=FILE_ABSENT,
          file_key=None, db_present=True)
    check("S4b sin fichero, el llavero abre la DB: lo usa y RECREA el fichero",
          r.situation == "4b" and r.source == SRC_KEYCHAIN and r.recreate_file and not r.mint)
    op = _Opener({"KCKEY": False})
    r = d(op, kc_state=KC_HAS_KEY, kc_key="KCKEY", file_state=FILE_ABSENT,
          file_key=None, db_present=True)
    check("S4b sin fichero, el llavero NO abre la DB: fallo ruidoso, NO acuña",
          r.situation == "4b" and r.fail and not r.mint)

    # ---- SITUATION 4c: file present but UNREADABLE --------------------------
    # Resilient policy (Bilal 2026-07-11): rescue via the keychain ONLY if its
    # key PROVABLY opens the DB; otherwise fail loud. Never mint, never blind.
    op = _Opener({"KCKEY": True})   # keychain key OPENS the DB
    r = d(op, kc_state=KC_HAS_KEY, kc_key="KCKEY", file_state=FILE_UNREADABLE,
          file_key=None, db_present=True)
    check("S4c ilegible + llave del llavero que ABRE la DB (validado): la usa + recrea fichero + AVISO, NO acuña",
          r.situation == "4c" and r.source == SRC_KEYCHAIN and r.recreate_file
          and r.warn and not r.fail and not r.mint)
    check("S4c rescate SOLO tras comprobar que abre (opens_db llamado con la llave del llavero)",
          op.calls == ["KCKEY"])

    op = _Opener({"KCKEY": False})  # keychain key does NOT open the DB
    r = d(op, kc_state=KC_HAS_KEY, kc_key="KCKEY", file_state=FILE_UNREADABLE,
          file_key=None, db_present=True)
    check("S4c ilegible + el llavero NO abre la DB: fallo ruidoso, NO acuña",
          r.situation == "4c" and r.fail and not r.mint)

    for kc in (KC_EMPTY, KC_UNAVAILABLE):
        r = d(kc_state=kc, kc_key=None, file_state=FILE_UNREADABLE, file_key=None, db_present=True)
        check(f"S4c ilegible + llavero={kc} (sin llave que ofrecer): fallo ruidoso, NO acuña",
              r.situation == "4c" and r.fail and not r.mint)

    op = _Opener({"KCKEY": True})   # has a key, but there's NO DB to validate against
    r = d(op, kc_state=KC_HAS_KEY, kc_key="KCKEY", file_state=FILE_UNREADABLE,
          file_key=None, db_present=False)
    check("S4c ilegible + llavero con llave pero SIN DB que validar: fallo ruidoso (nunca a ciegas)",
          r.situation == "4c" and r.fail and not r.mint and op.calls == [])

    # ---- SITUATION 5: the ONE mint cell -------------------------------------
    op = _Opener()
    r = d(op, kc_state=KC_EMPTY, file_state=FILE_ABSENT, file_key=None, db_present=False)
    check("S5 primer arranque real (llavero probado-vacío, sin fichero ni DB): ACUÑA",
          r.situation == "5" and r.mint and r.source == SRC_MINTED and r.seed_keychain)
    check("S5 decide SIN abrir la DB", op.calls == [])

    print("\n-- invariante: los ÚNICOS caminos que acuñan son S5 y el interruptor --")
    # Mint-gate negatives: every "no usable key" cell must FAIL, never mint.
    r = d(kc_state=KC_EMPTY, file_state=FILE_ABSENT, file_key=None, db_present=True)
    check("NEG llavero vacío + sin fichero + HAY DB → fallo, NUNCA acuña",
          r.fail and not r.mint)
    r = d(kc_state=KC_UNAVAILABLE, file_state=FILE_ABSENT, file_key=None, db_present=False)
    check("NEG llavero no disponible + sin fichero (sin DB) → fallo, NUNCA acuña",
          r.fail and not r.mint)
    r = d(kc_state=KC_UNAVAILABLE, file_state=FILE_ABSENT, file_key=None, db_present=True)
    check("NEG llavero no disponible + sin fichero + HAY DB → fallo, NUNCA acuña",
          r.fail and not r.mint)

    print("\n-- interruptor VOKTER_KEY_SOURCE=file (reversión) --")
    r = d(override=SRC_FILE, kc_state=KC_HAS_KEY, kc_key="KCKEY", file_key="FILEKEY")
    check("KS fichero-solo con fichero presente: usa el FICHERO, ignora el llavero",
          r.situation == "K" and r.source == SRC_FILE and r.key == "FILEKEY")
    r = d(override=SRC_FILE, file_state=FILE_ABSENT, file_key=None, db_present=True)
    check("KS fichero-solo, sin fichero + HAY DB: fallo ruidoso, NO acuña",
          r.situation == "K" and r.fail and not r.mint)
    r = d(override=SRC_FILE, file_state=FILE_ABSENT, file_key=None, db_present=False)
    check("KS fichero-solo, sin fichero ni DB: acuña al fichero",
          r.situation == "K" and r.mint)


def part2_real_validator() -> None:
    print("\n== PART 2 — real validator on a disposable encrypted DB ==\n")
    if not VENV_PY.exists():
        check("venv python presente para acuñar la DB de prueba", False, str(VENV_PY))
        return

    tmp = Path(tempfile.mkdtemp(prefix="vokter-keysource-"))
    db = tmp / "vokter.db"
    good = secrets.token_urlsafe(32)
    bad = secrets.token_urlsafe(32)
    try:
        # Mint a real encrypted DB with `good`, using the venv (has sqlcipher3).
        mint = (
            "import sys, sqlcipher3.dbapi2 as s\n"
            "db, key = sys.argv[1], sys.argv[2]\n"
            "c = s.connect(db)\n"
            "c.execute(\"PRAGMA key='%s'\" % key.replace(\"'\", \"''\"))\n"
            "c.execute('CREATE TABLE t(x TEXT)'); c.execute(\"INSERT INTO t VALUES('hi')\")\n"
            "c.commit(); c.close()\n"
        )
        subprocess.run([str(VENV_PY), "-c", mint, str(db), good], check=True)

        check("validador: la llave CORRECTA abre la DB (venv)",
              ks.key_opens_db(good, db, venv_py=VENV_PY, frozen_bin=Path("/nonexistent")) is True)
        check("validador: una llave INCORRECTA NO abre la DB (venv)",
              ks.key_opens_db(bad, db, venv_py=VENV_PY, frozen_bin=Path("/nonexistent")) is False)
        check("validador: sin ningún intérprete con sqlcipher3 → False (nunca lanza)",
              ks.key_opens_db(good, db, venv_py=Path("/nonexistent"), frozen_bin=Path("/nonexistent")) is False)

        # Informational: the frozen --verify-key path (does NOT gate the suite).
        # NOTE: it only works once the frozen binary is REBUILT with the new
        # launcher. A stale dist/ (built before this change) ignores the flag and
        # boots the server instead — detectable because it emits a traceback.
        if FROZEN_BIN.exists():
            env = os.environ.copy(); env["VOKTER_DB_KEY"] = good
            p = subprocess.run([str(FROZEN_BIN), "--verify-key", str(db)],
                               env=env, capture_output=True, text=True)
            stale = "vokter_backend" in p.stderr and "--verify-key" not in p.stderr
            if stale:
                print("[info] binario congelado es ANTERIOR a --verify-key (build viejo): "
                      "la bandera vive en el fuente; toma efecto al reconstruir el congelado. "
                      "En dev el validador usa el venv, que SÍ pasa arriba.")
            else:
                env["VOKTER_DB_KEY"] = bad
                rc_bad = subprocess.run([str(FROZEN_BIN), "--verify-key", str(db)],
                                        env=env, capture_output=True).returncode
                print(f"[info] frozen --verify-key: correcta rc={p.returncode} (0 esperado), "
                      f"incorrecta rc={rc_bad} (≠0 esperado)")
        else:
            print("[info] binario congelado ausente → --verify-key no ejercitado (ok en dev)")
    finally:
        try:
            db.unlink()
        except FileNotFoundError:
            pass
        try:
            tmp.rmdir()
        except OSError:
            pass


def _write_fake_bin(path: Path, body: str) -> Path:
    path.write_text("#!/usr/bin/env python3\n" + body)
    path.chmod(0o755)
    return path


def part3_validator_hardening() -> None:
    """A binary that does NOT understand --verify-key must never be trusted and
    must never leave a phantom process behind."""
    print("\n== PART 3 — endurecimiento del validador (binarios NO capaces) ==\n")
    tmp = Path(tempfile.mkdtemp(prefix="vokter-harden-"))
    db = tmp / "vokter.db"
    db.write_bytes(b"not-a-real-db")  # never actually opened by a fake
    nope = Path("/nonexistent-venv")
    try:
        # (1) A binary that exits 0 but never prints the marker (an old binary
        #     that "succeeded" at something else). Must NOT be trusted → False.
        noise = _write_fake_bin(tmp / "fake_noise",
                                "import sys\nprint('INFO: started uvicorn on :8080')\nsys.exit(0)\n")
        check("binario que sale 0 SIN marcador → NO se fía (False)",
              ks.key_opens_db("k", db, venv_py=nope, frozen_bin=noise) is False)

        # (2) A binary that hangs (a phantom server). key_opens_db must time out,
        #     kill the WHOLE group, return False, and leave nothing running.
        pidfile = tmp / "child.pid"
        hang = _write_fake_bin(tmp / "fake_hang",
                               "import os, sys, time\n"
                               f"open(r'{pidfile}','w').write(str(os.getpid()))\n"
                               "print('INFO: serving...', flush=True)\n"
                               "time.sleep(60)\n")
        t0 = time.time()
        r = ks.key_opens_db("k", db, venv_py=nope, frozen_bin=hang, timeout=2.0)
        elapsed = time.time() - t0
        check("binario que se CUELGA → False y corta por timeout (sin fantasma)",
              r is False and elapsed < 8.0, f"{elapsed:.1f}s")
        # The killpg must have reaped it: the pid must be gone.
        alive = False
        if pidfile.exists():
            pid = int(pidfile.read_text().strip())
            time.sleep(0.3)
            try:
                os.kill(pid, 0)   # signal 0 = existence check
                alive = True
            except OSError:
                alive = False
        check("el proceso colgado quedó MUERTO tras el timeout (killpg)", not alive)
    finally:
        for p in tmp.glob("*"):
            try:
                p.unlink()
            except OSError:
                pass
        try:
            tmp.rmdir()
        except OSError:
            pass


def main() -> int:
    part1_decision_table()
    part2_real_validator()
    part3_validator_hardening()
    print("\n" + ("=" * 60))
    print("RESUMEN — las 5 situaciones:")
    print("  S1 estable · S2 migración · S3 llavero caído · S4 (a/b/c) · S5 primer arranque")
    print(("TODAS VERDES ✓" if _ok else "ALGUNA FALLA ✗"))
    return 0 if _ok else 1


if __name__ == "__main__":
    sys.exit(main())
