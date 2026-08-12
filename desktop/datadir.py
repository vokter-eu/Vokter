#!/usr/bin/env python3
"""Phase 3.3-B — where the user's writable data lives, and the guardrail that
refuses to open an EMPTY Vokter in silence.

NOT wired into boot yet. This module is pure logic + read-only probes; it never
moves, creates, or deletes a byte of user data. Stage 2 (with Bilal's OK) will
have orchestrator.py resolve DATA_DIR through here.

Two jobs, mirroring the two golden rules one layer down from the keychain:

1. resolve_data_dir() — pick the writable data folder from the ROBUST signal,
   `frozen` (baked into the binary by the freezer at build time), NEVER from
   "does folder X exist". A dev checkout is never frozen; the shipped app always
   is. So the choice cannot flip because a folder appeared or was deleted.
     * env override — a DIRECTORY (VOKTER_DESKTOP_DATA), dev/test escape hatch
     * frozen  -> the platform per-user data dir (see platform_data_dir)
     * dev     -> <home>/runtime/data  (UNCHANGED; Bilal's dev world untouched)

2. guardrail() — the llavero lesson, one layer lower: "the data folder is empty"
   != "you are a new user". If the resolved folder has NO db BUT a Vokter clearly
   existed before — a db sits at a known prior location, OR the keychain holds a
   key (a key implies a db was minted once), OR we COULDN'T ASK the keychain —
   we STOP and warn LOUDLY, always offering a usable action. Loud-but-recoverable:
   never locked out (llavero rule), never blank in silence (this rule).

   The keychain signal is TRI-STATE on purpose (KeychainState): "unreachable" is
   NOT "empty". Assuming "no key" when we merely couldn't ask is the exact
   mistake keysource exists to avoid — and it's when the net matters most — so
   "unreachable" is treated with caution: it counts toward warning, never toward
   a silent fresh start.

   The list of prior locations is FIXED and known (dev runtime/data, the
   Docker-era XDG dir, platform equivalents) — never "any vokter.db on disk".
   Including dev runtime/data on purpose closes Bilal's back door: if the frozen
   binary is run in the dev tree WITHOUT the env override, it resolves to XDG;
   should XDG be empty while runtime/data holds the real db, the guardrail fires
   and points home — so a forgotten env var can't resurrect the silent-empty
   scare.

Design note (fix #5 / self-deception guard): the real-boot entry points read the
real filesystem and have NO simulation parameter. The decision is a SEPARATE pure
function, decide_guardrail(), that takes already-gathered facts. The dry run does
its "what if archived" by BUILDING simulated facts and feeding that same pure
function — so the production path cannot be told to pretend, not even by mistake.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

DB_NAME = "vokter.db"


class KeychainState(Enum):
    """Tri-state — 'unreachable' is deliberately distinct from 'empty'."""
    HAS_KEY = "hay llave"          # slot read, a key is present
    NO_KEY = "no hay llave"        # keychain reachable AND provably empty
    UNREACHABLE = "no pude preguntar"  # locked / headless / no session — UNKNOWN


# --- 1. Path resolution ------------------------------------------------------
def platform_data_dir() -> Path:
    """Per-user application-data dir for the INSTALLED app.

    SINGLE SOURCE OF TRUTH for the packaged data path on the orchestrator side.
    app/config.py:_default_db_path() currently computes the same paths for a
    STANDALONE backend launch; the two are byte-identical today. At Stage 2 the
    orchestrator always passes VOKTER_DB, so config.py's copy becomes unreachable
    in the desktop flow and config.py will be refactored to defer here — closing
    the last divergence path. That refactor TOUCHES THE BACKEND, so it waits for
    the cabling OK (see the note handed to Bilal). Until then: keep identical.
    """
    if sys.platform == "darwin":
        return Path(os.path.expanduser("~/Library/Application Support/Vokter"))
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
        return Path(base) / "Vokter"
    base = os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share"))
    return Path(base) / "vokter"


def resolve_data_dir(*, frozen: bool, home: Path, env_override: str | None) -> tuple[Path, str]:
    """Return (data_dir, why). Pure: reads env + the frozen flag, nothing else.

    env_override is a DIRECTORY (VOKTER_DESKTOP_DATA). It is NOT VOKTER_DB —
    config.py's VOKTER_DB is a *file* path (the .db itself). A Stage-2 caller
    that wants to honour VOKTER_DB must pass os.path.dirname(VOKTER_DB) here, or
    we would look for <db-file>/vokter.db and miss the data entirely.
    """
    if env_override:
        return Path(env_override).expanduser().resolve(), "env override (VOKTER_DESKTOP_DATA, a directory)"
    if frozen:
        return platform_data_dir(), "frozen binary → per-user application-data dir"
    return home / "runtime" / "data", "dev checkout → app-local runtime/data"


# --- 2. Guardrail ------------------------------------------------------------
def known_locations(home: Path) -> list[tuple[str, Path]]:
    """FIXED, curated list of places a prior Vokter db could live. Order = the
    order we'd offer them to the user. Never a filesystem-wide search."""
    locs: list[tuple[str, Path]] = [
        ("carpeta de datos del paquete (XDG)", platform_data_dir()),
        ("carpeta de desarrollo (runtime/data)", home / "runtime" / "data"),
    ]
    # Docker-era default was the linux XDG dir; include it explicitly so it is
    # still seen even if XDG_DATA_HOME is remapped elsewhere.
    docker_era = Path(os.path.expanduser("~/.local/share")) / "vokter"
    if docker_era not in [p for _, p in locs]:
        locs.append(("era-Docker (~/.local/share/vokter)", docker_era))
    return locs


