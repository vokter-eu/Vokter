"""Migration safety test — the in-place, one-way rewrite of the ENCRYPTED store.

migrations.run_once() (JSON→BLOB repack + FTS rebuild) and migrations.reembed_stale()
(dim-mismatch re-embed) rewrite the personal store on disk with no down-migration. A
half-done pass on a real user's DB has no rollback, so the guarantees we actually rely on
must be tested:

  1. run_once() migrates legacy JSON embeddings → packed float32 BLOB, value-preserving.
  2. It is IDEMPOTENT (meta-fenced): a second run rewrites nothing, bytes unchanged.
  3. FTS keyword retrieval works over existing rows after migration (and 'rebuild' repopulates).
  4. reembed_stale() re-embeds ONLY dim-mismatched rows; correct rows are untouched; idempotent.
  5. DEGRADES CLEANLY when interrupted mid-way: an un-re-embedded row is left intact and stays
     KEYWORD-retrievable via FTS, and the vector arm's dim-guard skips its stale vector — no
     corruption, no lockout.

Runs fully offline against a KEYED (SQLCipher) fixture DB — Ollama is stubbed. Run:
  desktop/runtime/venv/bin/python tests/migration_test.py
"""
import asyncio
import json
import os
import sys
import tempfile

# --- keyed fixture: set the env BEFORE importing config (it reads DB_PATH/DB_KEY at import) ---
_TMP = tempfile.mkdtemp(prefix="vokter-migtest-")
os.environ["VOKTER_DB"] = os.path.join(_TMP, "vokter.db")
os.environ["VOKTER_DB_KEY"] = "migration-test-key-" + "a" * 32  # non-empty → SQLCipher
os.environ.pop("VOKTER_OLLAMA_URL", None)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from contextlib import closing  # noqa: E402

import config  # noqa: E402
import db  # noqa: E402
import embedding  # noqa: E402
import migrations  # noqa: E402
import memory  # noqa: E402
import rag  # noqa: E402


def _fail(msg):
    print("FAIL:", msg)
    sys.exit(1)


def _match_ids(db_, fts_table, term):
    """rowids in an external-content FTS5 mirror matching `term` (rowid == source row id).
    Quote the term as an FTS5 phrase so punctuation (e.g. the '-' in INV-4471, which is
    otherwise the NOT operator) is treated literally."""
    phrase = '"' + term.replace('"', '') + '"'
    return {r[0] for r in db_.execute(
        f"SELECT rowid FROM {fts_table} WHERE {fts_table} MATCH ?", (phrase,)).fetchall()}


# ============================================================================
# 0. the fixture is genuinely ENCRYPTED (proves the keyed path, not plaintext dev)
# ============================================================================
with closing(db.get_db()) as d:
    d.commit()
with open(os.environ["VOKTER_DB"], "rb") as fh:
    header = fh.read(16)
if header.startswith(b"SQLite format 3"):
    _fail("fixture DB is PLAINTEXT — SQLCipher not active; test would be meaningless")
if config.sqlite_impl.__name__ not in ("sqlcipher3.dbapi2", "sqlcipher3"):
    _fail(f"sqlite_impl is {config.sqlite_impl.__name__}, expected sqlcipher3")
print("0. keyed fixture OK — DB on disk is encrypted (header not 'SQLite format 3')")

# ============================================================================
# 1. run_once(): legacy JSON embeddings → packed BLOB, value-preserving
# ============================================================================
VEC_A = [0.10, 0.20, 0.30, 0.40]
VEC_B = [0.90, 0.80, 0.70, 0.60]
with closing(db.get_db()) as d:
    # legacy rows: embedding stored as JSON *text* (the pre-A3 on-disk form)
    d.execute("INSERT INTO chunks(doc, content, embedding) VALUES(?,?,?)",
              ("lease.pdf", "the lease ends in August 2026", json.dumps(VEC_A)))
    d.execute("INSERT INTO chunks(doc, content, embedding) VALUES(?,?,?)",
              ("invoice.txt", "invoice INV-4471 is overdue", json.dumps(VEC_B)))
    d.commit()

