import os
import sqlite3 as _plain_sqlite3

VOKTER_VERSION = "0.8.0"  # single source of truth — used by main.py and the agent card

OLLAMA_URL  = os.getenv("VOKTER_OLLAMA_URL",  "http://ollama:11434")
CHAT_MODEL  = os.getenv("VOKTER_CHAT_MODEL",  "llama3.2:3b")
EMBED_MODEL = os.getenv("VOKTER_EMBED_MODEL", "nomic-embed-text")
DB_PATH     = os.getenv("VOKTER_DB",          "/data/vokter.db")
DATA_DIR    = os.path.dirname(DB_PATH) or "/data"
DB_KEY      = os.getenv("VOKTER_DB_KEY",      "")

CHUNK_SIZE    = 900
CHUNK_OVERLAP = 150
TOP_K         = 4
# max messages kept per conversation (= 10 turns)
# WARNING: process-local dict — do NOT run with multiple uvicorn workers
MAX_HISTORY   = 20

VOICE_MODELS_DIR = os.getenv("VOKTER_VOICE_MODELS_DIR", "/data/models")
WHISPER_MODEL    = os.getenv("VOKTER_WHISPER_MODEL",    "base")
WHISPER_DEVICE   = os.getenv("VOKTER_WHISPER_DEVICE",   "cpu")
PIPER_VOICE      = os.getenv("VOKTER_PIPER_VOICE",      "en_US-lessac-medium")

EMAIL_IMAP_HOST = os.getenv("VOKTER_EMAIL_IMAP_HOST",  "")
EMAIL_IMAP_PORT = int(os.getenv("VOKTER_EMAIL_IMAP_PORT", "993"))
EMAIL_USER      = os.getenv("VOKTER_EMAIL_USER",        "")
EMAIL_PASSWORD  = os.getenv("VOKTER_EMAIL_PASSWORD",    "")
EMAIL_FOLDER    = os.getenv("VOKTER_EMAIL_FOLDER",      "INBOX")
EMAIL_MAX_SYNC  = int(os.getenv("VOKTER_EMAIL_MAX_SYNC", "200"))

# Wallet (Phase 3)
# VOKTER_WALLET_ADAPTER: cashu | lightning |
#   eurc | eure | eurcv | evm |               (EVM chains)
#   eurc-solana | eure-solana | sol | solana | (Solana)
#   monero | bitcoin
WALLET_ADAPTER      = os.getenv("VOKTER_WALLET_ADAPTER",    "cashu")
CASHU_MINT_URL      = os.getenv("VOKTER_CASHU_MINT_URL",    "")
WALLET_SPEND_LIMIT  = int(os.getenv("VOKTER_WALLET_SPEND_LIMIT", "0"))  # per 24h in adapter unit; 0 = no limit

# A2A (Agent2Agent) HTTP transport — Phase 6 interoperability.
# VOKTER_A2A_URL: the *publicly reachable* JSON-RPC endpoint for this Vokter.
#   Leave empty (default) when Vokter is only reachable locally / via Nostr —
#   the agent card then advertises Nostr, not an unreachable localhost URL.
#   Set it (e.g. via a tunnel) to advertise the HTTP A2A interface.
# VOKTER_A2A_TOKEN: optional bearer token. When set, an HTTP caller presenting
#   it is 'trusted' (may use ask/wallet/plan/browse). Unauthenticated callers
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
        DB_KEY = ""
        print("WARNING: sqlcipher3 not installed — database is NOT encrypted")
else:
    print("WARNING: VOKTER_DB_KEY not set — database stored in plaintext. "
          "Set it in docker-compose.yml to enable encryption.")

# The "exposed without an admin token" case is now enforced as a hard refusal to
# start (see main.lifespan), not just a warning — fail closed before serving.
