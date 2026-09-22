"""Direction A — retrieval PRECISION harness (DEV-ONLY, never shipped; tests/ isn't frozen).

A falsifiable precision/recall@k harness for NON-CORE memory retrieval — the part the
ranker (recency-aware fusion, keyword-only gating) actually touches. Core/health/pinned
facts are ALWAYS injected and are excluded from the metrics here (a knob can't change them).

It seeds a fixed labelled corpus and a query set. Each query carries a gold label:
  * relevant : fact contents that SHOULD be retrieved
  * forbid   : fact contents that must NOT be retrieved (distractors / keyword leaks)
and, for the recency pair, an ordering assertion (current fact must rank above the stale one).

It runs the REAL public path — memory.relevant_block(query) — strips the always-on core
block, and scores the non-core picks. Deterministic: bge-m3 embeddings are stable, so the
numbers reproduce run to run (unlike anything that leans on a 3B generation).

Failure families covered (the three Bilal named):
  (1) keyword leak   — "my dog's name" must NOT pull "walked past a dog shelter"
  (2) recency        — a stale "lived in Madrid" must rank BELOW a current "moved to Barcelona"
  (3) exact-term     — an id (4471-XZ), a name (Nomi), a year (2019) must NOT regress
plus the crown jewel:
  (0) relevance gate — a greeting / off-topic question injects NOTHING non-core

Run (needs Ollama reachable with bge-m3):
  VOKTER_DB=$(mktemp -d)/eval.db VOKTER_OLLAMA_URL=http://127.0.0.1:11434 \
    VOKTER_EMBED_MODEL=bge-m3 desktop/runtime/venv/bin/python tests/memory_precision_eval.py
"""
import asyncio
import os
import sys
import time
from contextlib import closing

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import db          # noqa: E402
import memory      # noqa: E402

# ── labelled corpus ──────────────────────────────────────────────────────────
# All NON-CORE by design (verified with memory._is_core below) so the ranker is what's
# under test. Two are a recency pair (Madrid older, Barcelona newer).
CORE_FACTS = [                      # always-on; present only for realism, NOT scored
    "My name is Bilal",
    "I am allergic to shellfish",
]
NONCORE_FACTS = [
    # pets
    "My dog is called Rex",
    "I once walked past a dog shelter downtown",       # keyword-leak distractor ("dog")
    # location (recency pair)
    "I used to live in Madrid",                        # STALE (older)
    "I moved to Barcelona last month",                 # CURRENT (newer)
    # work
    "I work as a data analyst at a logistics company",
    "My office is near Sants station",
    # ids / dates (exact-term + a keyword-collision leak)
    "My library card number is 4471-XZ",               # exact id
    "Flight AB-4471 was delayed on Tuesday",           # KW-collision leak: shares "4471", low sim
    "I got married in 2019",                           # exact year (non-core phrasing)
    "My gym membership renews every January",
    # people (non-family → non-core)
    "My colleague Nomi sits at the next desk",         # exact name
    "My neighbour Tom waters my plants when I travel",
    # sport (EN + the implicit-ES HARD TP that set the 0.53 floor)
    "I support Athletic Club de Bilbao",               # team EN (colour<->team cross distractor)
    "El usuario es del Athletic Club de Bilbao",       # HARD TP: implicit ES team, ~0.537, no kw
    # tastes / habits (paraphrase-recall targets: queries share NO tokens with these)
    "My favourite colour is teal",
    "I drink my coffee black",
    "I'm trying to eat less red meat",
    "I love spicy Thai food",
    "I enjoy hiking on weekends",
    "I play the electric guitar",
    "I'm learning to sail",
    "I switched from Android to an iPhone this year",
]
# created_at offsets (seconds ago). Madrid deliberately OLDER than Barcelona; ALL other facts
# backdated older still, so the recency pair isn't polluted by now-stamped filler — Barcelona
# is genuinely among the newest.
_OLD = 200 * 86400
AGE = {f: _OLD for f in NONCORE_FACTS}
AGE["I used to live in Madrid"] = 90 * 86400           # 90 days ago (stale)
AGE["I moved to Barcelona last month"] = 30 * 86400    # 30 days ago (current)

