"""Direction A — one-time schema migration, idempotent and guarded by meta markers.

Split of responsibility:
  * db.get_db()  — the CHEAP, per-connection, idempotent DDL: base tables, the new
    memory.core column (ALTER-guarded), the FTS5 external-content mirrors and their
    sync triggers (CREATE ... IF NOT EXISTS). Safe to run on every connection.
  * run_once()   — the HEAVY, once-per-database work that must NOT sit in get_db():
    repack chunks.embedding JSON text → packed float32 BLOB, and POPULATE the FTS
    mirrors over already-existing rows ('rebuild'). Each step is fenced behind a
    row in `meta` so it never runs twice.

Encrypted-store safety (the migration rewrites the personal store on disk):
  * All writes go through the same keyed connection get_db() opens — the repacked
    BLOBs land encrypted, exactly like the JSON did.
  * temp_store=MEMORY (set in get_db) keeps the FTS index build off any plaintext
    temp file; secure_delete=ON scrubs the freed JSON pages as they're overwritten.
  * JSON→BLOB is one-way. There is no down-migration by design; the reversibility
    guarantee is a file backup taken before shipping (old code's json.loads() would
    throw on a BLOB). See ~/vokter-backups/.

The embedding work that needs Ollama is NOT here as blocking boot steps:
  * embed_pending() (memory.py) — embed facts that have no vector yet, after each add/edit.
  * reembed_stale()  (below)    — re-embed chunks + facts whose vector dimension doesn't
    match the live embed model (e.g. nomic-768 → bge-m3-1024). Both run in the background.
"""
import logging
from contextlib import closing

from db import get_db
from embedding import pack_embedding, unpack_embedding

log = logging.getLogger("vokter")


def _meta_get(db, key: str) -> str | None:
    row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


def _meta_set(db, key: str, value: str = "1") -> None:
    db.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?,?)", (key, value))


def _repack_chunks(db) -> int:
    """chunks.embedding: legacy JSON text → packed float32 BLOB, in place.

    Reads every row, and rewrites only the ones still stored as text (a BLOB round-
    trips through unpack→pack unchanged, but we skip it to avoid needless page churn).
    Returns how many rows were rewritten. The column keeps its TEXT declaration — SQLite
    stores a BLOB as a BLOB regardless of column affinity, so no ALTER is needed.
    """
    rewritten = 0
    rows = db.execute("SELECT id, embedding FROM chunks").fetchall()
    for cid, emb in rows:
        if isinstance(emb, (bytes, bytearray, memoryview)):
            continue                              # already packed
        vec = unpack_embedding(emb)
        if vec is None:
            continue                              # unreadable legacy row — leave it, skip
        db.execute("UPDATE chunks SET embedding=? WHERE id=?",
                   (pack_embedding(vec), cid))
        rewritten += 1
    return rewritten


def run_once() -> None:
    """Run the heavy one-time migrations that haven't run yet. Fast and idempotent on
    every subsequent boot (each step is fenced by a meta marker). Synchronous and
    Ollama-free, so it is safe to await before the app starts serving."""
    with closing(get_db()) as db:
        # 1) Repack document embeddings JSON→BLOB (once).
        if not _meta_get(db, "chunks_blob_v1"):
            n = _repack_chunks(db)
            _meta_set(db, "chunks_blob_v1")
            db.commit()
            log.info("[migrate] chunks embeddings repacked JSON→BLOB: %d row(s)", n)

        # 2) Populate the FTS5 mirrors over rows that predate the triggers (once).
        #    Triggers (created in get_db) only fire on FUTURE writes; existing rows are
        #    invisible to MATCH until an explicit 'rebuild'.
        if not _meta_get(db, "fts_rebuild_v1"):
            db.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")
            db.execute("INSERT INTO memory_fts(memory_fts) VALUES('rebuild')")
            _meta_set(db, "fts_rebuild_v1")
            db.commit()
            log.info("[migrate] FTS5 mirrors rebuilt over existing chunks + memory")


async def reembed_stale(batch: int = 64) -> int:
    """Re-embed any chunk or fact whose stored vector doesn't match the CURRENT embed
    model's dimension — NULL, or the wrong byte length (e.g. a 768-dim nomic vector after
    the switch to bge-m3's 1024-dim). Overwrites only the derived `embedding` column;
    content, FTS and the core flag are untouched, so this is regenerable and needs no
    backup. Idempotent and SELF-HEALING: it converges when every row matches the live dim,
    and simply re-runs any row a model change left stale — no marker or cursor to manage.

    Non-blocking background pass (boot + after the embedder is present). Needs Ollama; if it
    can't reach the engine it stops quietly and retries next boot. Until a row is re-embedded
    the retrieval dim-guard skips its stale vector, so retrieval degrades to the keyword/FTS
    arm rather than returning wrong matches. Returns the number of rows re-embedded this pass."""
    from rag import embed   # local import — rag pulls the engine adapter (heavier)
    try:
        dim = len(await embed("dimension probe"))
    except Exception:
        return 0            # engine unavailable → nothing to do now, retry next boot
    if not dim:
        return 0
    want = dim * 4          # float32 bytes for the live model's dimension
    total = 0
    for table in ("memory", "chunks"):
        while True:
            with closing(get_db()) as db:
                rows = db.execute(
                    f"SELECT id, content FROM {table} "
                    f"WHERE embedding IS NULL OR length(embedding) != ? "
                    f"ORDER BY id LIMIT ?", (want, batch),
                ).fetchall()
            if not rows:
                break
            for rid, content in rows:
                try:
                    vec = await embed(content)
                except Exception:
                    return total   # engine dropped mid-pass → stop, resume next boot
                with closing(get_db()) as db:
                    db.execute(f"UPDATE {table} SET embedding=? WHERE id=?",
                               (pack_embedding(vec), rid))
                    db.commit()
                total += 1
    if total:
        log.info("[migrate] re-embedded %d stale row(s) to dim=%d (%s)", total, dim, want)
    return total
