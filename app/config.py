import os
import sys
import sqlite3 as _plain_sqlite3

VOKTER_VERSION = "0.14.0"  # single source of truth — used by main.py and the agent card

_FROZEN = bool(getattr(sys, "frozen", False))  # running as the desktop binary


def _die(message: str) -> None:
    # Fatal misconfiguration: explain in plain words and refuse to run.
    # A desktop user must never see a raw traceback — or a silently
    # weakened Vokter.
    print(message, file=sys.stderr)
    sys.exit(1)


def _default_db_path() -> str:
    # Docker/venv keep the historical /data. The packaged (frozen) desktop app
    # does NOT guess its data location here: the desktop orchestrator is the
    # single source of truth for that path (desktop/datadir.py) and ALWAYS passes
    # it as VOKTER_DB (alongside the key). So a frozen process that reaches this
    # point was hand-launched without VOKTER_DB — it cannot store data safely
    # (it would also fail-closed on the missing key below). Refuse now, rather
    # than compute a path that could silently diverge from datadir.py's.
    if not _FROZEN:
        return "/data/vokter.db"
    _die(
        "ERROR: Vokter cannot start safely.\n\n"
        "This packaged Vokter was started without VOKTER_DB, so it does not\n"
        "know where your data lives. Start Vokter through the desktop app,\n"
        "which sets that for you.\n\n"
        "If you are launching this binary by hand, set VOKTER_DB (and\n"
        "VOKTER_DB_KEY) to the data you want to open."
    )
    raise SystemExit(1)  # unreachable: _die() already exited; keeps the -> str contract honest


OLLAMA_URL  = os.getenv("VOKTER_OLLAMA_URL",  "http://ollama:11434")
CHAT_MODEL  = os.getenv("VOKTER_CHAT_MODEL",  "qwen2.5:3b")  # default: the CPU sweet spot —
                                                            # NON-SWA (prompt cache works, so
                                                            # first-token ~1s vs gemma's ~10s on
                                                            # a weak CPU), doesn't dump stored
                                                            # memory like llama3.2:3b, decent
                                                            # es/ca. gemma3:4b (quality, slow on
                                                            # weak CPU) and llama3.2:3b (lightest)
                                                            # remain pickable in the UI.
EMBED_MODEL = os.getenv("VOKTER_EMBED_MODEL", "nomic-embed-text")  # embedder unchanged
DB_PATH     = os.getenv("VOKTER_DB") or _default_db_path()
DATA_DIR    = os.path.dirname(DB_PATH) or "/data"
DB_KEY      = os.getenv("VOKTER_DB_KEY",      "")

try:
    os.makedirs(DATA_DIR, exist_ok=True)
except OSError as exc:
    _die(
        "ERROR: Vokter could not create its data folder:\n"
        f"  {DATA_DIR}\n"
        f"({exc.strerror or exc})\n\n"
        "Check that you can write to that location, or set VOKTER_DB\n"
        "to a folder of your choice."
    )

CHUNK_SIZE    = 900
CHUNK_OVERLAP = 150
TOP_K         = 4
# max messages kept per conversation (= 10 turns)
# WARNING: process-local dict — do NOT run with multiple uvicorn workers
MAX_HISTORY   = 20

# Frozen default follows DATA_DIR so voice models land in the same per-user
# application-data folder as the DB, not in Docker's /data.
VOICE_MODELS_DIR = os.getenv("VOKTER_VOICE_MODELS_DIR") or (
    os.path.join(DATA_DIR, "models") if _FROZEN else "/data/models"
)
WHISPER_MODEL    = os.getenv("VOKTER_WHISPER_MODEL",    "small")  # small int8 ≈ 460MB: much
WHISPER_DEVICE   = os.getenv("VOKTER_WHISPER_DEVICE",   "cpu")     # better than 'base' on es/ca,
                                                                   # still usable on a CPU i3

# Bind address/port for entry points that serve the app themselves (the frozen
# desktop binary). Docker and the orchestrator pass uvicorn CLI flags instead,
# but every launch path must honour these same env names. Loopback by default:
# the admin API is unprotected when ADMIN_TOKEN is empty (see below).
BIND = os.getenv("VOKTER_BIND", "127.0.0.1")
try:
    PORT = int(os.getenv("VOKTER_PORT", "8080"))
except ValueError:
    PORT = 8080

EMAIL_IMAP_HOST = os.getenv("VOKTER_EMAIL_IMAP_HOST",  "")
EMAIL_IMAP_PORT = int(os.getenv("VOKTER_EMAIL_IMAP_PORT", "993"))
EMAIL_USER      = os.getenv("VOKTER_EMAIL_USER",        "")
EMAIL_PASSWORD  = os.getenv("VOKTER_EMAIL_PASSWORD",    "")
EMAIL_FOLDER    = os.getenv("VOKTER_EMAIL_FOLDER",      "INBOX")
EMAIL_MAX_SYNC  = int(os.getenv("VOKTER_EMAIL_MAX_SYNC", "200"))