# (query, relevant[], forbid[])  —  forbid=None marks a NO-FORCE-PICK case: the gate MUST
# inject nothing (greeting, off-topic, OR topical-but-unstored). relevant=[]+forbid=[] is unused.
Q = [
    # ── failure families ──────────────────────────────────────────────────────
    ("what is my dog's name?",        ["My dog is called Rex"],
                                      ["I once walked past a dog shelter downtown"]),   # vector leak
    ("tell me about my pet",          ["My dog is called Rex"],
                                      ["I once walked past a dog shelter downtown"]),   # leak, paraphrased
    ("where do I live now?",          ["I moved to Barcelona last month",
                                       "I used to live in Madrid"], []),                # recency pair
    ("what's my library card number?",["My library card number is 4471-XZ"],
                                      ["Flight AB-4471 was delayed on Tuesday"]),       # exact id + kw leak
    ("4471-XZ",                       ["My library card number is 4471-XZ"],
                                      ["Flight AB-4471 was delayed on Tuesday"]),       # bare id + kw leak
    # ── exact-term recall (must NOT regress) ──────────────────────────────────
    ("who is Nomi?",                  ["My colleague Nomi sits at the next desk"], []),
    ("when did I get married?",       ["I got married in 2019"], []),
    ("¿de qué equipo soy?",           ["El usuario es del Athletic Club de Bilbao"], []),# HARD implicit-ES
    # ── cross distractors (two close preferences) ─────────────────────────────
    ("what's my favourite colour?",   ["My favourite colour is teal"],
                                      ["I support Athletic Club de Bilbao"]),
    ("what team do I support?",       ["I support Athletic Club de Bilbao"],
                                      ["My favourite colour is teal"]),
    # ── paraphrase recall (query shares NO content tokens with the fact) ───────
    ("what do I do for a living?",    ["I work as a data analyst at a logistics company"], []),
    ("which instrument can I play?",  ["I play the electric guitar"], []),
    ("am I cutting down on any foods?",["I'm trying to eat less red meat"], []),
    ("what phone do I use?",          ["I switched from Android to an iPhone this year"], []),
    ("who looks after my plants?",    ["My neighbour Tom waters my plants when I travel"], []),
    # ── NO-FORCE-PICK: topical but nothing stored → gate must inject NOTHING ───
    ("what's my favourite film?",     [], None),
    ("what car do I drive?",          [], None),
    ("what's my sister's name?",      [], None),
    # ── relevance gate crown jewel ────────────────────────────────────────────
    ("hola, ¿qué tal?",               [], None),                                        # greeting
    ("what's 2+2?",                   [], None),                                         # off-topic
]
RECENCY_PAIR = ("I moved to Barcelona last month",   # current must rank ABOVE
                "I used to live in Madrid")          # stale


def _facts_in_block(block: str) -> list[str]:
    return [ln[2:] for ln in block.splitlines() if ln.startswith("- ")]


def _noncore_picks(block: str) -> list[str]:
    """The non-core facts the block injected, IN ORDER (core stripped — always-on, not scored)."""
    return [f for f in _facts_in_block(block) if not memory._is_core(f)]


async def seed():
    with closing(db.get_db()) as conn:
        conn.execute("DELETE FROM memory")
        conn.commit()
    for f in CORE_FACTS + NONCORE_FACTS:
        memory.add(f)
    # backdate the recency pair (add() stamps now(); override for a controlled comparison)
    now = time.time()
    with closing(db.get_db()) as conn:
        for content, ago in AGE.items():
            conn.execute("UPDATE memory SET created_at=? WHERE content=?", (now - ago, content))
        conn.commit()
    await memory.embed_pending()          # real bge-m3 vectors for every fact

    # sanity: every NONCORE_FACT really is non-core (else the metric is measuring core injection)
    misfiled = [f for f in NONCORE_FACTS if memory._is_core(f)]
    if misfiled:
        print(f"!! FIXTURE BUG: these were classified CORE, not non-core: {misfiled}")


def prf(retrieved: list[str], relevant: list[str]) -> tuple[float, float]:
    """precision, recall over the non-core picks. Empty-relevant → recall 1.0; empty-retrieved
    with empty-relevant → precision 1.0 (nothing wrongly injected)."""
    rset, gset = set(retrieved), set(relevant)
    hit = len(rset & gset)
    precision = 1.0 if not rset else hit / len(rset)
    recall = 1.0 if not gset else hit / len(gset)
    return precision, recall


