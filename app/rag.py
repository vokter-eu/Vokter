import json
import math
from contextlib import closing

import httpx
from fastapi import HTTPException

from config import OLLAMA_URL, EMBED_MODEL, TOP_K
from db import get_db


async def embed(text: str) -> list[float]:
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(
            f"{OLLAMA_URL}/api/embeddings",
            json={"model": EMBED_MODEL, "prompt": text},
        )
    if r.status_code != 200:
        raise HTTPException(502, f"Ollama (embeddings) returned {r.status_code}. "
                                 f"Did you run 'ollama pull {EMBED_MODEL}'?")
    return r.json()["embedding"]


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na  = math.sqrt(sum(x * x for x in a))
    nb  = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


async def retrieve(question: str) -> list[tuple]:
    with closing(get_db()) as db:
        rows = db.execute("SELECT doc, content, embedding FROM chunks").fetchall()
    if not rows:
        return []
    q_vec = await embed(question)
    return sorted(
        ((cosine(q_vec, json.loads(emb)), doc, content) for doc, content, emb in rows),
        key=lambda t: t[0],
        reverse=True,
    )[:TOP_K]
