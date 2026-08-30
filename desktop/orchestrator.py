#!/usr/bin/env python3
"""Vokter desktop orchestrator — Phase 1 prototype.

One entry point that boots the two heavy pieces Docker used to run for us and
supervises both, with NO Docker involved:

  * the "brain"  — a NATIVE Ollama binary (app-local, not the system/Docker one)
  * the "engine" — our FastAPI backend (uvicorn), pointed at that native Ollama

Goal of this file: PROVE the difficult pieces start together outside Docker.
It is intentionally throwaway-quality. Later phases replace it:
  * Phase 2 — DONE: the backend can run as a frozen, self-contained executable
             (see backend_flavour(); build recipe in desktop/freeze/).
  * Phase 3 — the Electron shell performs this same supervision from the app,
             and the DB key moves from a local file to the OS keychain.

Non-negotiables honoured here:
  * Encryption is REAL — we generate a strong VOKTER_DB_KEY and refuse to accept
    a silent fall-back to plaintext (config.py degrades quietly if sqlcipher is
    missing; `verify_encryption.py` checks the result on disk).
  * No third parties — the only outbound traffic is the one-time model download
    from Ollama's registry (same as before) and local loopback between pieces.
"""
import hashlib
import json
import os
import secrets
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import keysource   # local, stdlib-only: the key-source DECISION (Phase 3.2)
import datadir     # local, stdlib-only: data-dir resolution + guardrail (Phase 3.3-B)
import model_pull  # local, stdlib-only: /api/pull stream → per-model bar (Phase 3.3-D)

# --- Layout -----------------------------------------------------------------
def _here() -> Path:
    """The desktop/ directory, resolved whether we run from source or frozen.

    From source: the folder this file lives in. Frozen (the 3.3-A --orchestrate
    binary): __file__ points inside the bundle, so derive desktop/ from the
    executable — desktop/freeze/dist/vokter-backend/<exe> → parents[3]. This is a
    DEV-LAYOUT assumption on purpose; Phase 3.3-B replaces it with a proper
    split of read-only resources vs the user's writable data dir. VOKTER_DESKTOP_HOME
    overrides either way (escape hatch / tests).
    """
    env = os.environ.get("VOKTER_DESKTOP_HOME")
    if env:
        return Path(env).resolve()
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parents[3]
    return Path(__file__).resolve().parent

HERE      = _here()                                  # …/Vokter/desktop
REPO      = HERE.parent                               # …/Vokter
APP_DIR   = REPO / "app"                              # the FastAPI backend
RUNTIME   = HERE / "runtime"                          # app-local, git-ignored
VENV_PY   = RUNTIME / "venv" / "bin" / "python"       # backend interpreter
FROZEN_BIN = HERE / "freeze" / "dist" / "vokter-backend" / "vokter-backend"
OLLAMA_BIN = RUNTIME / "ollama" / "bin" / "ollama"    # native Ollama binary
# Phase 3.3-B: the WRITABLE user-data dir is resolved from the ROBUST `frozen`
# signal (never folder-existence). Dev stays byte-identical (HERE/runtime/data);
# the packaged app lands in the per-user application-data dir. The RESOURCE paths
# above (venv, ollama, frozen bin) stay app-local under HERE on purpose.
DATA_DIR, _DATA_WHY = datadir.resolve_data_dir(
    frozen=bool(getattr(sys, "frozen", False)),
    home=HERE,
    env_override=os.environ.get("VOKTER_DESKTOP_DATA"),  # a DIRECTORY, not VOKTER_DB
)
# Phase 3.3-C (C2): the model store and Ollama's home MUST be writable, so they
# hang off DATA_DIR (the per-user writable dir from 3.3-B), NOT RUNTIME — under a
# packaged install RUNTIME is read-only /opt and `ollama serve`'s mkdir would die
# here. OLLAMA_HOME keeps Ollama's keypair app-local too (no stray ~/.ollama),
# matching Vokter's sovereignty stance. Dev: DATA_DIR = HERE/runtime/data.
OLLAMA_MODELS_DIR = DATA_DIR / "ollama-models"        # writable model store
OLLAMA_HOME       = DATA_DIR / "ollama-home"          # keypair etc., app-local
# Phase 3.3-C (C2): the piper + whisper voice models ship inside the package as a
# READ-ONLY resource under HERE, and get seeded into DATA_DIR/models on first run
# (see seed_voice). This keeps "speaks + listens OFFLINE out of the box" true —
# the property the 9/9 clean-machine test certified — with no hidden first-use
# download. RESOURCE path (like venv/ollama), not writable data.
VOICE_SEED_DIR = HERE / "runtime" / "voice-seed"      # bundled piper + whisper
DBKEY_FILE = DATA_DIR / ".db_key"                     # Phase 1 only → keychain later

