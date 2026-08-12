#!/usr/bin/env python3
"""Phase 3.3-B · Stage 1 — READ-ONLY rehearsal of data-dir resolution + guardrail.

Shows, against Bilal's REAL machine, what a real boot WOULD resolve and whether
the guardrail WOULD fire — in every scenario that matters — WITHOUT moving,
creating, or deleting a single byte. Real boot is UNCHANGED (orchestrator still
uses runtime/data). This only rehearses.

The "what if XDG already archived" scenario is simulated ENTIRELY here: the dry
run builds the facts (treating the archived dir as empty) and feeds the SAME pure
datadir.decide_guardrail() the real boot uses. The production guardrail() has no
simulation parameter — it cannot be told to pretend (fix #5).

Run under the SYSTEM python3 (keychain read needs secretstorage).
"""
from __future__ import annotations

import sys
from pathlib import Path

import datadir
from datadir import KeychainState
import keychain
import orchestrator as orch

HOME = orch.HERE  # …/Vokter/desktop  (dev layout root, == _here())


def _keychain_state() -> KeychainState:
    """Read the REAL slot, read-only, NO write-probe (never pops a dialog).
    Tri-state: unreachable is NOT empty."""
    if not keychain.is_reachable_readonly():
        return KeychainState.UNREACHABLE
    return KeychainState.HAS_KEY if keychain.get_key() is not None else KeychainState.NO_KEY


def _emit(g: datadir.Guardrail) -> None:
    print("   GUARDARRAÍL     → " + ("SALTA ⚠️" if g.triggered else "no salta"))
    for line in g.message().splitlines():
        print("     " + line)


def _show(title: str, *, frozen: bool, env_override: str | None,
          kc: KeychainState, pretend_archived: frozenset[Path] = frozenset(),
          note: str | None = None) -> None:
    data_dir, why = datadir.resolve_data_dir(frozen=frozen, home=HOME, env_override=env_override)
    print(f"\n── {title} ──")
    print(f"   frozen={frozen}  override={env_override or '(ninguno)'}")
    print(f"   RESUELVE datos → {data_dir}")
    print(f"   motivo         → {why}")
    print(f"   ¿DB ahí?       → {datadir._dir_has_db(data_dir)}")
    print(f"   llavero        → {kc.value}")
    if note:
        print(f"   NOTA           → {note}")

    if not pretend_archived:
        # Real path: read the real filesystem via the production entry point.
        _emit(datadir.guardrail(resolved_dir=data_dir, keychain=kc, home=HOME))
        return

    # Simulated post-archive: BUILD facts here (archived dirs look empty) and
    # feed the same pure decision. Production guardrail() is never told to lie.
    print(f"   [simulado post-archivo: trato como vacías → {', '.join(str(p) for p in pretend_archived)}]")

    def present(d: Path) -> bool:
        return d not in pretend_archived and datadir._dir_has_db(d)

    resolved_has_db = present(data_dir)
    candidates = [(l, p) for l, p in datadir.known_locations(HOME)
                  if p != data_dir and present(p)]
    _emit(datadir.decide_guardrail(resolved_dir=data_dir, resolved_has_db=resolved_has_db,
                                   candidates=candidates, keychain=kc))


def main() -> int:
    print("== 3.3-B · ENSAYO EN SOLO LECTURA — resolución de rutas + guardarraíl ==")
    print("   (no mueve, ni crea, ni borra nada — el arranque real sigue intacto)")
    print(f"\n  home (dev) = {HOME}")
    print("  ubicaciones conocidas que vigila el guardarraíl:")
    for label, path in datadir.known_locations(HOME):
        mark = "✓ tiene DB" if datadir._dir_has_db(path) else "· vacía"
        print(f"    {mark:12} {path}  ({label})")

    kc = _keychain_state()
    print(f"\n  llavero (solo lectura, sin sonda): {kc.value}")

    # 1. Dev normal — lo que ocurre hoy cada día en tu máquina.
    _show("ESCENARIO 1 · dev (source, sin frozen)", frozen=False, env_override=None, kc=kc)

    xdg = datadir.platform_data_dir()

    # 2. Paquete instalado HOY, antes de archivar: XDG tiene la DB vieja de
    #    Docker. El guardarraíl NO salta (hay una DB), pero es la DB EQUIVOCADA:
    #    la llave del llavero no la abre → keysource fallará RUIDOSO (4c). Dos
    #    redes distintas para dos fallos distintos: guardarraíl=vacío,
    #    keysource=llave-que-no-abre. Por eso hay que ARCHIVAR antes de instalar.
    _show("ESCENARIO 2 · paquete instalado HOY (frozen, XDG aún con DB de Docker)",
          frozen=True, env_override=None, kc=kc,
          note="DB presente pero es la vieja de Docker → la llave no la abre → "
               "keysource falla ruidoso (no es trabajo del guardarraíl). ARCHIVAR antes de instalar.")

    # 3. La puerta de atrás que Bilal quiso cubrir: binario FROZEN en el árbol de
    #    dev SIN acordarse del override → resuelve a XDG. Simulamos XDG YA
    #    archivado (vacío) para ver la RED actuar y ofrecer runtime/data.
    _show("ESCENARIO 3 · frozen-en-dev, olvidé el override, XDG YA archivado",
          frozen=True, env_override=None, kc=kc, pretend_archived=frozenset({xdg}))

    # 4. La lección del llavero, un piso más abajo: XDG archivado (vacío) Y el
    #    llavero INALCANZABLE (bloqueado). "No pude preguntar" ≠ "no hay" → la red
    #    debe SALTAR igualmente (caución), no arrancar en vacío en silencio.
    _show("ESCENARIO 4 · XDG archivado + llavero INALCANZABLE (fix #1)",
          frozen=True, env_override=None, kc=KeychainState.UNREACHABLE,
          pretend_archived=frozenset({xdg}))

    # 5. Nuevo usuario de verdad: nada en ninguna parte y el llavero, alcanzable,
    #    VACÍO → arranque fresco en silencio es CORRECTO (no debe saltar).
    _show("ESCENARIO 5 · usuario nuevo real (todo archivado + llavero vacío)",
          frozen=True, env_override=None, kc=KeychainState.NO_KEY,
          pretend_archived=frozenset({xdg, HOME / "runtime" / "data"}))

    print("\n" + "=" * 64)
    print("Ensayo terminado. NADA se ha movido. Revisa arriba qué resolvería.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
