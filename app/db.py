import os
from config import DB_PATH, DB_KEY, sqlite_impl


def get_db():
    if d := os.path.dirname(DB_PATH):
        os.makedirs(d, exist_ok=True)
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
    conn.execute(
        """CREATE TABLE IF NOT EXISTS agent_config (
               key   TEXT PRIMARY KEY,
               value TEXT NOT NULL
           )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS conversations (
               seq         INTEGER PRIMARY KEY AUTOINCREMENT,
               conv_id     TEXT NOT NULL,
               role        TEXT NOT NULL,
               content     TEXT NOT NULL,
               ts          REAL NOT NULL,
               human_owned INTEGER NOT NULL DEFAULT 0  -- C2a: 1 = the local human's thread
           )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS conversations_conv_id_idx ON conversations(conv_id)"
    )
    # Migrate conversations tables created before human_owned existed (C2a bit-guard).
    # Runs ONCE, only when the column is missing. Backfill B: existing rows predate persisted
    # peer threads in practice (peer continuity lives in the process-local _conversations map,
    # lost on restart), so treat all prior rows as the human's — otherwise old human chats
    # would stay human_owned=0 and remain readable by a non-human caller that knew the id.
    # The UPDATE is DML, so it needs an explicit commit (unlike the autocommitting ALTER).
    cols = [r[1] for r in conn.execute("PRAGMA table_info(conversations)").fetchall()]
    if "human_owned" not in cols:
        conn.execute("ALTER TABLE conversations ADD COLUMN human_owned INTEGER NOT NULL DEFAULT 0")
        conn.execute("UPDATE conversations SET human_owned=1")
        conn.commit()
    conn.execute(
        """CREATE TABLE IF NOT EXISTS known_agents (
               id           TEXT PRIMARY KEY,   -- nostr hex pubkey, or a2a endpoint url
               npub         TEXT,               -- bech32, for display (nostr peers)
               name         TEXT,               -- from their agent card, if known
               transport    TEXT NOT NULL,      -- 'nostr' | 'a2a-http'
               direction    TEXT NOT NULL,      -- 'inbound' | 'outbound' | 'both'
               card_json    TEXT,               -- their A2A card, if fetched
               trust        TEXT NOT NULL DEFAULT 'neutral',  -- blocked | neutral | trusted
               interactions INTEGER NOT NULL DEFAULT 0,
               first_seen   REAL NOT NULL,
               last_seen    REAL NOT NULL
           )"""
    )
    # Migrate known_agents tables created before the trust column existed.
    cols = [r[1] for r in conn.execute("PRAGMA table_info(known_agents)").fetchall()]
    if "trust" not in cols:
        conn.execute(
            "ALTER TABLE known_agents ADD COLUMN trust TEXT NOT NULL DEFAULT 'neutral'"
        )
    # What the human is willing to sell, and the bounds Vokter negotiates within.
    # 'floor' is the secret reserve price — it is never serialised into a message.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS negotiation_listings (
               item       TEXT PRIMARY KEY,
               opening    INTEGER NOT NULL,   -- first asking price (sats)
               floor      INTEGER NOT NULL,   -- reserve; never sell below, never disclosed
               max_rounds INTEGER NOT NULL DEFAULT 4,
               unit       TEXT NOT NULL DEFAULT 'sat'
           )"""
    )
    # Phase 1: personal memory — facts the user explicitly asks Vokter to remember
    # ("remember that ..."). Lives in THIS encrypted DB (same keychain-backed key),
    # never leaves the device, fully user-visible/editable/deletable (see
    # memory_routes + the "What Vokter knows about you" view). `embedding` is
    # reserved for a later phase's top-k retrieval; Phase 1 includes all facts, so
    # it stays NULL for now.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS memory (
               id         INTEGER PRIMARY KEY AUTOINCREMENT,
               content    TEXT NOT NULL,                -- the fact, in the user's words
               source     TEXT NOT NULL DEFAULT 'told',  -- told | learned (Phase 2)
               created_at REAL NOT NULL,
               embedding  TEXT,                          -- reserved (Phase 3 retrieval)
               confidence REAL NOT NULL DEFAULT 1.0
           )"""
    )
    return conn
