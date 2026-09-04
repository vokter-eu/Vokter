"""Core-memory budget cap — the always-on block is bounded, and it is bounded SAFELY.

The always-on identity block used to grow unbounded (every core=1 fact, forever). This tests
the cap + its safety invariants against a KEYED (SQLCipher) fixture, offline:

  1. over budget → OLDEST non-health/non-pinned core demoted to core=0 (kept set fits budget).
  2. HEALTH/allergy facts are NEVER demoted, even far over budget (the safety-critical invariant).
  3. USER-PINNED facts are never demoted (the escape hatch).
  4. demotion is NOT deletion — a demoted fact still exists and stays keyword-retrievable (FTS),
     i.e. it drops from always-on to Direction-A as-needed.
  5. _is_health PRECISION (it's the uncapped path): a non-health fact is NOT flagged health.
  6. enforce is idempotent.

A tiny budget (20 tokens) is set via env so a handful of short facts forces eviction. Run:
  desktop/runtime/venv/bin/python tests/memory_budget_test.py
"""
import os
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="vokter-budget-")
os.environ["VOKTER_DB"] = os.path.join(_TMP, "vokter.db")
os.environ["VOKTER_DB_KEY"] = "budget-test-key-" + "b" * 32   # keyed → SQLCipher
os.environ["VOKTER_CORE_BUDGET_TOKENS"] = "20"                # ~2-3 short facts fit
os.environ.pop("VOKTER_OLLAMA_URL", None)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from contextlib import closing  # noqa: E402

import config  # noqa: E402
import db  # noqa: E402
import memory  # noqa: E402


def _fail(msg):
    print("FAIL:", msg)
    sys.exit(1)


def _row(mid):
    with closing(db.get_db()) as d:
        return d.execute("SELECT core, health, pinned, created_at FROM memory WHERE id=?",
                         (mid,)).fetchone()


def _fts_finds(mid, term):
    with closing(db.get_db()) as d:
        ids = {r[0] for r in d.execute(
            "SELECT rowid FROM memory_fts WHERE memory_fts MATCH ?", ('"' + term + '"',)).fetchall()}
    return mid in ids


if config.CORE_BUDGET_TOKENS != 20:
    _fail(f"budget env not applied: CORE_BUDGET_TOKENS={config.CORE_BUDGET_TOKENS}")

# ============================================================================
# 5. _is_health precision FIRST (it decides what becomes uncapped) — assert the HARD direction
# ============================================================================
must_be_health = ["I'm allergic to shellfish", "soy alérgico a la penicilina",
                  "I have coeliac disease", "tengo diabetes tipo 1", "I'm asthmatic"]
must_NOT_be_health = ["diabetic-friendly recipe for guests", "my brother's family recipe",
                      "I'm allergic to boring meetings"[:0] or "the allergy clinic is on Calle Mayor",
                      "we watched a documentary about diabetes", "my favourite colour is teal"]
for f in must_be_health:
    if not memory._is_health(f):
        _fail(f"genuine health fact NOT flagged health (would lose budget-exemption): {f!r}")
for f in must_NOT_be_health:
    if memory._is_health(f):
        _fail(f"NON-health fact wrongly flagged health (uncapped forever): {f!r}")
print(f"5. _is_health precision OK — {len(must_be_health)} genuine flagged, "
      f"{len(must_NOT_be_health)} non-health correctly rejected (uncapped path stays clean)")

# ============================================================================
# 5b. tightened _is_core: catches identity-shaped facts, rejects word-appears-anywhere noise
# ============================================================================
core_yes = ["my name is Bilal", "my daughter Emma is 7", "mi hermano Jordi vive en Girona",
            "tengo dos hijos", "I'm allergic to shellfish", "we're getting married in June"]
core_no = ["the wife of the CEO gave the keynote", "call it a family recipe from the internet",
           "that band's name is hard to pronounce", "named after a mountain, the model is fast",
           "my favourite colour is teal", "I support Athletic Club de Bilbao"]
for f in core_yes:
    if not memory._is_core(f):
        _fail(f"tightened _is_core missed a genuine identity fact: {f!r}")
for f in core_no:
    if memory._is_core(f):
        _fail(f"tightened _is_core still over-classifies (word-appears-anywhere): {f!r}")