# --- Config (overridable via env) -------------------------------------------
# Native Ollama runs on 11435 ON PURPOSE — the leftover Docker Ollama squats on
# 11434, and we must never bind against it or we would be testing the very thing
# Phase 1 exists to eliminate.
OLLAMA_PORT = int(os.environ.get("VOKTER_DESKTOP_OLLAMA_PORT", "11435"))
# 8081 on purpose: the leftover Docker vokter-app squats on 8080. A desktop app
# also shouldn't collide with a dev server on the conventional 8080.
BACKEND_PORT = int(os.environ.get("VOKTER_DESKTOP_BACKEND_PORT", "8081"))
CHAT_MODEL  = os.environ.get("VOKTER_CHAT_MODEL",  "qwen2.5:3b")  # first-run pull default (see
EMBED_MODEL = os.environ.get("VOKTER_EMBED_MODEL", "bge-m3")  # config.py rationale)

# Sovereign mirror for GGUF models we host ourselves (same host as the Catalan chat model —
# see app/hardware.py MODEL_ASSETS_BASE). The embedder is a MIRROR model: its GGUF is fetched
# from OUR release and sideloaded into Ollama at first run, NOT pulled from the Ollama registry
# or HuggingFace (the HF pull hits an auth-realm bug, and sovereignty forbids the dependency).
MODEL_ASSETS_BASE = os.environ.get(
    "VOKTER_MODEL_ASSETS_BASE",
    "https://github.com/vokter-eu/Vokter/releases/download/models-v1",
).rstrip("/")
# Models provisioned by SIDELOAD (download our GGUF → verify sha256 → create in Ollama) instead
# of a registry pull. bge-m3 is an EMBEDDING GGUF (bert arch): passthrough template, NO chat
# stop/num_ctx. sha256/size pin OUR hosted copy (Q4_K_M, 1024-dim). Matches app/hardware.py.
MIRROR_MODELS = {
    "bge-m3": {
        "gguf":     "bge-m3-Q4_K_M.gguf",
        "sha256":   "6d39681b26c61279ac1f82db35a04a05009e94c415b51c858ff571489a82fc06",
        "size":     437778496,
        "template": "{{ .Prompt }}",
    },
}

OLLAMA_HOST = f"127.0.0.1:{OLLAMA_PORT}"
OLLAMA_URL  = f"http://{OLLAMA_HOST}"

_procs: list[subprocess.Popen] = []


def log(msg: str) -> None:
    print(f"[orchestrator] {msg}", flush=True)


# Phase 3.3-D: model-download progress for the Electron loading screen. We print
# one machine-readable line per meaningful step to stdout; main.js already reads
# this child's stdout (it only forwarded it to the terminal before) and, in step
# 2b, parses lines with this exact prefix and relays them to loading.html. The
# prefix is deliberately distinct from the human "[orchestrator] " lines.
PROGRESS_PREFIX = "[progress] "


def emit_progress(event: dict) -> None:
    print(PROGRESS_PREFIX + json.dumps(event, separators=(",", ":")), flush=True)


# Phase 3.3 (start-fresh button): when the keychain guardrail halts a blank boot,
# the Electron window shows a clickable "[2] start fresh". Clicking it re-spawns
# this orchestrator with VOKTER_START_FRESH=1 (the "answer" is a boot parameter,
# not a live channel). We emit a STRUCTURED [guardrail] line (facts only — the UI
# composes its own English copy; the Spanish guardrail message stays in the logs).
START_FRESH_ENV = "VOKTER_START_FRESH"
GUARDRAIL_PREFIX = "[guardrail] "


def _start_fresh_requested() -> bool:
    return os.environ.get(START_FRESH_ENV, "").strip() == "1"


def emit_guardrail(event: dict) -> None:
    print(GUARDRAIL_PREFIX + json.dumps(event, separators=(",", ":")), flush=True)