migrations.run_once()

with closing(db.get_db()) as d:
    rows = d.execute("SELECT content, embedding FROM chunks ORDER BY id").fetchall()
for content, emb in rows:
    if not isinstance(emb, (bytes, bytearray, memoryview)):
        _fail(f"chunk still not a BLOB after run_once: {content!r} -> {type(emb)}")
got_a = embedding.unpack_embedding(rows[0][1]).tolist()
if [round(x, 4) for x in got_a] != VEC_A:
    _fail(f"JSON→BLOB changed the vector: {got_a} != {VEC_A}")
with closing(db.get_db()) as d:
    if migrations._meta_get(d, "chunks_blob_v1") != "1" or migrations._meta_get(d, "fts_rebuild_v1") != "1":
        _fail("meta markers not set after run_once")
print("1. run_once JSON→BLOB OK — 2 rows repacked, vector value preserved, markers set")

# ============================================================================
# 2. IDEMPOTENT: second run_once rewrites nothing (bytes byte-identical)
# ============================================================================
with closing(db.get_db()) as d:
    before = [bytes(r[0]) for r in d.execute("SELECT embedding FROM chunks ORDER BY id")]
    n_again = migrations._repack_chunks(d)   # direct call: how many WOULD it rewrite now?
migrations.run_once()
with closing(db.get_db()) as d:
    after = [bytes(r[0]) for r in d.execute("SELECT embedding FROM chunks ORDER BY id")]
if n_again != 0:
    _fail(f"_repack_chunks rewrote {n_again} already-packed rows (not idempotent)")
if before != after:
    _fail("second run_once changed the stored BLOBs (not idempotent)")
print("2. idempotent OK — repack is a no-op on a migrated DB, BLOBs unchanged")

# ============================================================================
# 3. FTS keyword retrieval over existing rows (+ explicit 'rebuild' repopulates)
# ============================================================================
with closing(db.get_db()) as d:
    ids = {r[0]: r[1] for r in d.execute("SELECT id, content FROM chunks")}
    lease_id = next(i for i, c in ids.items() if "lease" in c)
    inv_id = next(i for i, c in ids.items() if "INV-4471" in c)
    if lease_id not in _match_ids(d, "chunks_fts", "lease"):
        _fail("post-migration FTS MATCH 'lease' did not find the lease chunk")
    if inv_id not in _match_ids(d, "chunks_fts", "INV-4471"):
        _fail("post-migration FTS MATCH 'INV-4471' did not find the invoice chunk")
    # exercise the exact 'rebuild' command run_once uses: wipe the mirror, rebuild, re-check
    d.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('delete-all')")
    if _match_ids(d, "chunks_fts", "lease"):
        _fail("delete-all did not clear the FTS mirror (test precondition)")
    d.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")
    d.commit()
    if lease_id not in _match_ids(d, "chunks_fts", "lease"):
        _fail("'rebuild' did not repopulate the FTS mirror over existing rows")
print("3. FTS OK — keyword retrieval works post-migration; 'rebuild' repopulates existing rows")

# ============================================================================
# 4. reembed_stale(): re-embed ONLY dim-mismatched rows; correct rows untouched; idempotent
# ============================================================================
LIVE_DIM = 8                      # the stubbed model's dimension
WANT_LEN = LIVE_DIM * 4           # float32 bytes

async def _stub_embed(text):      # deterministic, no network
    return [0.05] * LIVE_DIM
rag.embed = _stub_embed           # reembed_stale does `from rag import embed` at call time

