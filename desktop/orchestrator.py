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
import os
import secrets
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

# --- Layout -----------------------------------------------------------------
HERE      = Path(__file__).resolve().parent          # …/Vokter/desktop
REPO      = HERE.parent                               # …/Vokter
APP_DIR   = REPO / "app"                              # the FastAPI backend
RUNTIME   = HERE / "runtime"                          # app-local, git-ignored
VENV_PY   = RUNTIME / "venv" / "bin" / "python"       # backend interpreter
FROZEN_BIN = HERE / "freeze" / "dist" / "vokter-backend" / "vokter-backend"
OLLAMA_BIN = RUNTIME / "ollama" / "bin" / "ollama"    # native Ollama binary
DATA_DIR  = RUNTIME / "data"                          # SQLite DB + voice models
OLLAMA_MODELS_DIR = RUNTIME / "ollama-models"         # app-local model store
DBKEY_FILE = DATA_DIR / ".db_key"                     # Phase 1 only → keychain later

# --- Config (overridable via env) -------------------------------------------
# Native Ollama runs on 11435 ON PURPOSE — the leftover Docker Ollama squats on
# 11434, and we must never bind against it or we would be testing the very thing
# Phase 1 exists to eliminate.
OLLAMA_PORT = int(os.environ.get("VOKTER_DESKTOP_OLLAMA_PORT", "11435"))
# 8081 on purpose: the leftover Docker vokter-app squats on 8080. A desktop app
# also shouldn't collide with a dev server on the conventional 8080.
BACKEND_PORT = int(os.environ.get("VOKTER_DESKTOP_BACKEND_PORT", "8081"))
CHAT_MODEL  = os.environ.get("VOKTER_CHAT_MODEL",  "llama3.2:3b")
EMBED_MODEL = os.environ.get("VOKTER_EMBED_MODEL", "nomic-embed-text")

OLLAMA_HOST = f"127.0.0.1:{OLLAMA_PORT}"
OLLAMA_URL  = f"http://{OLLAMA_HOST}"

_procs: list[subprocess.Popen] = []


def log(msg: str) -> None:
    print(f"[orchestrator] {msg}", flush=True)


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
    """Load or mint a strong DB encryption key. Never the 'change-me' default,
    never empty. Stored 0600 in the app data dir for Phase 1; Phase 3 moves it
    to the OS keychain."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if DBKEY_FILE.exists():
        return DBKEY_FILE.read_text().strip()
    key = secrets.token_urlsafe(32)
    # Create the file already 0600 (O_EXCL to lose no race) — never let the
    # DB master key exist world-readable for the window before a later chmod.
    fd = os.open(DBKEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(key)
    log(f"minted a fresh DB encryption key → {DBKEY_FILE} (0600)")
    return key


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
    env = os.environ.copy()
    env["OLLAMA_HOST"] = OLLAMA_HOST          # bind + where the CLI looks
    env["OLLAMA_MODELS"] = str(OLLAMA_MODELS_DIR)  # app-local, sovereign store
    # Non-negotiable #2 ("zero hidden calls"): Ollama otherwise pings ollama.com
    # for cloud inference / web search / model recommendations. Off, hard.
    env["OLLAMA_NO_CLOUD"] = "1"
    log(f"starting native Ollama on {OLLAMA_URL} (models → {OLLAMA_MODELS_DIR})")
    _procs.append(subprocess.Popen([str(OLLAMA_BIN), "serve"], env=env))
    if not wait_http(f"{OLLAMA_URL}/api/version", timeout=30):
        die("native Ollama did not come up on " + OLLAMA_URL)
    log("native Ollama is up")


def ensure_models() -> None:
    """Pull the chat + embedding models into the app-local store if absent.
    First run downloads ~2 GB — that is expected, not a hang."""
    env = os.environ.copy()
    env["OLLAMA_HOST"] = OLLAMA_HOST
    env["OLLAMA_MODELS"] = str(OLLAMA_MODELS_DIR)
    for model in (CHAT_MODEL, EMBED_MODEL):
        log(f"ensuring model present: {model} (first run may download a lot)")
        rc = subprocess.call([str(OLLAMA_BIN), "pull", model], env=env)
        if rc != 0:
            die(f"failed to pull model {model}")


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
    flavour = backend_flavour()
    preflight(flavour)
    db_key = ensure_db_key()
    start_ollama()
    ensure_models()
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
