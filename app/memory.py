"""Phase 1 — personal memory (explicit, `told`).

Facts the user asks Vokter to remember, stored in the SAME encrypted DB (sqlcipher,
keychain-backed key). Never leaves the device; fully user-visible/editable/deletable.

Phase 1 scope: EXPLICIT saves only ("remember that ...") and the review window.
No chat injection yet (that is 1b), no learning-from-conversation (that is Phase 2),
no similarity retrieval (Phase 1 includes ALL facts, so no embeddings are computed).
"""
import time
from contextlib import closing

from db import get_db

# Explicit, predictable triggers — NOT fuzzy NLP. The user always knows when a
# save happens (Vokter confirms), and can see/delete it in the review window.
_ES_PREFIXES = (
    "recuérdame que ", "recuerdame que ", "recuérdame: ", "recuerdame: ",
    "recuerda que ", "apunta que ", "no olvides que ",
)
_EN_PREFIXES = (
    "remember that ", "remember: ", "note that ", "keep in mind that ",
)
_PREFIXES = _ES_PREFIXES + _EN_PREFIXES


def parse_remember(text: str) -> str | None:
    """If `text` is an explicit 'remember ...' command, return the fact (verbatim,
    original case), else None. Trailing punctuation is trimmed."""
    stripped = text.strip()
    low = stripped.lower()
    for p in _PREFIXES:
        if low.startswith(p):
            fact = stripped[len(p):].strip().rstrip(".!").strip()
            return fact or None
    return None


def trigger_lang(text: str) -> str:
    """'es' or 'en' — so Vokter confirms in the language you asked in."""
    low = text.strip().lower()
    return "es" if any(low.startswith(p) for p in _ES_PREFIXES) else "en"


def add(content: str, source: str = "told") -> dict:
    """Store a fact. Returns the created row. `content` is stored VERBATIM so the
    user sees exactly what was saved."""
    content = content.strip()
    now = time.time()
    with closing(get_db()) as db:
        cur = db.execute(
            "INSERT INTO memory(content, source, created_at, confidence) VALUES(?,?,?,?)",
            (content, source, now, 1.0),
        )
        db.commit()
        new_id = cur.lastrowid
    return {"id": new_id, "content": content, "source": source, "created_at": now}


def list_all() -> list[dict]:
    """Every fact Vokter holds, newest first — the whole of what it knows about you."""
    with closing(get_db()) as db:
        rows = db.execute(
            "SELECT id, content, source, created_at FROM memory ORDER BY created_at DESC, id DESC"
        ).fetchall()
    return [{"id": r[0], "content": r[1], "source": r[2], "created_at": r[3]} for r in rows]


def edit(mem_id: int, content: str) -> bool:
    """Correct a fact. Returns False if the id doesn't exist."""
    content = content.strip()
    with closing(get_db()) as db:
        cur = db.execute("UPDATE memory SET content=? WHERE id=?", (content, mem_id))
        db.commit()
        return cur.rowcount > 0


def delete(mem_id: int) -> bool:
    """Forget one fact. Returns False if the id doesn't exist."""
    with closing(get_db()) as db:
        cur = db.execute("DELETE FROM memory WHERE id=?", (mem_id,))
        db.commit()
        return cur.rowcount > 0


def forget_all() -> int:
    """'Forget everything about me' — a REAL delete, then VACUUM to reclaim and
    overwrite the freed pages (not a hidden flag). Returns how many were removed."""
    with closing(get_db()) as db:
        n = db.execute("SELECT COUNT(*) FROM memory").fetchone()[0]
        db.execute("DELETE FROM memory")
        db.commit()
        db.execute("VACUUM")   # scrub freed pages so erased facts don't linger on disk
    return n