m_null = memory.add("my favourite colour is teal")["id"]        # embedding NULL → stale
m_wrong = memory.add("I support Athletic Club de Bilbao")["id"]  # will set a wrong-dim vector
m_ok_id = memory.add("my name is Bilal")["id"]                   # will set a correct-dim vector
with closing(db.get_db()) as d:
    d.execute("UPDATE memory SET embedding=? WHERE id=?",
              (embedding.pack_embedding([0.1] * 4), m_wrong))     # 4-dim → length 16 ≠ 32
    ok_blob = embedding.pack_embedding([0.2] * LIVE_DIM)          # 8-dim → length 32 (correct)
    d.execute("UPDATE memory SET embedding=? WHERE id=?", (ok_blob, m_ok_id))
    d.commit()

n = asyncio.run(migrations.reembed_stale())
with closing(db.get_db()) as d:
    lens = {r[0]: (len(bytes(r[1])) if r[1] is not None else None)
            for r in d.execute("SELECT id, embedding FROM memory")}
    ok_after = bytes(d.execute("SELECT embedding FROM memory WHERE id=?", (m_ok_id,)).fetchone()[0])
if lens[m_null] != WANT_LEN or lens[m_wrong] != WANT_LEN:
    _fail(f"stale rows not re-embedded to live dim: null={lens[m_null]} wrong={lens[m_wrong]}")
if ok_after != ok_blob:
    _fail("a correct-dim row was needlessly rewritten (should be skipped)")
if asyncio.run(migrations.reembed_stale()) != 0:
    _fail("reembed_stale is not idempotent — second pass re-embedded rows")
print(f"4. reembed_stale OK — {n} stale row(s) fixed to dim={LIVE_DIM}, correct row untouched, idempotent")

# ============================================================================
# 5. CLEAN DEGRADE when interrupted mid-way (the no-rollback risk)
# ============================================================================
with closing(db.get_db()) as d:                 # isolate: exactly two fresh stale rows
    d.execute("DELETE FROM memory")
    d.execute("DELETE FROM chunks")
    d.execute("INSERT INTO memory_fts(memory_fts) VALUES('rebuild')")
    d.commit()
first = memory.add("I am allergic to shellfish")["id"]
jordi = memory.add("my brother Jordi lives in Girona")["id"]

_calls = {"n": 0}
async def _flaky_embed(text):     # probe ok, first row ok, then the engine "drops"
    _calls["n"] += 1
    if _calls["n"] > 2:
        raise RuntimeError("engine dropped mid-pass")
    return [0.05] * LIVE_DIM
rag.embed = _flaky_embed

done = asyncio.run(migrations.reembed_stale())
with closing(db.get_db()) as d:
    emb = {r[0]: r[1] for r in d.execute("SELECT id, embedding FROM memory")}
    still_stale = [i for i, e in emb.items() if e is None or len(bytes(e)) != WANT_LEN]
    jordi_by_keyword = jordi in _match_ids(d, "memory_fts", "Jordi")
if done != 1:
    _fail(f"expected exactly 1 row embedded before the drop, got {done}")
if not still_stale:
    _fail("interruption left no stale row — cannot prove degrade path")
# the un-re-embedded row must survive intact AND stay keyword-retrievable
if not jordi_by_keyword:
    _fail("interrupted row is NOT keyword-retrievable — degrade path broken")
# and the vector arm's dim-guard must skip its stale vector (never a wrong match)
stale_vec = embedding.unpack_embedding(emb[still_stale[0]])
if stale_vec is not None and stale_vec.shape[0] == LIVE_DIM:
    _fail("stale row would slip past the dim-guard (wrong-dim not skipped)")
print("5. clean-degrade OK — interrupted row left intact, still keyword-retrievable, "
      "dim-guard skips its stale vector")

print("\nOK — migration: JSON→BLOB value-preserving + idempotent, FTS rebuild works, "
      "reembed_stale targets only stale rows, and an interrupted pass degrades cleanly "
      "(no corruption, no lockout, keyword retrieval intact).")
