import json
import math
from contextlib import closing

from config import TOP_K
from db import get_db
from engine import get_engine

_SCAN_LIMIT = 10_000  # safety cap — prevents OOM on very large corpora


async def embed(text: str) -> list[float]:
    # Thin wrapper kept for its callers (retrieve, ingestion). The actual
    # engine call lives behind the adapter — Vokter no longer talks to any
    # specific engine here.
    return await get_engine().embed(text)


def cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        return 0.0  # dimension mismatch — embedding model changed between ingest and query
    dot = sum(x * y for x, y in zip(a, b))
    na  = math.sqrt(sum(x * x for x in a))
    nb  = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


async def retrieve(question: str, top_k: int | None = None) -> list[tuple]:
    k = top_k if top_k is not None else TOP_K
    with closing(get_db()) as db:
        rows = db.execute(
            "SELECT doc, content, embedding FROM chunks LIMIT ?", (_SCAN_LIMIT,)
        ).fetchall()
    if not rows:
        return []
    q_vec = await embed(question)
    return sorted(
        ((cosine(q_vec, json.loads(emb)), doc, content) for doc, content, emb in rows),
        key=lambda t: t[0],
        reverse=True,
    )[:k]