def _dir_has_db(d: Path) -> bool:
    """Real filesystem check — the ONLY db-presence probe production uses."""
    return (d / DB_NAME).exists()


def gather_candidates(resolved_dir: Path, home: Path) -> list[tuple[str, Path]]:
    """Known locations (other than the resolved one) that actually hold a db.
    Reads the real filesystem; no simulation surface."""
    out: list[tuple[str, Path]] = []
    for label, path in known_locations(home):
        if path == resolved_dir:
            continue
        if _dir_has_db(path):
            out.append((label, path))
    return out


@dataclass
class Guardrail:
    triggered: bool
    resolved_dir: Path
    resolved_has_db: bool
    keychain: KeychainState
    candidates: list[tuple[str, Path]] = field(default_factory=list)  # other dirs holding a db

    def message(self) -> str:
        if not self.triggered:
            if self.resolved_has_db:
                return "OK — la carpeta de datos resuelta ya contiene tu Vokter."
            return ("OK — carpeta nueva y vacía, sin rastro de un Vokter anterior "
                    "en ningún sitio conocido ni en el llavero → arranque nuevo, correcto.")
        lines = [
            "⚠️  VOKTER NO ARRANCA EN VACÍO.",
            f"    Miraba en: {self.resolved_dir}  (sin base de datos)",
        ]
        if self.keychain is KeychainState.HAS_KEY:
            lines.append("    Pero el llavero GUARDA UNA LLAVE → aquí YA hubo un Vokter.")
        elif self.keychain is KeychainState.UNREACHABLE:
            lines.append("    Y NO pude comprobar el llavero (¿bloqueado / sin sesión?) →")
            lines.append("    no doy por hecho que estés vacío: mejor parar y preguntarte.")
        if self.candidates:
            lines.append("    Encontré datos existentes en:")
            for label, path in self.candidates:
                lines.append(f"      • {path}  ({label})")

        # Fix #2 — every warning must offer a usable action, never a dead end.
        lines.append("")
        lines.append("    Elige (nunca decidiré por ti):")
        if self.candidates:
            lines.append("      [1] Apúntame a los datos antiguos  (usar una de las de arriba)")
        else:
            lines.append("      [1] Indícame manualmente dónde están mis datos antiguos")
        lines.append("      [2] De verdad empiezo de cero        (crear un Vokter nuevo, vacío)")
        return "\n".join(lines)


def decide_guardrail(*, resolved_dir: Path, resolved_has_db: bool,
                     candidates: list[tuple[str, Path]], keychain: KeychainState) -> Guardrail:
    """PURE decision from already-gathered facts. No filesystem, no keychain, no
    simulation parameter — real boot and the dry run share exactly this logic.

    Trigger = the resolved dir is empty AND [a known location holds a db OR the
    keychain says (or MIGHT say) a key exists]. UNREACHABLE counts as 'might' —
    caution over a silent fresh start."""
    if resolved_has_db:
        return Guardrail(False, resolved_dir, True, keychain, candidates=[])
    key_signal = keychain in (KeychainState.HAS_KEY, KeychainState.UNREACHABLE)
    triggered = bool(candidates) or key_signal
    return Guardrail(triggered, resolved_dir, False, keychain, candidates)


def guardrail(*, resolved_dir: Path, keychain: KeychainState, home: Path) -> Guardrail:
    """Real-boot entry point: reads the real filesystem, gathers facts, and
    delegates to decide_guardrail. NO simulation surface — the production path
    cannot be told to pretend a dir is empty, not even by mistake."""
    return decide_guardrail(
        resolved_dir=resolved_dir,
        resolved_has_db=_dir_has_db(resolved_dir),
        candidates=gather_candidates(resolved_dir, home),
        keychain=keychain,
    )
