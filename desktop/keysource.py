#!/usr/bin/env python3
"""Phase 3.2 · step 3 — the key-source DECISION (keychain-first, file fallback).

NOT wired into boot yet. This module only DECIDES which DB key a boot would use
and what side effects (seed keychain / recreate file / mint) it WOULD perform.
The orchestrator keeps its current file-first behaviour until a later stage
flips the default (that flip is Stage 3, done separately with Bilal's OK). Kept
as a pure, separately testable unit for exactly that reason.

Golden rule (unchanged): if the keychain fails we fall back to the file; we
never lock the user out of their DB, and we never raise a keychain dialog.

The invariant with teeth: a NEW key is minted in EXACTLY ONE situation
(SITUATION 5) — keychain proven-available-and-empty AND no key file AND no
database. Every other "no usable key" path FAILS LOUD; it never mints. Two
confusions a naive port would make, and which this refuses to make:
  * "couldn't ASK the keychain"  != "the keychain is EMPTY"
  * "couldn't READ the key file" != "there is NO key file"

Precedence means a VALIDATED try-order, not blind selection: a key taken from
the keychain is trusted only if it equals the (already-proven) file key, or if
it actually OPENS the database. A wrong/corrupt keychain entry degrades to the
proven file key — never to an apparent lock-out.
"""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

# --- Fact vocabulary --------------------------------------------------------
FILE_PRESENT = "present"        # present and readable, non-empty
FILE_UNREADABLE = "unreadable"  # present but could not be read (perms/corrupt/empty)
FILE_ABSENT = "absent"          # genuinely not there

KC_HAS_KEY = "available_with_key"  # proven available, slot holds a key
KC_EMPTY = "available_empty"       # PROVEN available, slot empty (safe to mint)
KC_UNAVAILABLE = "unavailable"     # couldn't ask (locked/headless/hung/error)

SRC_KEYCHAIN = "keychain"
SRC_FILE = "file"
SRC_MINTED = "minted"

# Kill switch, same family as the other VOKTER_DESKTOP_* knobs. "file" forces
# file-only mode (skip the keychain entirely); unset/anything-else = the new
# keychain-first logic once the default is flipped in Stage 3.
OVERRIDE_ENV = "VOKTER_KEY_SOURCE"


@dataclass
class Decision:
    """What a boot WOULD do. The caller performs the effects; this only decides."""
    situation: str                 # "1"|"2"|"3"|"4a"|"4b"|"4c"|"5"|"K"
    key: str | None = None         # the key to open the DB with (None if fail/mint)
    source: str | None = None      # SRC_* or None on failure
    seed_keychain: bool = False    # write the file key INTO the keychain slot
    recreate_file: bool = False    # write the keychain key BACK to the file
    mint: bool = False             # generate a brand-new key (SITUATION 5 only)
    fail: bool = False             # refuse to boot; touch nothing
    warn: bool = False             # loud warning (a discrepancy was found)
    reason: str = ""               # human-readable log line


