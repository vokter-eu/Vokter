import os
from config import DB_PATH, DB_KEY, sqlite_impl


def get_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite_impl.connect(DB_PATH)
    if DB_KEY:
        safe_key = DB_KEY.replace("'", "''")
        conn.execute(f"PRAGMA key='{safe_key}'")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS chunks (
               id        INTEGER PRIMARY KEY AUTOINCREMENT,
               doc       TEXT NOT NULL,
               content   TEXT NOT NULL,
               embedding TEXT NOT NULL
           )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS synced_emails (
               message_id TEXT PRIMARY KEY,
               subject    TEXT,
               sender     TEXT,
               date       TEXT,
               synced_at  TEXT DEFAULT CURRENT_TIMESTAMP
           )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS identity_keys (
               key_id TEXT PRIMARY KEY,
               value  BLOB NOT NULL
           )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS session_nonces (
               session_id TEXT PRIMARY KEY,
               nonce      BLOB NOT NULL,
               context    TEXT NOT NULL,
               created_at TEXT NOT NULL
           )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS browse_allowlist (
               pattern    TEXT PRIMARY KEY,
               added_at   TEXT NOT NULL
           )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS cashu_proofs (
               id         TEXT PRIMARY KEY,   -- proof secret
               mint       TEXT NOT NULL,
               amount     INTEGER NOT NULL,
               proof_json TEXT NOT NULL,
               spent      INTEGER NOT NULL DEFAULT 0
           )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS wallet_transactions (
               id        TEXT PRIMARY KEY,
               adapter   TEXT NOT NULL,
               direction TEXT NOT NULL,       -- 'in' | 'out'
               amount    INTEGER NOT NULL,
               unit      TEXT NOT NULL,
               memo      TEXT DEFAULT '',
               output    TEXT DEFAULT '',     -- token / txid / bolt11 for the counterparty
               ts        REAL NOT NULL
           )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS scheduled_tasks (
               id               TEXT PRIMARY KEY,
               name             TEXT NOT NULL,
               goal             TEXT NOT NULL,
               interval_seconds INTEGER NOT NULL,
               next_run         REAL NOT NULL,
               last_run         REAL,
               enabled          INTEGER NOT NULL DEFAULT 1,
               created_at       REAL NOT NULL
           )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS task_runs (
               id          TEXT PRIMARY KEY,
               task_id     TEXT NOT NULL REFERENCES scheduled_tasks(id) ON DELETE CASCADE,
               started_at  REAL NOT NULL,
               finished_at REAL,
               status      TEXT NOT NULL DEFAULT 'running',   -- running | done | error
               output      TEXT NOT NULL DEFAULT ''
           )"""
    )
    return conn