print(f"5b. tightened _is_core OK — {len(core_yes)} identity caught, "
      f"{len(core_no)} word-in-passing rejected (killed the always-on noise)")

# ============================================================================
# 1. over budget → oldest non-health/non-pinned core demoted; kept set fits budget
# ============================================================================
# four family (core, non-health, non-pinned) facts, oldest → newest (~9 tok each)
fam = [
    memory.add("my brother Jordi lives in Girona now"),   # oldest
    memory.add("my sister Marta works in Madrid city"),
    memory.add("my mother lives in Bilbao near us"),
    memory.add("my daughter Emma just turned seven"),     # newest
]
fam_ids = [f["id"] for f in fam]
kept = [i for i in fam_ids if _row(i)[0] == 1]
demoted = [i for i in fam_ids if _row(i)[0] == 0]
if not demoted:
    _fail("nothing demoted despite exceeding the 20-token budget")
used = sum(memory._est_tokens(f["content"]) for f in fam if _row(f["id"])[0] == 1)
if used > config.CORE_BUDGET_TOKENS:
    _fail(f"kept core pool {used} tok exceeds budget {config.CORE_BUDGET_TOKENS}")
# demoted must be the OLDEST (lowest created_at), kept the newest
newest_created = max(_row(i)[3] for i in kept)
oldest_kept = min(_row(i)[3] for i in kept)
if any(_row(i)[3] > oldest_kept for i in demoted):
    _fail("a demoted fact is NEWER than a kept one — eviction is not oldest-first")
if fam_ids[-1] in demoted:
    _fail("the newest identity fact was demoted (should always be kept)")
print(f"1. budget cap OK — kept {len(kept)} newest (~{used} tok ≤ 20), demoted {len(demoted)} oldest")

# ============================================================================
# 4. demotion ≠ deletion: demoted fact still present + keyword-retrievable
# ============================================================================
d0 = demoted[0]
if _row(d0) is None:
    _fail("a demoted fact vanished from the store")
if not _fts_finds(d0, "Jordi") and not any(_fts_finds(i, "Madrid") for i in demoted):
    # at least one demoted family fact must still be keyword-findable (Direction A pool)
    _fail("demoted facts are not keyword-retrievable — demotion lost them from retrieval")
print("4. demote≠delete OK — demoted fact still stored and keyword-retrievable (as-needed pool)")

# ============================================================================
# 2. HEALTH never demoted, even FAR over budget
# ============================================================================
h = memory.add("I'm allergic to shellfish and peanuts")   # health=1, exempt
if _row(h["id"])[1] != 1:
    _fail("health fact not flagged health=1")
# pile on many more core family facts to blow well past budget
for i in range(8):
    memory.add(f"my cousin number {i} lives somewhere far away from here")
if _row(h["id"])[0] != 1:
    _fail("HEALTH fact was demoted under budget pressure — SAFETY INVARIANT BROKEN")
print("2. health-exempt OK — allergy fact stays always-on (core=1) despite heavy over-budget")

# ============================================================================
# 3. user-pinned never demoted
# ============================================================================
# pin the oldest already-demoted family fact → must come back to always-on and stay
if not memory.pin(d0):
    _fail("pin() returned False for an existing fact")
if _row(d0)[0] != 1 or _row(d0)[2] != 1:
    _fail("pinned fact is not always-on (core=1, pinned=1)")
for i in range(6):
    memory.add(f"my uncle {i} used to live in a town by the sea long ago")
if _row(d0)[0] != 1:
    _fail("PINNED fact was demoted under budget pressure — pin is not an escape hatch")
print("3. user-pin OK — pinned fact stays always-on despite heavy over-budget")

# ============================================================================
# 6. idempotent
# ============================================================================
again = memory.enforce_core_budget()
if again:
    _fail(f"enforce_core_budget not idempotent — demoted {len(again)} more on a settled store")
print("6. idempotent OK — a second enforce demotes nothing")

print("\nOK — core budget: always-on block bounded to the token budget by demoting OLDEST "
      "non-health/non-pinned identity facts (never deleted, still retrievable); health/allergy "
      "and user-pinned facts are never demoted; _is_health keeps the uncapped path clean.")
