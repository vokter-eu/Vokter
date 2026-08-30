import os
from config import DB_PATH, DB_KEY, sqlite_impl


def get_db():
    if d := os.path.dirname(DB_PATH):
        os.makedirs(d, exist_ok=True)
    conn = sqlite_impl.connect(DB_PATH)
    if DB_KEY:
        # H1: SQLCipher secure-memory — lock/zero the key + decrypted pages in RAM. MUST be set
        # BEFORE PRAGMA key. (Plain sqlite3 in keyless dev ignores unknown PRAGMAs.)
        conn.execute("PRAGMA cipher_memory_security = ON")
        safe_key = DB_KEY.replace("'", "''")
        conn.execute(f"PRAGMA key='{safe_key}'")
    # H1: never spill sorts / index builds / FTS5 work to plaintext temp files on disk.
    conn.execute("PRAGMA temp_store = MEMORY")
    # H3: scrub freed pages so 'forget' (delete) actually erases, not just unlinks.
    conn.execute("PRAGMA secure_delete = ON")
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
    # memory_routes + the "What Vokter knows about you" view).
    #   embedding: Direction A top-k retrieval — a packed float32 BLOB, NULL until the
    #     background pass embeds the fact (keyword-retrievable via FTS meanwhile).
    #   core: 1 = an identity fact (name/allergies/family) always in the prompt; 0 = a
    #     preference retrieved only when the message is relevant (see memory.relevant_block).
    conn.execute(
        """CREATE TABLE IF NOT EXISTS memory (
               id         INTEGER PRIMARY KEY AUTOINCREMENT,
               content    TEXT NOT NULL,                -- the fact, in the user's words
               source     TEXT NOT NULL DEFAULT 'told',  -- told | learned (Phase 2)
               created_at REAL NOT NULL,
               embedding  BLOB,                          -- packed float32; NULL until embedded
               core       INTEGER NOT NULL DEFAULT 0,    -- 1 = identity fact, always injected
               confidence REAL NOT NULL DEFAULT 1.0
           )"""
    )
    # Migrate memory tables created before Direction A (embedding was TEXT, no core).
    # The embedding column already exists (reserved, all-NULL) so it needs no change —
    # SQLite stores a BLOB regardless of the column's TEXT affinity. Only `core` is new.
    mcols = [r[1] for r in conn.execute("PRAGMA table_info(memory)").fetchall()]
    if "core" not in mcols:
        conn.execute("ALTER TABLE memory ADD COLUMN core INTEGER NOT NULL DEFAULT 0")

    # A key/value marker table so the one-time heavy migrations (migrations.run_once)
    # know what has already run and never repeat it.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS meta (
               key   TEXT PRIMARY KEY,
               value TEXT NOT NULL
           )"""
    )

    # FTS5 external-content mirrors of chunks + memory — the keyword half of hybrid
    # retrieval (Direction A / A2). `content=` points each mirror at its base table so
    # the text isn't duplicated; triggers keep them in lock-step. Existing rows are
    # populated once by migrations.run_once ('rebuild') — triggers only fire on new writes.
    conn.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5("
        "content, doc UNINDEXED, content='chunks', content_rowid='id')"
    )
    conn.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5("
        "content, content='memory', content_rowid='id')"
    )
    # Sync triggers. For an external-content FTS5 table a DELETE (and the delete half of
    # an UPDATE) MUST use the special 'delete' command with the OLD text — a plain DELETE
    # from the FTS table silently corrupts the index. INSERT is a plain insert.
    for base, fts in (("chunks", "chunks_fts"), ("memory", "memory_fts")):
        conn.execute(
            f"CREATE TRIGGER IF NOT EXISTS {base}_ai AFTER INSERT ON {base} BEGIN"
            f"  INSERT INTO {fts}(rowid, content) VALUES (new.id, new.content);"
            f" END"
        )
        conn.execute(
            f"CREATE TRIGGER IF NOT EXISTS {base}_ad AFTER DELETE ON {base} BEGIN"
            f"  INSERT INTO {fts}({fts}, rowid, content) VALUES ('delete', old.id, old.content);"
            f" END"
        )
        conn.execute(
            f"CREATE TRIGGER IF NOT EXISTS {base}_au AFTER UPDATE ON {base} BEGIN"
            f"  INSERT INTO {fts}({fts}, rowid, content) VALUES ('delete', old.id, old.content);"
            f"  INSERT INTO {fts}(rowid, content) VALUES (new.id, new.content);"
            f" END"
        )
    return conn
