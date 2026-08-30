from contextlib import closing

import numpy as np

from config import RRF_K, RRF_W_KW, RRF_W_VEC, TOP_K, VECTOR_TOP_N
from db import get_db
from embedding import unpack_embedding
from engine import get_engine
from fts import to_match_query

_SCAN_LIMIT = 10_000  # safety cap — prevents OOM on very large corpora


async def embed(text: str) -> list[float]:
    # Thin wrapper kept for its callers (retrieve, ingestion). The actual
    # engine call lives behind the adapter — Vokter no longer talks to any
    # specific engine here.
    return await get_engine().embed(text)


def rrf(vec_ranked: list[int], kw_ranked: list[int]) -> dict[int, float]:
    """Reciprocal Rank Fusion: each arm contributes w / (RRF_K + rank) for a candidate,
    summed across arms. Rank-based, so it fuses a cosine score and an FTS relevance score
    without needing them on the same scale."""
    scores: dict[int, float] = {}
    for rank, cid in enumerate(vec_ranked):
        scores[cid] = scores.get(cid, 0.0) + RRF_W_VEC / (RRF_K + rank + 1)
    for rank, cid in enumerate(kw_ranked):
        scores[cid] = scores.get(cid, 0.0) + RRF_W_KW / (RRF_K + rank + 1)
    return scores


async def retrieve(question: str, top_k: int | None = None,
                   min_score: float | None = None) -> list[tuple]:
    """Hybrid top-k document retrieval (Direction A / A2).

    Two candidate lists per query:
      * vector arm — cosine similarity over the packed-float32 BLOB matrix (A3), top-N;
      * keyword arm — FTS5 MATCH on a SANITISED query (fts.to_match_query, never raw), top-N.
    Fused with Reciprocal Rank Fusion. Relevance gate preserved: a candidate reaches the
    final top-k only if it clears the semantic floor (`min_score`) OR is a genuine keyword
    hit — so a greeting/off-topic message (no strong vector match, no keyword match) returns
    [] and nothing is injected. `min_score=None` disables the semantic floor (planner path).

    Returns (score, doc, content) in fused order; `score` is the cosine similarity
    (0.0 for a keyword-only hit) for display/threshold, same tuple shape as before."""
    k = top_k if top_k is not None else TOP_K
    floor = min_score
    with closing(get_db()) as db:
        rows = db.execute(
            "SELECT id, doc, content, embedding FROM chunks LIMIT ?", (_SCAN_LIMIT,)
        ).fetchall()
        if not rows:
            return []
        meta = {cid: (doc, content) for cid, doc, content, _ in rows}

        # --- vector arm ---
        vec_ranked: list[int] = []
        sim_by_id: dict[int, float] = {}
        q = np.asarray(await embed(question), dtype=np.float32)
        qn = float(np.linalg.norm(q))
        if qn:
            dim = q.shape[0]
            ids, vecs = [], []
            for cid, _doc, _content, emb in rows:
                v = unpack_embedding(emb)
                if v is not None and v.shape[0] == dim:   # dim guard (mixed models / corrupt)
                    ids.append(cid)
                    vecs.append(v)
            if vecs:
                matrix = np.vstack(vecs)
                sims = (matrix @ q) / (np.linalg.norm(matrix, axis=1) * qn + 1e-12)
                order = np.argsort(-sims)[:VECTOR_TOP_N]
                vec_ranked = [ids[i] for i in order]
                sim_by_id = {ids[i]: float(sims[i]) for i in order}

        # --- keyword arm (FTS5, sanitised) ---
        kw_ranked: list[int] = []
        match = to_match_query(question)
        if match:
            try:
                kw_rows = db.execute(
                    "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ? "
                    "ORDER BY rank LIMIT ?", (match, VECTOR_TOP_N)
                ).fetchall()
                kw_ranked = [r[0] for r in kw_rows]
            except Exception:
                kw_ranked = []   # a malformed match must never break retrieval

    scores = rrf(vec_ranked, kw_ranked)
    if not scores:
        return []
    kw_set = set(kw_ranked)
    # Relevance gate: keep a candidate only if it clears the semantic floor OR is a genuine
    # keyword hit. floor=None → semantic gate off (keep all vector candidates + keyword hits).
    eligible = [
        cid for cid in scores
        if (cid in kw_set) or (floor is None) or (sim_by_id.get(cid, 0.0) >= floor)
    ]
    if not eligible:
        return []
    eligible.sort(key=lambda cid: scores[cid], reverse=True)
    return [(sim_by_id.get(cid, 0.0), meta[cid][0], meta[cid][1]) for cid in eligible[:k]]
