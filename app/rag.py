from contextlib import closing

import numpy as np

from config import TOP_K
from db import get_db
from embedding import unpack_embedding
from engine import get_engine

_SCAN_LIMIT = 10_000  # safety cap — prevents OOM on very large corpora


async def embed(text: str) -> list[float]:
    # Thin wrapper kept for its callers (retrieve, ingestion). The actual
    # engine call lives behind the adapter — Vokter no longer talks to any
    # specific engine here.
    return await get_engine().embed(text)


async def retrieve(question: str, top_k: int | None = None) -> list[tuple]:
    """Top-k document chunks by cosine similarity to `question`.

    A3: embeddings are packed float32 BLOBs, so the whole corpus loads into one
    N×D numpy matrix and the ranking is a single vectorized dot product + argpartition
    — replacing the old per-row json.loads and pure-Python cosine loop. Returns
    (score, doc, content) sorted by score desc, same contract as before; the caller
    applies the relevance floor."""
    k = top_k if top_k is not None else TOP_K
    with closing(get_db()) as db:
        rows = db.execute(
            "SELECT doc, content, embedding FROM chunks LIMIT ?", (_SCAN_LIMIT,)
        ).fetchall()
    if not rows:
        return []
    q = np.asarray(await embed(question), dtype=np.float32)
    qn = float(np.linalg.norm(q))
    if not qn:
        return []
    dim = q.shape[0]
    # Dimension guard: a row embedded with a different model has a different length —
    # skip it rather than let np.vstack throw and kill the whole retrieval (same spirit
    # as the old cosine() len-mismatch → 0.0). Robust across the JSON→BLOB transition.
    vecs, meta = [], []
    for doc, content, emb in rows:
        v = unpack_embedding(emb)
        if v is not None and v.shape[0] == dim:
            vecs.append(v)
            meta.append((doc, content))
    if not vecs:
        return []
    matrix = np.vstack(vecs)                                  # N×D
    sims = (matrix @ q) / (np.linalg.norm(matrix, axis=1) * qn + 1e-12)
    n = len(sims)
    kk = min(k, n)
    top = np.argpartition(-sims, kk - 1)[:kk] if kk < n else np.arange(n)
    top = top[np.argsort(-sims[top])]                        # order the k winners desc
    return [(float(sims[i]), meta[i][0], meta[i][1]) for i in top]
