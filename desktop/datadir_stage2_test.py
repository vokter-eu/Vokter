#!/usr/bin/env python3
"""Phase 3.3-B · Stage 2 — verify the CABLED path on Bilal's real machine.

Exercises the REAL wired code (orchestrator.ensure_db_key → _guardrail_or_die →
datadir.guardrail), not a mock. Two checks:

  PRUEBA A (existing user, dev): DATA_DIR resolves to runtime/data, the guardrail
    does NOT fire (there IS a DB there), and the chosen key OPENS the real DB.
    Situation 1 writes nothing (no mint/recreate/seed), so Bilal's files stay
    byte-identical (asserted with hashes by the caller).

  PRUEBA B (the new one — the firing case): point the orchestrator at a THROWAWAY
    empty dir with the REAL keychain (which holds a key). The guardrail must fire
    → the orchestrator ABORTS loudly (SystemExit 1), starts no backend, and
    creates no DB. Throwaway dir + read-only-to-Bilal's-data = touches nothing.

Run under the SYSTEM python3 (keychain needs secretstorage).
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import datadir
import keysource
import orchestrator as orch


def prueba_a() -> bool:
    print("\n== PRUEBA A · usuario existente en dev ==")
    expected = orch.HERE / "runtime" / "data"
    print(f"  DATA_DIR resuelto → {orch.DATA_DIR}")
    print(f"  motivo            → {orch._DATA_WHY}")
    ok_path = orch.DATA_DIR == expected
    print(f"  ¿== runtime/data? → {ok_path}")

    db_path = orch.DATA_DIR / "vokter.db"
    # Real wired boot logic up to the key (guardrail runs INSIDE; if it fired it
    # would die() before returning — reaching a key proves it did NOT fire).
    key = orch.ensure_db_key()
    print("  guardarraíl       → NO saltó (ensure_db_key devolvió una llave)")

    opens = keysource.key_opens_db(key, db_path, venv_py=orch.VENV_PY, frozen_bin=orch.FROZEN_BIN)
    print(f"  ¿la llave ABRE tu DB? → {opens}")
    return ok_path and opens


def prueba_b() -> bool:
    print("\n== PRUEBA B · carpeta vacía desechable + tu llavero real (debe ABORTAR) ==")
    with tempfile.TemporaryDirectory(prefix="vokter-empty-") as tmp:
        empty = Path(tmp)
        # Point the CABLED orchestrator at the throwaway empty dir.
        orch.DATA_DIR = empty
        orch.DBKEY_FILE = empty / ".db_key"
        print(f"  DATA_DIR (desechable) → {empty}  (vacío: {list(empty.iterdir()) == []})")

        aborted = False
        try:
            orch.ensure_db_key()
        except SystemExit as exc:
            aborted = exc.code == 1
            print(f"  ABORTÓ con SystemExit(code={exc.code}) ✓")
        else:
            print("  ✗ NO abortó — arrancó en vacío (FALLO)")

        made_db = (empty / "vokter.db").exists()
        print(f"  ¿creó una DB vacía? → {made_db}  (debe ser False)")
        return aborted and not made_db


def main() -> int:
    a = prueba_a()
    b = prueba_b()
    print("\n" + "=" * 60)
    print(f"PRUEBA A (no molesta cuando no debe): {'OK ✓' if a else 'FALLO ✗'}")
    print(f"PRUEBA B (frena de verdad cuando debe): {'OK ✓' if b else 'FALLO ✗'}")
    print("STAGE 2 " + ("VERDE ✓" if (a and b) else "CON PROBLEMAS ✗"))
    return 0 if (a and b) else 1


if __name__ == "__main__":
    sys.exit(main())
