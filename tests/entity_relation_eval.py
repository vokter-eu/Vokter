"""Phase-B de-risking measurement (read-only, no model, no DB, no Ollama).

Phase A's "~100% link precision by construction" does NOT transfer to relations: a fact can
CONTAIN "lives in Madrid" and still not assert it (negation, past tense, a different subject).
So this harness is deliberately ADVERSARIAL — it mixes clean positives with grounded-but-FALSE
traps, and reports precision INCLUDING the traps. A number from clean-only facts would be a lie.

  * relation PRECISION — of relations extracted, how many are true (traps must NOT be emitted).
  * relation RECALL    — of the gold relations, how many are extracted (per predicate).

Closed set only: child-of / parent-of / attends / lives-in / works-at. Anything else = Phase C.

Run:  desktop/runtime/venv/bin/python tests/entity_relation_eval.py   (or any python3 — stdlib only)
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import entities  # noqa: E402

# Known persons (as Phase A would have discovered them from family phrasing in these facts).
PERSONS = {"emma", "leo", "lucia", "sara"}

# (fact, gold relations as (subj_norm, predicate, obj_norm)) — [] means NO relation (a trap
# or a plain fact). SELF is entities.SELF_NAME normalised.
SELF = entities.norm(entities.SELF_NAME)
DATA = [
    # --- clean positives ---
    ("My daughter Emma is 6 years old",                         [("emma", "child-of", SELF)]),
    ("My son Leo plays football",                               [("leo", "child-of", SELF)]),
    ("Mi hija Lucía estudia en la Universidad de Valencia",
        [("lucia", "child-of", SELF), ("lucia", "attends", "universidad de valencia")]),
    ("Emma attends Oakwood Primary School",                     [("emma", "attends", "oakwood primary school")]),
    ("Emma lives in Barcelona",                                 [("emma", "lives-in", "barcelona")]),
    ("Leo works at Siemens",                                    [("leo", "works-at", "siemens")]),
    ("Sara works for the BBC",                                  [("sara", "works-at", "bbc")]),
    # --- traps: grounded but FALSE → must emit NOTHING ---
    ("Emma doesn't live in Madrid",                             []),   # negation
    ("Emma used to work at Siemens",                            []),   # past tense
    ("Emma's brother works at Siemens",                         []),   # subject misattribution
    ("Emma lives in Our house",                                 []),   # junk/pronoun object
    ("Emma no longer attends Oakwood",                          []),   # negation
    ("It is not true that Leo lives in Paris",                  []),   # distant negation
    # --- plain facts / out-of-set family → no relation ---
    ("Emma loves dinosaurs",                                    []),
    ("My wife Sara is a dentist",                               []),   # spouse not in closed set
    ("Emma's teacher is Mr Dawson",                             []),   # no predicate verb
]


def main() -> int:
    gold = set()
    gold_by_pred = {}
    for _fact, rels in DATA:
        for r in rels:
            gold.add(r)
            gold_by_pred.setdefault(r[1], set()).add(r)

    extracted = set()
    per_fact = []
    for fact, _rels in DATA:
        got = entities.extract_relations(fact, PERSONS)
        norm_got = {(entities.norm(s), p, entities.norm(o)) for s, p, o, _t in got}
        extracted |= norm_got
        per_fact.append((fact, norm_got, set(_rels)))

    tp = extracted & gold
    fp = extracted - gold
    fn = gold - extracted

    precision = len(tp) / len(extracted) if extracted else 1.0
    recall = len(tp) / len(gold) if gold else 1.0

    print("=== Phase-B heuristic relation extraction — ADVERSARIAL measurement ===")
    print(f"facts: {len(DATA)}  (gold relations: {len(gold)}, traps/plain: "
          f"{sum(1 for _f, r in DATA if not r)})\n")
    for fact, got, want in per_fact:
        mark = "ok " if got == want else "XX "
        extra = got - want
        missing = want - got
        note = ""
        if extra:
            note += f"  +FALSE:{sorted(extra)}"
        if missing:
            note += f"  -MISSED:{sorted(missing)}"
        print(f"  {mark}{fact!r}{note}")
    print()
    print(f"RELATION PRECISION (incl. traps): {precision:.3f}  ({len(tp)}/{len(extracted)})")
    print(f"RELATION RECALL                 : {recall:.3f}  ({len(tp)}/{len(gold)})")
    print("  recall by predicate:")
    for pred in entities.PREDICATES:
        g = gold_by_pred.get(pred, set())
        if g:
            print(f"    {pred:10s}: {len(g & tp)}/{len(g)}")
    if fp:
        print(f"\n  FALSE POSITIVES (precision killers): {sorted(fp)}")
    if fn:
        print(f"  MISSED (recall gap): {sorted(fn)}")
    # The headline honesty check: NO trap may produce a relation.
    trap_leaks = [f for f, got, want in per_fact if not want and got]
    print(f"\n  traps that leaked a relation (must be EMPTY): {trap_leaks}")
    return 0 if not fp else 1


if __name__ == "__main__":
    raise SystemExit(main())
