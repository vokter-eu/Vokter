"""Direction A — retrieval eval harness (DEV-ONLY, never shipped; tests/ isn't frozen).

Seeds a fixed set of personal facts + documents, runs a query set with known expected
outcomes, and prints BEFORE (dump-all memory + vector-only, pure-Python cosine docs) vs
AFTER (query-aware relevant_block + hybrid numpy/FTS retrieve) for each: which facts and
which document chunks get injected, and the retrieval latency.

Then it reports the three headline numbers Bilal asked for:
  (a) right fact injected            — the on-topic fact is pulled in AFTER
  (b) dump eliminated                — non-core facts injected on an UNRELATED question
                                       (BEFORE dumps them all; AFTER pulls none)
  (c) exact-term recall from FTS     — an invoice id / name found by the keyword arm AFTER,
                                       missed by the vector-only BEFORE
plus the vector-search speed delta (pure-Python cosine → numpy matrix).

Run (needs Ollama on 127.0.0.1:11434 with nomic-embed-text):
  VOKTER_DB=$(mktemp -d)/eval.db VOKTER_OLLAMA_URL=http://127.0.0.1:11434 \
    desktop/runtime/venv/bin/python tests/memory_retrieval_eval.py
"""
import asyncio
import math
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import db                       # noqa: E402
import memory                   # noqa: E402
import rag                      # noqa: E402
from embedding import pack_embedding, unpack_embedding  # noqa: E402

# ── fixtures ─────────────────────────────────────────────────────────────────
FACTS = [
    "The user's name is Bilal",                        # core (name)
    "The user is allergic to shellfish",               # core (allergy)
    "The user's favourite colour is teal",             # non-core EN (preference)
    "The user supports Athletic Club de Bilbao",       # non-core EN (team)
    "The user's brother Jordi lives in Girona",        # core (relationship)
    # Spanish equivalents — Bilal's actual language. Short, implicitly phrased facts are
    # the hard case for the semantic arm; these expose whether recall holds in ES, not
    # just EN (the earlier English-only fixture gave a FALSE green on the team metric).
    "El color favorito del usuario es el naranja",     # non-core ES (preference)
    "El usuario es del Athletic Club de Bilbao",       # non-core ES (team, implicit "es del")
]
DOCS = {
    "invoice.txt": "Invoice number INV-4471 issued on 2026-08-30 to Jordi Puig for "
                   "consulting services rendered in July. Total amount due 1200 EUR.",
    "recipe.txt":  "To make a traditional paella you need bomba rice, saffron, olive "
                   "oil, chicken and seafood, cooked slowly in a wide flat pan.",
    "travel.txt":  "The regional train service between Bilbao and Girona is slow; the "
                   "journey takes around seven hours with a change in Barcelona.",
}
QUERIES = [
    ("what team do I support?",        "EN team fact, NOT colour"),
    ("¿de qué equipo soy?",            "ES team fact, NOT colour"),
    ("what's 2+2?",                    "nothing beyond core (no dump)"),
    ("what's my favourite colour?",    "EN colour fact"),
    ("¿cuál es mi color favorito?",    "ES colour fact"),
    ("tell me about Jordi",            "keyword catches the name (fact + doc)"),
    ("INV-4471",                       "keyword catches the exact invoice id"),
]
FILLER = int(os.getenv("EVAL_FILLER", "1000"))   # synthetic chunks to expose the speed delta


def _facts_in_block(block: str) -> list[str]:
    return [ln[2:] for ln in block.splitlines() if ln.startswith("- ")]


def _noncore(facts: list[str]) -> list[str]:
    return [f for f in facts if not memory._is_core(f)]


async def _pure_python_vector_docs(query: str, k: int, floor: float):
    """BEFORE: the pre-A2/A3 path — vector-only, per-row json/pure-Python cosine, floor gate.
    (Reads the same BLOBs but scores the old way, for a fair quality+speed comparison.)"""
    with db.get_db() as conn:
        rows = conn.execute("SELECT doc, content, embedding FROM chunks").fetchall()
    q = await rag.embed(query)

    def cos(a, b):
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a)); nb = math.sqrt(sum(y * y for y in b))
        return dot / (na * nb) if na and nb else 0.0

    scored = []
    for doc, content, emb in rows:
        v = unpack_embedding(emb)
        if v is not None and v.shape[0] == len(q):
            scored.append((cos(q, v.tolist()), doc, content))
    scored.sort(reverse=True)
    return [(s, d, c) for s, d, c in scored[:k] if s >= floor]


async def seed():
    conn = db.get_db()
    # Fresh temp DB per run; clear anyway for reruns. The AFTER-DELETE triggers keep the
    # FTS mirrors in sync row-by-row, so no separate FTS clear is needed.
    conn.execute("DELETE FROM memory"); conn.execute("DELETE FROM chunks")
    conn.commit(); conn.close()

    for f in FACTS:
        memory.add(f)                       # classifies core, indexes FTS (trigger)
    await memory.embed_pending()            # embed the facts (real vectors)

    for name, text in DOCS.items():
        vec = await rag.embed(text)
        with db.get_db() as conn:
            conn.execute("INSERT INTO chunks(doc, content, embedding) VALUES(?,?,?)",
                         (name, text, pack_embedding(vec)))
            conn.commit()
    # synthetic filler with random unit-ish vectors — bulk for the speed comparison only
    if FILLER:
        rng = np.random.default_rng(0)
        with db.get_db() as conn:
            for i in range(FILLER):
                v = rng.standard_normal(768).astype(np.float32)
                conn.execute("INSERT INTO chunks(doc, content, embedding) VALUES(?,?,?)",
                             (f"filler-{i}.txt", f"unrelated filler chunk {i}", pack_embedding(v)))
            conn.commit()


