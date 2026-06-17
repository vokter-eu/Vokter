import os
from config import DB_PATH, DB_KEY, sqlite_impl


def get_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite_impl.connect(DB_PATH)
    if DB_KEY:
        conn.execute(f"PRAGMA key='{DB_KEY}'")
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
    return conn