def backend_flavour() -> str:
    """Which backend to launch: 'venv' or 'frozen'.

    Dev default is the venv — never silently run a possibly-stale frozen
    build next to freshly edited app/ code. A user machine has no venv, so
    it gets the frozen binary automatically. VOKTER_DESKTOP_BACKEND forces
    either one (that is also how the frozen path is tested on a dev box).
    """
    choice = os.environ.get("VOKTER_DESKTOP_BACKEND", "").strip().lower()
    if choice in ("venv", "frozen"):
        return choice
    if choice:
        log(f"FATAL: VOKTER_DESKTOP_BACKEND must be 'venv' or 'frozen', not {choice!r}")
        sys.exit(1)
    return "venv" if VENV_PY.exists() else "frozen"


def preflight(flavour: str) -> None:
    """Fail loud and early with an actionable message if setup is missing."""
    problems = []
    if not OLLAMA_BIN.exists():
        problems.append(f"native Ollama not found at {OLLAMA_BIN} — run desktop/setup.sh")
    if flavour == "venv" and not VENV_PY.exists():
        problems.append(f"backend venv not found at {VENV_PY} — run desktop/setup.sh")
    if flavour == "frozen" and not FROZEN_BIN.exists():
        problems.append(f"frozen backend not found at {FROZEN_BIN} — build it first "
                        f"(see desktop/freeze/README.md)")
    if problems:
        for p in problems:
            log("MISSING: " + p)
        sys.exit(1)


def ensure_db_key() -> str:
    """Load or mint the DB encryption key — STAGE 3b: keychain-first.

    Precedence is now KEYCHAIN-FIRST with the file as the proven fallback, driven
    by keysource.decide() over the real world (file / keychain / DB facts). The
    golden rule holds: if the keychain can't help we fall back to the file key,
    and a new key is minted in exactly ONE situation (proven-empty keychain, no
    file, no DB) — never over data we merely failed to unlock.

    Emergency switch: VOKTER_KEY_SOURCE=file forces file-only mode and skips the
    keychain ENTIRELY — not even the availability write-probe runs, so "ignore
    the keychain" is literally true (no probe item, slot untouched).

    Effects are executed here (decide() only decides): seed the keychain on the
    migration/mint paths, and re-create the file backup where needed — BOTH
    best-effort, NEVER a boot condition (a keychain or backup-write hiccup must
    not lock anyone out).
    """
    db_path = DATA_DIR / "vokter.db"
    override = os.environ.get(keysource.OVERRIDE_ENV)  # None unless the switch is set

    if override == keysource.SRC_FILE:
        # Emergency switch: do NOT touch the keychain at all (skip the write-probe).
        file_state, file_key = keysource.read_file_key(DBKEY_FILE)
        facts = dict(file_state=file_state, file_key=file_key,
                     db_present=db_path.exists(),
                     kc_state=keysource.KC_UNAVAILABLE, kc_key=None)
    else:
        facts = _keychain_first_facts(db_path)

    # Phase 3.3-B guardrail — gate BEFORE the key decision, and before we create
    # the dir or mint a key: never silently boot a BLANK Vokter over existing data.
    guard = _guardrail_check(facts, override)
    if guard.triggered:
        # Halt (default) OR, if the user clicked [2] "start fresh", proceed
        # create-only. Either way we do NOT fall through to the normal key path.
        return _handle_guardrail(guard, facts)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    decision = keysource.decide(**facts, opens_db=_db_opener(db_path), override=override)
    log(f"key source → situation {decision.situation}, source={decision.source} "
        f"— {decision.reason}")
    if decision.warn:
        log("WARNING (key source): " + decision.reason)
    if decision.fail:
        die("refusing to boot without a usable DB key: " + decision.reason)

    if decision.mint:
        key = _mint_key_file()
    else:
        key = decision.key
        if decision.recreate_file:
            _recreate_key_file(key)  # best-effort, never a boot condition
    if decision.seed_keychain:
        _seed_keychain(key)          # best-effort, never a boot condition
    return key


def _guardrail_check(facts: dict, override: str | None) -> "datadir.Guardrail":
    """Phase 3.3-B — decide whether booting now would be a BLANK Vokter over data.

    Runs BEFORE the key decision and REUSES the keychain state keysource already
    gathered (no second probe). Returns the Guardrail; the caller halts (default)
    or, on an explicit [2], proceeds create-only. PURE apart from the guardrail's
    own filesystem read — no side effects here.
    """
    if override == keysource.SRC_FILE:
        # Keychain DELIBERATELY skipped (emergency file mode): that is "we chose
        # not to ask", NOT the cautious "we couldn't ask", so it carries no
        # keychain signal here. Prior-DB files still protect via the candidates.
        kc = datadir.KeychainState.NO_KEY
    elif facts["kc_state"] == keysource.KC_UNAVAILABLE:
        kc = datadir.KeychainState.UNREACHABLE
    elif facts["kc_state"] == keysource.KC_HAS_KEY:
        kc = datadir.KeychainState.HAS_KEY
    else:  # KC_EMPTY — proven reachable and empty
        kc = datadir.KeychainState.NO_KEY

    return datadir.guardrail(resolved_dir=DATA_DIR, keychain=kc, home=HERE)