# A2A (Agent2Agent) HTTP transport — Phase 6 interoperability.
# VOKTER_A2A_URL: the *publicly reachable* JSON-RPC endpoint for this Vokter.
#   Leave empty (default) when Vokter is only reachable locally / via Nostr —
#   the agent card then advertises Nostr, not an unreachable localhost URL.
#   Set it (e.g. via a tunnel) to advertise the HTTP A2A interface.
# VOKTER_A2A_TOKEN: optional bearer token. When set, an HTTP caller presenting
#   it is 'trusted' (may use ask/plan/browse). Unauthenticated callers
#   can only use the public 'introduce' handshake.
A2A_URL   = os.getenv("VOKTER_A2A_URL",   "")
A2A_TOKEN = os.getenv("VOKTER_A2A_TOKEN", "")

# Admin token — gates the HUMAN's admin API (everything under /api/ except the
# public agent card). A SEPARATE trust domain from VOKTER_A2A_TOKEN, which only
# elevates a peer *agent* over /a2a; a peer must never hold the admin token.
# Opt-in: when empty the admin API is unprotected (safe only because the app is
# loopback-bound by default and the browser UI is loopback-only). Set this
# before exposing Vokter to any network.
ADMIN_TOKEN = os.getenv("VOKTER_ADMIN_TOKEN", "")

# Human-session token — a THIRD, separate trust domain (P2: "this request is the
# local human session and may receive personal memory"). Minted per-launch by the
# Electron shell and injected here on every backend spawn (survives a Start-fresh
# respawn byte-identical); the backend only COMPARES it, never generates it. Only
# the local UI presents it, as the X-Vokter-Human-Session header on /api/ask.
# Internal callers (A2A/Nostr dispatch, MCP) do NOT hold it → they never receive
# memory, deny-by-default. When empty (raw uvicorn/docker dev, no Electron) the gate
# is STRICT: no memory is injected at all — fail-closed over P2, "seguridad sobre
# comodidad". In the packaged product Electron always mints it, so the gate is always
# live. See app/chat.py:is_local_human_session and docs/threat-model-prompt-injection.md §7.
HUMAN_SESSION_TOKEN = os.getenv("VOKTER_HUMAN_SESSION_TOKEN", "")

# Max request body accepted on the public /a2a endpoint (bytes). Bounds memory
# against an oversized-body DoS on the one networked-by-design surface.
try:
    A2A_MAX_BODY = int(os.getenv("VOKTER_A2A_MAX_BODY", "262144"))
except ValueError:
    A2A_MAX_BODY = 262144

sqlite_impl = _plain_sqlite3

_DEFAULT_DB_KEY = "change-me-before-first-run"
if DB_KEY == _DEFAULT_DB_KEY:
    print("WARNING: VOKTER_DB_KEY is still set to the default value. "
          "Change it to a strong, unique passphrase before storing sensitive data.")

if DB_KEY:
    try:
        from sqlcipher3 import dbapi2 as sqlite_impl  # type: ignore[no-redef]
    except ImportError:
        # Principle 4 (real privacy): an encryption key was provided, so
        # silently storing data in plaintext is never acceptable.
        _die(
            "ERROR: Vokter cannot start safely.\n\n"
            "Your data is set to be encrypted, but the encryption component\n"
            "(SQLCipher) could not be loaded on this machine. Starting anyway\n"
            "would store your documents and keys UNPROTECTED on disk, so\n"
            "Vokter refuses to run.\n\n"
            "What you can do: reinstall Vokter. If the problem persists,\n"
            "please report it: https://github.com/vokter-eu/Vokter/issues"
        )
elif _FROZEN:
    # The desktop binary has no legitimate unencrypted mode: the desktop app
    # (orchestrator) always creates a key. Keyless plaintext stays available
    # only as the long-documented dev/Docker opt-in below.
    _die(
        "ERROR: Vokter cannot start safely.\n\n"
        "Vokter was started without an encryption key (VOKTER_DB_KEY), so\n"
        "your documents and keys would be stored UNPROTECTED on disk.\n\n"
        "Start Vokter through the desktop app, which creates a key for you.\n"
        "If you are launching this binary by hand, set VOKTER_DB_KEY."
    )
else:
    print("WARNING: VOKTER_DB_KEY not set — database stored in plaintext. "
          "Set VOKTER_DB_KEY to enable encryption.")

# The "exposed without an admin token" case is now enforced as a hard refusal to
# start (see main.lifespan), not just a warning — fail closed before serving.