def decide(
    *,
    file_state: str,
    file_key: str | None,
    db_present: bool,
    kc_state: str,
    kc_key: str | None,
    opens_db: Callable[[str], bool],
    override: str | None = None,
) -> Decision:
    """Pure decision over the boot facts.

    `opens_db(key) -> bool` validates a candidate key against an EXISTING DB. It
    is called ONLY when a key must be arbitrated against real data (situations
    4a / 4b) — never in the shortcut cases (1/2/3/5), where the file is already
    proven or there is nothing to open.
    """
    # --- Kill switch: file-only mode ---------------------------------------
    if override == SRC_FILE:
        if file_state == FILE_UNREADABLE:
            return Decision("K", fail=True,
                            reason="fichero-solo (interruptor): el fichero existe pero no se pudo leer; NO acuño")
        if file_state == FILE_PRESENT:
            return Decision("K", key=file_key, source=SRC_FILE,
                            reason="fichero-solo (interruptor): uso el fichero")
        if db_present:
            return Decision("K", fail=True,
                            reason="fichero-solo: hay DB pero no hay fichero de llave; NUNCA acuño; fallo ruidoso")
        return Decision("K", mint=True, source=SRC_MINTED,
                        reason="fichero-solo: primer arranque (sin fichero ni DB); acuño al fichero")

    # --- 4c: the key file exists but we could not read it ------------------
    # This must NEVER be mistaken for "absent" — that mistake would let a later
    # branch mint over a DB we simply failed to unlock.
    #
    # Bilal's resilient policy (2026-07-11): a corrupt/unreadable FILE must not
    # lock the user out if the KEYCHAIN holds a key that PROVABLY opens the DB.
    # Use that key ONLY if the validator confirms it opens the DB — never
    # blindly — and re-create the file from it. If it doesn't open (or there is
    # no keychain key, or no DB to validate against), fail loud. Still never mint.
    if file_state == FILE_UNREADABLE:
        if kc_state == KC_HAS_KEY and db_present and opens_db(kc_key):
            return Decision("4c", key=kc_key, source=SRC_KEYCHAIN, recreate_file=True, warn=True,
                            reason="fichero ilegible pero la llave del LLAVERO ABRE la DB (validado); la uso y recreo el fichero")
        return Decision("4c", fail=True,
                        reason="fichero ilegible y el llavero no aporta una llave que (validada) abra la DB; fallo ruidoso, NO acuño")

    # --- Keychain holds a key ----------------------------------------------
    if kc_state == KC_HAS_KEY:
        if file_state == FILE_PRESENT:
            if kc_key == file_key:
                return Decision("1", key=kc_key, source=SRC_KEYCHAIN,
                                reason="estado estable: llavero y fichero coinciden; uso el llavero (fichero de respaldo)")
            # They DISAGREE (situation 4a) — trust the one that opens the DB.
            if db_present:
                if opens_db(kc_key):
                    return Decision("4a", key=kc_key, source=SRC_KEYCHAIN, warn=True,
                                    reason="discrepancia: la llave del LLAVERO abre la DB; la uso (AVISO: difiere del fichero)")
                if opens_db(file_key):
                    return Decision("4a", key=file_key, source=SRC_FILE, warn=True,
                                    reason="discrepancia: la llave del llavero NO abre la DB; uso el FICHERO (AVISO)")
                return Decision("4a", fail=True,
                                reason="discrepancia y NINGUNA llave abre la DB; fallo ruidoso, NO acuño")
            # No DB to arbitrate against → prefer the proven file, warn.
            return Decision("4a", key=file_key, source=SRC_FILE, warn=True,
                            reason="discrepancia sin DB que arbitrar; uso el fichero (AVISO)")
        # File absent, keychain has a key (situation 4b).
        if db_present:
            if opens_db(kc_key):
                return Decision("4b", key=kc_key, source=SRC_KEYCHAIN, recreate_file=True,
                                reason="sin fichero: la llave del llavero abre la DB; la uso y recreo el fichero de respaldo")
            return Decision("4b", fail=True,
                            reason="sin fichero: el llavero tiene llave pero NO abre la DB; fallo ruidoso, NO acuño")
        # No file, no DB, but the keychain holds a key → adopt it, re-seed file.
        return Decision("4b", key=kc_key, source=SRC_KEYCHAIN, recreate_file=True,
                        reason="sin fichero ni DB: adopto la llave del llavero y recreo el fichero de respaldo")

    # --- Keychain PROVEN available but empty -------------------------------
    if kc_state == KC_EMPTY:
        if file_state == FILE_PRESENT:
            return Decision("2", key=file_key, source=SRC_FILE, seed_keychain=True,
                            reason="migración: llavero vacío; uso el fichero y siembro el llavero")
        if db_present:
            return Decision("2", fail=True,
                            reason="llavero vacío y sin fichero pero HAY DB; no puedo abrirla; NUNCA acuño; fallo ruidoso")
        # The ONLY cell that mints.
        return Decision("5", mint=True, source=SRC_MINTED, seed_keychain=True,
                        reason="primer arranque real: llavero disponible y vacío, sin fichero ni DB; acuño llave nueva")

    # --- Keychain UNAVAILABLE (we could not ask) ---------------------------
    if file_state == FILE_PRESENT:
        return Decision("3", key=file_key, source=SRC_FILE,
                        reason="llavero no disponible; uso el fichero (regla de oro); no siembro ahora")
    # File absent AND we couldn't ask the keychain → never mint: the keychain
    # might hold a key we simply couldn't read.
    return Decision("3", fail=True,
                    reason="llavero no disponible y sin fichero; NO acuño (el llavero podría tener la llave); fallo ruidoso")


# --- Fact gathering ---------------------------------------------------------
def read_file_key(path: Path) -> tuple[str, str | None]:
    """(state, key). Distinguishes ABSENT from PRESENT-but-UNREADABLE — a read
    failure (perms/corruption) or an empty file is UNREADABLE, never 'absent'."""
    if not path.exists():
        return FILE_ABSENT, None
    try:
        raw = path.read_text().strip()
    except OSError:
        return FILE_UNREADABLE, None
    if not raw:
        return FILE_UNREADABLE, None
    return FILE_PRESENT, raw


