import os
import sqlite3 as _plain_sqlite3

OLLAMA_URL  = os.getenv("VOKTER_OLLAMA_URL",  "http://ollama:11434")
CHAT_MODEL  = os.getenv("VOKTER_CHAT_MODEL",  "llama3.2:3b")
EMBED_MODEL = os.getenv("VOKTER_EMBED_MODEL", "nomic-embed-text")
DB_PATH     = os.getenv("VOKTER_DB",          "/data/vokter.db")
DB_KEY      = os.getenv("VOKTER_DB_KEY",      "")

CHUNK_SIZE    = 900
CHUNK_OVERLAP = 150
TOP_K         = 4
# max messages kept per conversation (= 10 turns)
# WARNING: process-local dict — do NOT run with multiple uvicorn workers
MAX_HISTORY   = 20

EMAIL_IMAP_HOST = os.getenv("VOKTER_EMAIL_IMAP_HOST",  "")
EMAIL_IMAP_PORT = int(os.getenv("VOKTER_EMAIL_IMAP_PORT", "993"))
EMAIL_USER      = os.getenv("VOKTER_EMAIL_USER",        "")
EMAIL_PASSWORD  = os.getenv("VOKTER_EMAIL_PASSWORD",    "")
EMAIL_FOLDER    = os.getenv("VOKTER_EMAIL_FOLDER",      "INBOX")
EMAIL_MAX_SYNC  = int(os.getenv("VOKTER_EMAIL_MAX_SYNC", "200"))

sqlite_impl = _plain_sqlite3

if DB_KEY:
    try:
        from sqlcipher3 import dbapi2 as sqlite_impl  # type: ignore[no-redef]
    except ImportError:
        DB_KEY = ""
        print("WARNING: sqlcipher3 not installed — database is NOT encrypted")
else:
    print("WARNING: VOKTER_DB_KEY not set — database stored in plaintext. "
          "Set it in docker-compose.yml to enable encryption.")