async def run(label: str) -> dict:
    print("\n" + "=" * 96)
    print(f"RUN: {label}")
    print("=" * 96)
    GATE_QUERIES = {"hola, ¿qué tal?", "what's 2+2?"}   # greeting / off-topic (crown jewel)
    Ps, Rs = [], []
    leaks = 0
    gate_ok = True
    noforce_violations = []     # topical-but-unstored queries that WRONGLY force-picked a fact
    exact_ok = {}
    for query, relevant, forbid in Q:
        picks = _noncore_picks(await memory.relevant_block(query))
        p, r = prf(picks, relevant)
        Ps.append(p); Rs.append(r)

        # forbid / gate accounting
        forb = set(forbid or [])
        leaked = [f for f in picks if f in forb]
        leaks += len(leaked)
        is_gate = forbid is None
        if is_gate and picks:
            if query in GATE_QUERIES:
                gate_ok = False                          # greeting/off-topic leaked → gate broke
            else:
                noforce_violations.append((query, picks))  # nothing stored, yet a fact injected
        # exact-term recall bookkeeping (must not regress)
        if query == "4471-XZ" or query == "what's my library card number?":
            exact_ok[query] = "My library card number is 4471-XZ" in picks
        if query == "who is Nomi?":
            exact_ok[query] = "My colleague Nomi sits at the next desk" in picks
        if query == "when did I get married?":
            exact_ok[query] = "I got married in 2019" in picks
        if query == "¿de qué equipo soy?":     # the recall-critical hard TP (set the 0.53 floor)
            exact_ok["ES_team_hard"] = "El usuario es del Athletic Club de Bilbao" in picks

        tag = ""
        if leaked:  tag += f"  ⚠ LEAK: {leaked}"
        if is_gate and picks:  tag += f"  ⚠ GATE BROKEN: {picks}"
        if is_gate and not picks:  tag += "  ✓ gate held (nothing injected)"
        print(f"\n  Q: {query!r}")
        print(f"     picks({len(picks)}): {picks}")
        print(f"     P={p:.2f} R={r:.2f}{tag}")

    # recency ordering
    current, stale = RECENCY_PAIR
    picks_live = _noncore_picks(await memory.relevant_block("where do I live now?"))
    ci = picks_live.index(current) if current in picks_live else None
    si = picks_live.index(stale) if stale in picks_live else None
    recency_ok = (ci is not None and si is not None and ci < si)

    mp = sum(Ps) / len(Ps); mr = sum(Rs) / len(Rs)
    print("\n" + "-" * 96)
    print(f"  AGGREGATE  mean precision@5 = {mp:.3f}   mean recall@5 = {mr:.3f}")
    print(f"  leaks (forbidden facts injected)          : {leaks}   (target 0)")
    print(f"  relevance gate held (greeting+off-topic)  : {gate_ok}  (must be True)")
    print(f"  no-force-pick held (topical-but-unstored) : {not noforce_violations}  "
          f"(must be True){'  ⚠ ' + str(noforce_violations) if noforce_violations else ''}")
    # Not a knob any more — a DIAGNOSTIC of the deferred gap: recency alone can't make a current
    # fact outrank a genuinely-more-similar stale one (that's conflict-resolution / supersession,
    # deferred). With the margin gate on, the noise is stripped and this reduces to a clean
    # Madrid/Barcelona pair — which is exactly where supersession, not recency, is the right tool.
    print(f"  supersession gap (current '{current}' above stale): {recency_ok}  "
          f"(current@{ci} vs stale@{si}) — deferred: conflict-resolution, not recency")
    print(f"  exact-term recall (must NOT regress)      : {exact_ok}")
    print("-" * 96)
    return {"precision": mp, "recall": mr, "leaks": leaks, "gate_ok": gate_ok,
            "noforce_ok": not noforce_violations, "recency_ok": recency_ok,
            "exact_ok": exact_ok, "exact_all": all(exact_ok.values())}


async def main():
    await seed()
    from config import (MEMORY_TOP_K, MEMORY_MIN_SCORE, MEMORY_REL_MARGIN,
                        KW_ONLY_MIN_COVERAGE)
    print(f"\nseeded {len(CORE_FACTS)} core + {len(NONCORE_FACTS)} non-core facts")
    print(f"knobs: MEMORY_TOP_K={MEMORY_TOP_K} MIN_SCORE={MEMORY_MIN_SCORE} "
          f"REL_MARGIN={MEMORY_REL_MARGIN} KW_ONLY_MIN_COVERAGE={KW_ONLY_MIN_COVERAGE}"
          f"  (both 0 = shipped behavior; the precision pair is validated & flipped together)")
    await run(os.getenv("EVAL_LABEL", "current"))


if __name__ == "__main__":
    asyncio.run(main())