async def main():
    await seed()
    floor = 0.57
    print(f"\nseeded {len(FACTS)} facts + {len(DOCS)} docs (+{FILLER} filler chunks)\n")
    print("=" * 92)

    before_dump = memory.system_block()
    dumped_noncore = _noncore(_facts_in_block(before_dump))

    right_fact_ok = {}
    fts_recall = {}
    b_times, a_times = [], []

    for q, expect in QUERIES:
        # ---- memory ----
        after_mem = _facts_in_block(await memory.relevant_block(q))
        before_mem = _facts_in_block(before_dump)     # dump-all is query-independent

        # ---- docs (timed) ----
        t0 = time.perf_counter(); before_docs = await _pure_python_vector_docs(q, 4, floor)
        b_times.append(time.perf_counter() - t0)
        t0 = time.perf_counter(); after_docs = await rag.retrieve(q, top_k=4, min_score=floor)
        a_times.append(time.perf_counter() - t0)

        print(f"\nQUERY: {q!r}   (expect: {expect})")
        print(f"  BEFORE facts ({len(before_mem)}): {before_mem}")
        print(f"  AFTER  facts ({len(after_mem)}): {after_mem}")
        print(f"  BEFORE docs : {[d for _, d, _ in before_docs]}")
        print(f"  AFTER  docs : {[d for _, d, _ in after_docs]}")

        # metric bookkeeping (per language — team recall is the case that differs EN vs ES)
        if q == "what team do I support?":
            right_fact_ok["team_EN"] = any("Athletic" in f for f in after_mem) and \
                                       not any("teal" in f or "naranja" in f for f in after_mem)
        if q == "¿de qué equipo soy?":
            right_fact_ok["team_ES"] = any("Athletic" in f for f in after_mem) and \
                                       not any("teal" in f or "naranja" in f for f in after_mem)
        if q == "what's my favourite colour?":
            right_fact_ok["colour_EN"] = any("teal" in f for f in after_mem)
        if q == "¿cuál es mi color favorito?":
            right_fact_ok["colour_ES"] = any("naranja" in f for f in after_mem)
        if q == "INV-4471":
            fts_recall["inv_before"] = any(d == "invoice.txt" for _, d, _ in before_docs)
            fts_recall["inv_after"] = any(d == "invoice.txt" for _, d, _ in after_docs)
        if q == "tell me about Jordi":
            fts_recall["jordi_before"] = any(d == "invoice.txt" for _, d, _ in before_docs)
            fts_recall["jordi_after"] = any(d == "invoice.txt" for _, d, _ in after_docs)

    # unrelated-question dump metric
    unrelated_after = _noncore(_facts_in_block(await memory.relevant_block("what's 2+2?")))

    # Diagnostic: for the team queries, the best non-core cosine vs the floor — shows WHY
    # ES recall differs from EN (short implicit facts land in a narrow band under the floor).
    async def _best_noncore_cos(query: str):
        q = np.asarray(await rag.embed(query), dtype=np.float32); qn = np.linalg.norm(q)
        with db.get_db() as conn:
            rows = conn.execute("SELECT content, embedding FROM memory WHERE core=0").fetchall()
        out = []
        for content, emb in rows:
            v = unpack_embedding(emb)
            if v is not None:
                out.append((float(v @ q / (np.linalg.norm(v) * qn)), content))
        return sorted(out, reverse=True)

    print("\n" + "=" * 92)
    print("HEADLINE NUMBERS")
    print(f"  (a) right fact injected      : "
          f"team_EN={right_fact_ok.get('team_EN')}  team_ES={right_fact_ok.get('team_ES')}  "
          f"colour_EN={right_fact_ok.get('colour_EN')}  colour_ES={right_fact_ok.get('colour_ES')}")
    for q in ("what team do I support?", "¿de qué equipo soy?"):
        top = await _best_noncore_cos(q)
        team = next((s for s, c in top if "Athletic" in c), None)
        print(f"      · {q!r}: team-fact cosine={team:.3f} vs floor {floor} "
              f"→ {'HIT' if team and team >= floor else 'MISS'}   (top non-core: "
              f"{top[0][1][:32]!r}={top[0][0]:.3f})")
    print(f"  (b) dump eliminated          : non-core facts on an UNRELATED question — "
          f"BEFORE={len(dumped_noncore)}  AFTER={len(unrelated_after)}")
    print(f"      (BEFORE dumped: {dumped_noncore})")
    print(f"  (c) exact-term FTS recall    : 'tell me about Jordi' finds invoice.txt — "
          f"BEFORE(vector-only)={fts_recall.get('jordi_before')}  "
          f"AFTER(hybrid)={fts_recall.get('jordi_after')}   <- the keyword arm's win")
    print(f"      invoice id 'INV-4471' — BEFORE={fts_recall.get('inv_before')}  "
          f"AFTER={fts_recall.get('inv_after')}")
    b_ms = 1000 * sum(b_times) / len(b_times); a_ms = 1000 * sum(a_times) / len(a_times)
    speed = (b_ms / a_ms) if a_ms else float("inf")
    print(f"  speed delta (end-to-end retrieve over {FILLER + len(DOCS)} chunks; both include"
          f" the same ~embed roundtrip): BEFORE={b_ms:.1f}ms  AFTER={a_ms:.1f}ms  → {speed:.1f}× faster")
    print("=" * 92)


if __name__ == "__main__":
    asyncio.run(main())