def gather_facts(
    *,
    file_path: Path,
    db_path: Path,
    kc_available: Callable[[], bool],
    kc_get: Callable[[], str | None],
) -> dict:
    """Read the world into the inputs `decide()` needs.

    `kc_available` / `kc_get` are injected so a boot can pass the write-probing
    `keychain.is_available` + `keychain.get_key`, while the read-only dry run can
    pass non-writing equivalents. `kc_get` is consulted only when the keychain
    is deemed available."""
    file_state, file_key = read_file_key(file_path)
    db_present = db_path.exists()
    if kc_available():
        k = kc_get()
        kc_state, kc_key = (KC_HAS_KEY, k) if k is not None else (KC_EMPTY, None)
    else:
        kc_state, kc_key = KC_UNAVAILABLE, None
    return dict(file_state=file_state, file_key=file_key, db_present=db_present,
                kc_state=kc_state, kc_key=kc_key)


# --- The validator: "does this key open the DB?" ----------------------------
# The orchestrator's own interpreter has secretstorage but NOT sqlcipher3, so it
# CANNOT open the DB itself. We shell out to whatever carries sqlcipher3: the
# venv python in dev, else the frozen backend's --verify-key mode on a user
# machine. The key travels by ENV (VOKTER_VERIFY_KEY), never argv, so it can't
# leak into the process list. A missing/failing validator returns False → the
# caller falls back to the proven file key and NEVER mints.
VERIFY_KEY_ENV = "VOKTER_VERIFY_KEY"

# Capability marker. The validator trusts a subprocess's verdict ONLY if the
# subprocess printed this on stdout, PROVING it actually ran the verify path. A
# binary that predates --verify-key never prints it (it would boot the server
# instead), so its output is treated as "not verify-capable" → False. Kept in
# lock-step with freeze/vokter_backend.py.
_VERIFY_MARKER = "__VOKTER_VERIFY_KEY__"

_VERIFY_SCRIPT = (
    "import os, sys, sqlcipher3.dbapi2 as s\n"
    "print('" + _VERIFY_MARKER + "', flush=True)\n"
    "db = sys.argv[1]; key = os.environ['" + VERIFY_KEY_ENV + "']\n"
    "try:\n"
    "    con = s.connect(f'file:{db}?mode=ro&immutable=1', uri=True)\n"
    "    con.execute(\"PRAGMA key='%s'\" % key.replace(\"'\", \"''\"))\n"
    "    con.execute('SELECT count(*) FROM sqlite_master').fetchone()\n"
    "    con.close()\n"
    "except Exception:\n"
    "    sys.exit(1)\n"
)


def _run_capped(cmd: list[str], env: dict, timeout: float) -> tuple[int | None, bytes]:
    """Run `cmd` in its OWN process session; return (returncode, stdout). On
    timeout, SIGKILL the whole process group so nothing (e.g. a phantom server
    an old binary tried to start) can linger, and return (None, b''). Never
    raises."""
    try:
        proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, start_new_session=True)
    except Exception:
        return None, b""
    try:
        out, _ = proc.communicate(timeout=timeout)
        return proc.returncode, out
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:
            proc.kill()
        try:
            proc.communicate(timeout=5)
        except Exception:
            pass
        return None, b""


def key_opens_db(
    key: str,
    db_path: Path,
    *,
    venv_py: Path,
    frozen_bin: Path,
    timeout: float = 15.0,
) -> bool:
    """True iff `key` opens `db_path` (SQLCipher), read-only + immutable.

    Never raises: any problem (no validator, timeout, wrong key, unreadable DB,
    or a binary that doesn't understand the flag) is a plain False, so the
    caller degrades to the proven file key."""
    env = os.environ.copy()
    env[VERIFY_KEY_ENV] = key
    tmp_guard: str | None = None
    try:
        if venv_py.exists():
            cmd = [str(venv_py), "-c", _VERIFY_SCRIPT, str(db_path)]
        elif frozen_bin.exists():
            env["VOKTER_DB_KEY"] = key  # the frozen binary reads the key from here
            # Defensive sandbox for the case this binary is OLD and ignores
            # --verify-key: point its data dir at a throwaway temp and its bind
            # at an unbindable RFC-5737 TEST-NET address, so it can neither touch
            # real data nor stand up a phantom server — it fails fast and clean.
            tmp_guard = tempfile.mkdtemp(prefix="vokter-verify-")
            env["VOKTER_DB"] = os.path.join(tmp_guard, "probe.db")
            env["VOKTER_BIND"] = "192.0.2.1"  # RFC 5737 TEST-NET-1: not local → bind fails
            cmd = [str(frozen_bin), "--verify-key", str(db_path)]
        else:
            return False
        rc, out = _run_capped(cmd, env, timeout)
        # Trust the verdict ONLY from a binary that proved it understands the
        # flag (printed the marker). No marker ⇒ not verify-capable ⇒ False.
        if _VERIFY_MARKER.encode() not in out:
            return False
        return rc == 0
    finally:
        if tmp_guard:
            shutil.rmtree(tmp_guard, ignore_errors=True)