def _handle_guardrail(guard: "datadir.Guardrail", facts: dict) -> str:
    """The guardrail fired. Two paths:

    * DEFAULT (no [2]): halt loudly and start NOTHING — emit a structured
      [guardrail] line so the window can offer the choice, log the human message,
      and die(). This is byte-for-byte the old "never an empty Vokter" behaviour
      plus the one new stdout line the UI needs.
    * START FRESH ([2], VOKTER_START_FRESH=1): the user has CONFIRMED a fresh
      start → resolve a key create-only (never touches existing data or the
      keychain) and proceed. die() only if it cannot proceed without clobbering.
    """
    if _start_fresh_requested():
        log("start-fresh CONFIRMED by the user — proceeding create-only; no "
            "existing database, key file, keychain slot, or prior location is touched")
        key = _start_fresh_key(facts)  # owns its own mkdir (create-only)
        if key is None:
            die("cannot start fresh without overwriting an existing key file — "
                "refusing (it may be your real key)")
        return key

    # Default: halt. Structured signal for the UI (facts only), then the human
    # message to the logs, then die — exactly as before.
    emit_guardrail({
        "triggered": True,
        "has_candidates": bool(guard.candidates),
        "keychain": guard.keychain.name.lower(),  # has_key | unreachable | no_key
    })
    for line in guard.message().splitlines():
        log(line)
    die("refusing to start an EMPTY Vokter (see the options above) — no "
        "backend started, no database created")


def _keychain_first_facts(db_path: Path) -> dict:
    """Read the real world for the keychain-first decision: file + DB + keychain.

    Availability is proven with the write-probing keychain.is_available (which
    distinguishes PROVEN-EMPTY from UNAVAILABLE — the whole reason a naive port
    would lock a user out). Any keychain problem (missing deps, locked, hung)
    degrades to UNAVAILABLE, so decide() falls back to the proven file key and
    never mints over data. NOTE: is_available writes a self-deleting probe item;
    the read-only dry run uses is_reachable_readonly instead, so a real boot may
    land in Situation 3 (file) where the dry run reported 2 — still safe, no
    lock-out, just no seed that boot.
    """
    try:
        import keychain
        return keysource.gather_facts(
            file_path=DBKEY_FILE, db_path=db_path,
            kc_available=keychain.is_available, kc_get=keychain.get_key,
        )
    except Exception as exc:  # keychain deps missing → treat as unavailable
        log(f"keychain not consultable ({exc!r}); treating it as unavailable")
        file_state, file_key = keysource.read_file_key(DBKEY_FILE)
        return dict(file_state=file_state, file_key=file_key,
                    db_present=db_path.exists(),
                    kc_state=keysource.KC_UNAVAILABLE, kc_key=None)


def _db_opener(db_path: Path):
    """Validator for decide(): does a candidate key open the real DB? Shells out
    to whatever carries sqlcipher3 (venv in dev, frozen --verify-key on a user
    machine). Never raises → any problem is a plain False, degrading to the file."""
    def opens(key: str) -> bool:
        return keysource.key_opens_db(key, db_path, venv_py=VENV_PY, frozen_bin=FROZEN_BIN)
    return opens


