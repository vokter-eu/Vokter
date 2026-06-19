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
    # Generate a candidate key unconditionally so both processes in a startup
    # race produce a valid key; INSERT OR IGNORE ensures only one wins,
    # and the SELECT that follows always reads back the stored winner.
    candidate = secrets.token_bytes(32)
    conn = get_db()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO identity_keys (key_id, value) VALUES (?, ?)",
            (_MASTER_KEY_ROW, candidate),
        )
        conn.commit()
        row = conn.execute(
            "SELECT value FROM identity_keys WHERE key_id = ?",
            (_MASTER_KEY_ROW,),
        ).fetchone()
        return bytes(row[0])
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


def get_nostr_privkey() -> bytes:
    """Derive a deterministic secp256k1 private key for Vokter's Nostr identity.

    Derived from the master key via HMAC-SHA256 with a fixed domain label.
    Returns the same 32 bytes every time (Layer 3 identity — stable Nostr npub),
    unlinkable to the ephemeral session keys used for other external interactions.
    """
    return hmac.digest(_master_key, b"vokter:nostr:identity:v1", hashlib.sha256)


def get_nostr_npub() -> str:
    """Return Vokter's stable Nostr public identity as a bech32 npub.

    This is the agent's public name on the network (Layer 3 identity). It is
    always derivable from the master key, whether or not the Nostr listener is
    running. nostr_sdk is imported lazily so this module stays importable (and
    fast to import) when the npub is never requested.
    """
    from nostr_sdk import Keys, SecretKey

    keys = Keys(secret_key=SecretKey.from_bytes(get_nostr_privkey()))
    return keys.public_key().to_bech32()


def _derive(nonce: bytes, context: str) -> bytes:
    # HMAC-SHA256(master_key, nonce || context) — one-way, unlinkable per nonce
    return hmac.digest(_master_key, nonce + context.encode(), hashlib.sha256)
