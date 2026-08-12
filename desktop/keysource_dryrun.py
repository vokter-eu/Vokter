#!/usr/bin/env python3
"""Phase 3.2 · step 3 — STAGE 2 read-only dry run against the REAL data.

This previews what the new keychain-first logic WOULD decide on Bilal's real
machine, and confirms the chosen key actually opens the real DB — WITHOUT
changing anything. It is PURELY read-only:

  * the keychain slot is READ (get_key); availability is checked with the
    read-only signal (is_reachable_readonly) — NOT the write-probe;
  * the DB is opened read-only + immutable by the validator;
  * NOTHING is seeded, minted, recreated, or written. The Decision's
    seed_keychain / mint / recreate_file flags are REPORTED, never executed.

Real boot is UNCHANGED (still file-first). This only rehearses; flipping the
default to keychain-first is Stage 3, done separately with Bilal's OK.

Run under the SYSTEM python3 (needs secretstorage); the validator shells out to
the venv (needs sqlcipher3).
"""
from __future__ import annotations

import sys

import keychain
import keysource as ks
import orchestrator as orch

DB_PATH = orch.DATA_DIR / "vokter.db"


def main() -> int:
    print("== STAGE 2 — ENSAYO EN SOLO LECTURA contra datos reales ==")
    print("   (no escribe nada: ni siembra, ni acuña, ni recrea, ni toca la DB)\n")
    print(f"  fichero de llave: {orch.DBKEY_FILE}")
    print(f"  base de datos:    {DB_PATH}")
    print(f"  slot llavero:     {keychain.SERVICE}/{keychain.KEY_NAME}\n")

    # --- Read the world, read-only ----------------------------------------
    reachable = keychain.is_reachable_readonly()
    slot = keychain.get_key()  # pure read of the REAL slot
    print(f"  llavero alcanzable (solo lectura, SIN sonda): {reachable}")
    print(f"  get_key(slot real): {'None (VACÍO)' if slot is None else f'PRESENTE (len={len(slot)})'}")

    facts = ks.gather_facts(
        file_path=orch.DBKEY_FILE,
        db_path=DB_PATH,
        kc_available=keychain.is_reachable_readonly,  # read-only, no probe
        kc_get=keychain.get_key,                      # read
    )
    print(f"\n  HECHOS: fichero={facts['file_state']}  db_present={facts['db_present']}  "
          f"llavero={facts['kc_state']}")

    def opener(key: str) -> bool:
        # read-only + immutable open, via the venv (dev) / frozen (--verify-key)
        return ks.key_opens_db(key, DB_PATH, venv_py=orch.VENV_PY, frozen_bin=orch.FROZEN_BIN)

    decision = ks.decide(**facts, opens_db=opener, override=None)

    print("\n== QUÉ ELEGIRÍA el arranque keychain-first (sin ejecutarlo) ==")
    print(f"  Situación:   {decision.situation}")
    print(f"  Fuente:      {decision.source}")
    print(f"  Motivo:      {decision.reason}")
    print(f"  Efectos que HARÍA un arranque real (aquí NO se ejecutan): "
          f"seed_keychain={decision.seed_keychain}  recreate_file={decision.recreate_file}  "
          f"mint={decision.mint}  fail={decision.fail}  warn={decision.warn}")

    # --- Explicit confirmation: does the CHOSEN key open the real DB? ------
    ok = True
    if decision.fail:
        print("\n  ⚠️  La decisión sería FALLO RUIDOSO — no habría llave usable.")
        ok = False
    elif decision.mint:
        print("\n  (La decisión sería ACUÑAR: no hay DB que abrir — nada que confirmar.)")
    elif decision.key is not None:
        opens = opener(decision.key)
        print(f"\n  CONFIRMACIÓN: la llave elegida ({decision.source}) "
              f"{'ABRE ✓' if opens else 'NO abre ✗'} tu DB real (solo lectura).")
        ok = opens
    else:
        ok = False

    # --- Prove we wrote nothing to the keychain ---------------------------
    slot_after = keychain.get_key()
    untouched = slot_after == slot
    print(f"\n  slot del llavero tras el ensayo: "
          f"{'None (VACÍO)' if slot_after is None else 'PRESENTE'} — "
          f"{'INTACTO ✓' if untouched else 'CAMBIÓ ✗'}")

    print("\n" + "=" * 60)
    print("ENSAYO OK ✓" if (ok and untouched) else "ENSAYO CON PROBLEMAS ✗")
    return 0 if (ok and untouched) else 1


if __name__ == "__main__":
    sys.exit(main())