def _write_key_file_excl(key: str) -> None:
    """Write the key file with O_EXCL: it FAILS (FileExistsError) rather than
    overwrite an existing key — the OS enforces "never clobber a key". Also loses
    no race and never leaves the master key world-readable (0600 from creation)."""
    fd = os.open(DBKEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(key)


def _mint_key_file() -> str:
    key = secrets.token_urlsafe(32)
    _write_key_file_excl(key)
    log(f"minted a fresh DB encryption key → {DBKEY_FILE} (0600)")
    return key


def _start_fresh_key(facts: dict) -> str | None:
    """Create-only key resolution for a user-CONFIRMED fresh start ([2]). Returns
    the key to use, or None if it cannot proceed without overwriting an existing
    key file (then we refuse — that file may be the user's real key).

    The safety invariant is OWNED here, not inherited from helpers: this writes
    ONLY the resolved data dir's key file, and only through an O_EXCL create —
    it NEVER truncates, NEVER seeds/writes the keychain (an UNREACHABLE slot may
    hold the real user's key), and NEVER touches candidate DBs or any other
    location. Idempotent: a second [2] after a prior mint reuses the file key.
    """
    if facts["file_state"] == keysource.FILE_PRESENT and facts["file_key"]:
        log("start-fresh: reusing the existing key file (idempotent; no new key)")
        return facts["file_key"]
    # No readable file key → we must create one. Value: adopt the keychain's key
    # if we can READ it (HAS_KEY, a read not a write), otherwise mint a new one.
    if facts["kc_state"] == keysource.KC_HAS_KEY and facts["kc_key"]:
        key, why = facts["kc_key"], "adopting the keychain key"
    else:
        key, why = secrets.token_urlsafe(32), "minting a new key"
    # First boot on a clean machine: the resolved data dir usually does NOT exist
    # yet (this is the first data-touching step). Create it before the O_EXCL write
    # so it fails only on a real pre-existing key file, not on a missing parent.
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        _write_key_file_excl(key)
    except FileExistsError:
        log("start-fresh: a key file exists but is UNREADABLE — refusing to "
            "overwrite it (it may be your real key); cannot start fresh safely")
        return None
    log(f"start-fresh: {why} → wrote {DBKEY_FILE} (0600). Keychain untouched.")
    return key


def _recreate_key_file(key: str) -> None:
    """Best-effort re-creation of the .db_key backup (situations 4b/4c). NEVER a
    boot condition: if the keychain key opened the DB but rewriting the file
    fails (e.g. it was unreadable by perms → probably not writable either), the
    boot still succeeds with that key; we only log the miss. Written atomically
    (temp + os.replace), already 0600."""
    try:
        tmp = DBKEY_FILE.with_name(DBKEY_FILE.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(key)
        os.replace(tmp, DBKEY_FILE)
        log(f"recreated the .db_key backup (0600) → {DBKEY_FILE}")
    except Exception as exc:
        log(f"could not recreate the .db_key backup ({exc!r}); continuing — "
            f"recreate is best-effort, never a boot condition")


def _seed_keychain(key: str) -> None:
    """Best-effort file→keychain seed (migration and first-mint paths). NEVER
    breaks the boot: keychain.mirror swallows every keychain hiccup, and the file
    remains the source of truth regardless."""
    try:
        import keychain
        status = keychain.mirror(key)
        log(f"keychain seed → {status}")
    except Exception as exc:
        log(f"keychain seed skipped ({exc!r}); the file remains the source of truth")


def wait_http(url: str, timeout: float = 60.0) -> bool:
    """Poll an HTTP endpoint until it answers or we give up."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2):
                return True
        except Exception:
            time.sleep(0.5)
    return False


def _ollama_already_up() -> bool:
    """True if an Ollama is already answering on our port. Prevents spawning a
    second `ollama serve` that dies on EADDRINUSE — which the supervise loop
    would then see as a dead child and use to tear the whole orchestrator down."""
    try:
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/version", timeout=2):
            return True
    except Exception:
        return False


def start_ollama() -> None:
    OLLAMA_MODELS_DIR.mkdir(parents=True, exist_ok=True)
    if _ollama_already_up():
        log(f"an Ollama is already serving on {OLLAMA_URL} — reusing it, not "
            f"starting a second instance (stop it first if it isn't ours)")
        return
    OLLAMA_HOME.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["OLLAMA_HOST"] = OLLAMA_HOST          # bind + where the CLI looks
    env["OLLAMA_HOME"] = str(OLLAMA_HOME)     # keypair app-local, no stray ~/.ollama
    env["OLLAMA_MODELS"] = str(OLLAMA_MODELS_DIR)  # app-local, sovereign store
    # Non-negotiable #2 ("zero hidden calls"): Ollama otherwise pings ollama.com
    # for cloud inference / web search / model recommendations. Off, hard.
    env["OLLAMA_NO_CLOUD"] = "1"
    # Keep the loaded model resident instead of evicting it after 5 min idle. On slow
    # local CPU the cold model-load is the ~10-15s "dead" wait before the first token;
    # keeping it in RAM makes every message after the first (and, with the startup
    # pre-warm, the first too) hit the warm ~2s path. Costs one model's RAM (~3.3 GB for
    # a 4B) held for the session — a deliberate usability trade on this hardware.
    env["OLLAMA_KEEP_ALIVE"] = os.environ.get("VOKTER_OLLAMA_KEEP_ALIVE", "-1")
    log(f"starting native Ollama on {OLLAMA_URL} (models → {OLLAMA_MODELS_DIR})")
    _procs.append(subprocess.Popen([str(OLLAMA_BIN), "serve"], env=env))
    if not wait_http(f"{OLLAMA_URL}/api/version", timeout=30):
        die("native Ollama did not come up on " + OLLAMA_URL)
    log("native Ollama is up")


def ensure_models() -> None:
    """Pull the chat + embedding models into the app-local store if absent,
    reporting real progress to the UI. First run downloads ~2 GB — that is
    expected, not a hang.

    Same operation as before, moved from the `ollama pull` CLI to the running
    server's HTTP /api/pull so we can stream progress into the Electron window
    (Phase 3.3-D). Behaviour kept IDENTICAL on the three things that matter:
      * idempotency — /api/pull is the same op as the CLI: if the model is
        already present it streams a few status lines and finishes without
        re-downloading a byte (its presence check is the manifest).
      * failure    — any transport error, an {"error": …} line, or a stream that
        ends without "success" → die() (loud, non-zero exit), as before.
      * store      — the download location is owned by the RUNNING server
        (OLLAMA_MODELS, set in start_ollama), NOT by the pull call. Talking to it
        over HTTP cannot move the store; this function no longer sets any env.
    An interrupted download can never masquerade as complete: Ollama names blobs
    by content sha256 (final name appears only after verify) and writes the
    manifest last, so a half-pull leaves no manifest → not present → resumes next
    run.
    """
    models = (CHAT_MODEL, EMBED_MODEL)
    for i, model in enumerate(models, start=1):
        # Offline-safe: /api/pull still contacts the registry to check the manifest, so a
        # network blip would fail the pull and die() even when the model is already here.
        # Skip the network entirely when the model is present locally — only pull when it's
        # genuinely missing (first run), which legitimately needs the network.
        if _model_present(model):
            log(f"model already present, skipping pull: {model}")
            continue
        log(f"ensuring model present: {model} (first run may download a lot)")
        # A mirror model (our sovereign GGUF, e.g. the bge-m3 embedder) isn't in the Ollama
        # registry — sideload it from our host instead of a registry pull. Same progress
        # frames, so the loading bar is identical ('N of 2'). Everything else registry-pulls.
        if model in MIRROR_MODELS:
            _sideload_streaming(model, MIRROR_MODELS[model], index=i, count=len(models))
        else:
            _pull_streaming(model, index=i, count=len(models))


def _model_present(model: str) -> bool:
    """True if `model` is already in the local store, so starting needs no network.

    Matches Ollama's implicit ':latest' tag. On any error reading the local /api/tags we
    return False (fall through to the pull) rather than wrongly skip — the tags endpoint is
    the same local server we just started, so this normally succeeds and keeps boot offline-safe."""
    want = {model, model if ":" in model else f"{model}:latest"}
    try:
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=10) as resp:
            have = {m.get("name", "") for m in json.load(resp).get("models", [])}
    except (urllib.error.URLError, OSError, json.JSONDecodeError, ValueError):
        return False
    return bool(want & have)


def _pull_streaming(model: str, index: int, count: int) -> None:
    """Stream one model pull from the local Ollama server, feeding model_pull and
    emitting a per-model progress line per meaningful step. die()s on failure.

    We emit STRUCTURED index/count (not a localized "modelo 1 de 2" string): the
    UI owns the wording, so the on-screen language lives in exactly one place
    (electron/loading.js) — cleaner for real i18n later, and it keeps Ollama
    jargon (the model id) off the orchestrator→UI wire entirely."""
    parser = model_pull.PullParser()
    last_key = None          # (phase, percent@0.1, indeterminate) — throttles output
    saw_success = False
    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/pull",
        data=json.dumps({"model": model, "stream": True}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        # timeout is per-socket-read: a download that keeps sending progress never
        # hits it; a genuinely stalled connection eventually does → die, not hang.
        with urllib.request.urlopen(req, timeout=120) as resp:
            for raw in resp:
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue  # tolerate a stray non-JSON line, keep streaming
                snap = parser.update(obj)
                if snap.phase == model_pull.PHASE_ERROR:
                    die(f"failed to pull model {model}: {snap.error}")
                if snap.phase == model_pull.PHASE_DONE:
                    saw_success = True
                key = (snap.phase, round(snap.percent, 1), snap.indeterminate)
                if key != last_key:
                    last_key = key
                    event = {"model": model, "index": index, "count": count,
                             **snap.as_event()}
                    emit_progress(event)
    except (urllib.error.URLError, OSError) as e:
        # connection refused / dropped mid-download / read timeout
        die(f"failed to pull model {model}: {e}")
    if not saw_success:
        # stream ended without a "success" line → incomplete pull
        die(f"failed to pull model {model}: download ended before completion")


def _emit(model, index, count, snap) -> None:
    emit_progress({"model": model, "index": index, "count": count, **snap.as_event()})


def _sideload_streaming(model: str, meta: dict, index: int, count: int) -> None:
    """Provision a MIRROR model (our sovereign GGUF) into Ollama, streaming the same
    per-model progress as _pull_streaming so the loading bar shows 'N of 2' identically.

    Steps, all feeding model_pull.Snapshot events: download OUR GGUF (real %/bytes) →
    verify sha256 → push the blob to Ollama if absent → /api/create (embedding recipe:
    passthrough template, no stop). This is the pre-backend counterpart of the backend's
    _sideload_gen — mirror models used to be provisioned ONLY post-boot via the picker,
    but the DEFAULT embedder must be here, before the backend and the first retrieval.
    die()s (loud, non-zero) on any failure, exactly like a failed registry pull."""
    digest = "sha256:" + meta["sha256"]
    url = f"{MODEL_ASSETS_BASE}/{meta['gguf']}"
    total = int(meta["size"])
    cache = DATA_DIR / "gguf-cache"
    cache.mkdir(parents=True, exist_ok=True)
    tmp = cache / (meta["gguf"] + ".part")

    # 1) download from OUR host, hash as we go, report a real bar (never a hung spinner)
    h = hashlib.sha256(); done = 0; last = -1.0
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "vokter-orchestrator"})
        with urllib.request.urlopen(req, timeout=120) as resp, open(tmp, "wb") as f:
            # HTTP sets status=200; a file:// URL (tests/local mirror) sets it to None — both OK.
            if getattr(resp, "status", 200) not in (200, None):
                die(f"embedder host returned {resp.status} for {url}")
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk); h.update(chunk); done += len(chunk)
                pct = round(100.0 * done / total, 1) if total else 0.0
                if pct != last:
                    last = pct
                    _emit(model, index, count, model_pull.Snapshot(
                        phase=model_pull.PHASE_DOWNLOADING, completed=done, total=total,
                        percent=pct, indeterminate=False))
    except (urllib.error.URLError, OSError) as e:
        die(f"failed to download embedder {model} from {url}: {e}")
    if h.hexdigest() != meta["sha256"]:
        try: os.remove(tmp)
        except OSError: pass
        die(f"embedder {model} download checksum mismatch (host tampered or truncated)")

    # 2) push blob if Ollama doesn't already have it, then create the model (indeterminate)
    _emit(model, index, count, model_pull.Snapshot(
        phase=model_pull.PHASE_WRITING, completed=total, total=total, percent=99.0,
        indeterminate=True))
    try:
        present = False
        try:
            with urllib.request.urlopen(urllib.request.Request(
                    f"{OLLAMA_URL}/api/blobs/{digest}", method="HEAD"), timeout=30) as r:
                present = getattr(r, "status", 200) == 200
        except urllib.error.HTTPError as e:
            present = e.code == 200        # 404 → not present → push below
        if not present:
            size = os.path.getsize(tmp)
            with open(tmp, "rb") as body:
                push = urllib.request.Request(
                    f"{OLLAMA_URL}/api/blobs/{digest}", data=body, method="POST",
                    headers={"Content-Type": "application/octet-stream",
                             "Content-Length": str(size)})
                with urllib.request.urlopen(push, timeout=1800) as r:
                    if getattr(r, "status", 200) not in (200, 201):
                        die(f"embedder {model} blob import failed ({r.status})")
        create = urllib.request.Request(
            f"{OLLAMA_URL}/api/create",
            data=json.dumps({"model": model, "files": {meta["gguf"]: digest},
                             "template": meta["template"], "stream": True}).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        saw_success = False
        with urllib.request.urlopen(create, timeout=1800) as resp:
            for raw in resp:
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if o.get("error"):
                    die(f"embedder {model} create failed: {o['error']}")
                if o.get("status") == "success":
                    saw_success = True
    except (urllib.error.URLError, OSError) as e:
        die(f"failed to import embedder {model} into Ollama: {e}")
    if not saw_success:
        die(f"embedder {model} create ended before completion")
    try: os.remove(tmp)          # Ollama has its own blob copy now
    except OSError: pass
    _emit(model, index, count, model_pull.Snapshot(
        phase=model_pull.PHASE_DONE, completed=total, total=total, percent=100.0,
        indeterminate=False))
    log(f"sideloaded mirror model: {model}")


def seed_voice() -> None:
    """Copy the bundled piper + whisper models into the writable data dir on first
    run, so the app speaks and listens OFFLINE from the very first launch — no
    hidden first-use download (Vokter non-negotiable #2). Idempotent: only fills
    in what's missing, never overwrites the user's models. A dev checkout without
    the build-prep seed (VOICE_SEED_DIR absent) simply falls back to the code's
    existing on-demand download path."""
    models = DATA_DIR / "models"
    # (seed subdir, destination subdir, sentinel that proves it's already there)
    jobs = [
        ("piper",   "piper",   "en_US-lessac-medium.onnx"),
        ("whisper", "whisper", "base-int8/model.bin"),
    ]
    for name, dst_name, sentinel in jobs:
        src = VOICE_SEED_DIR / name
        dst = models / dst_name
        if not src.is_dir():
            continue                          # not bundled → on-demand fallback
        if (dst / sentinel).exists():
            continue                          # already present → leave user's dir
        dst.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, dst, dirs_exist_ok=True)
        log(f"seeded voice model '{name}' → {dst}")


def start_backend(db_key: str, flavour: str) -> None:
    env = os.environ.copy()
    env["VOKTER_OLLAMA_URL"] = OLLAMA_URL        # ← native Ollama, not Docker DNS
    env["VOKTER_DB_KEY"]     = db_key            # real encryption
    env["VOKTER_DB"]         = str(DATA_DIR / "vokter.db")
    env["VOKTER_VOICE_MODELS_DIR"] = str(DATA_DIR / "models")
    env["VOKTER_CHAT_MODEL"]  = CHAT_MODEL
    env["VOKTER_EMBED_MODEL"] = EMBED_MODEL
    # The frozen binary (Phase 2+) reads these instead of uvicorn CLI flags —
    # export them so every backend flavour binds where wait_http() checks.
    env["VOKTER_BIND"] = "127.0.0.1"
    env["VOKTER_PORT"] = str(BACKEND_PORT)
    if flavour == "frozen":
        # Self-contained: no interpreter, no cwd dependence.
        cmd, cwd = [str(FROZEN_BIN)], None
        log(f"starting backend (FROZEN binary {FROZEN_BIN}) "
            f"on http://127.0.0.1:{BACKEND_PORT}")
    else:
        cmd = [str(VENV_PY), "-m", "uvicorn", "main:app",
               "--host", "127.0.0.1", "--port", str(BACKEND_PORT)]
        cwd = str(APP_DIR)
        log(f"starting backend (venv uvicorn) on http://127.0.0.1:{BACKEND_PORT}")
    _procs.append(subprocess.Popen(cmd, cwd=cwd, env=env))
    if not wait_http(f"http://127.0.0.1:{BACKEND_PORT}/", timeout=60):
        die("backend did not come up")
    log(f"backend is up — open http://127.0.0.1:{BACKEND_PORT}")


def shutdown(signum=None, frame=None, code: int = 0) -> None:
    log("shutting down — stopping child processes")
    for p in reversed(_procs):
        if p.poll() is None:
            p.terminate()
    for p in reversed(_procs):
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            p.kill()
    sys.exit(code)


def die(msg: str) -> None:
    # Exit non-zero: a fatal boot failure must NOT look like success to whatever
    # supervises this (Phase-3 Electron, CI). A clean Ctrl-C still exits 0 above.
    log("FATAL: " + msg)
    shutdown(code=1)


def main() -> None:
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    log(f"desktop home → {HERE}  (frozen={getattr(sys, 'frozen', False)})")
    log(f"data dir → {DATA_DIR}  ({_DATA_WHY})")
    flavour = backend_flavour()
    preflight(flavour)
    db_key = ensure_db_key()
    start_ollama()
    ensure_models()
    seed_voice()
    start_backend(db_key, flavour)
    log("all pieces are up. Ctrl-C to stop. Now open the UI and verify a chat.")
    # Supervise: if either child dies, take the whole thing down.
    while True:
        for p in _procs:
            if p.poll() is not None:
                die(f"a child process exited (pid {p.pid}, code {p.returncode})")
        time.sleep(1)


if __name__ == "__main__":
    main()
