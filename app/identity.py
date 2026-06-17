"""
Layers 1 and 2 of Vokter's three-layer identity model.

Layer 1 — Master key: 32 random bytes, generated once, stored in SQLite
(encrypted at rest when VOKTER_DB_KEY is set), never exported or sent anywhere.

Layer 2 — Session keys: HMAC-SHA256(master_key, nonce || context) derived
per external interaction.  Each interaction gets a fresh unlinkable key.
"""
import hmac
import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from db import get_db

_MASTER_KEY_ROW = "master_key_v1"


def _load_or_create_master_key() -> bytes:
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT value FROM identity_keys WHERE key_id = ?",
            (_MASTER_KEY_ROW,),
        ).fetchone()
        if row is not None:
            return bytes(row[0])

        key = secrets.token_bytes(32)
        conn.execute(
            "INSERT INTO identity_keys (key_id, value) VALUES (?, ?)",
            (_MASTER_KEY_ROW, key),
        )
        conn.commit()
        print("identity: master key generated and stored.")
        return key
    finally:
        conn.close()


# Loaded once at import — lives in process memory only.
_master_key: bytes = _load_or_create_master_key()


def new_session_key(context: str = "") -> tuple[str, bytes]:
    """
    Derive a fresh, unlinkable ephemeral key for one external interaction.

    Returns (session_id, key_bytes).  session_id is persisted locally so the
    key can be reproduced with get_session_key() if needed.
    """
    session_id = str(uuid.uuid4())
    nonce = secrets.token_bytes(16)
    key = _derive(nonce, context)

    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(hours=24)).isoformat()

    conn = get_db()
    try:
        conn.execute("DELETE FROM session_nonces WHERE created_at < ?", (cutoff,))
        conn.execute(
            """INSERT INTO session_nonces (session_id, nonce, context, created_at)
               VALUES (?, ?, ?, ?)""",
            (session_id, nonce, context, now.isoformat()),
        )
        conn.commit()
    finally:
        conn.close()

    return session_id, key


def get_session_key(session_id: str) -> bytes:
    """Reproduce a session key from its stored nonce."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT nonce, context FROM session_nonces WHERE session_id = ?",
            (session_id,),
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        raise KeyError(f"Unknown session_id: {session_id}")
    return _derive(bytes(row[0]), row[1])


def _derive(nonce: bytes, context: str) -> bytes:
    # HMAC-SHA256(master_key, nonce || context) — one-way, unlinkable per nonce
    return hmac.digest(_master_key, nonce + context.encode(), hashlib.sha256)
