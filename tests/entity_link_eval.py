"""Phase-A de-risking measurement (read-only, no model, no DB, no Ollama).

Question the go/no-go hinges on: when a user asks "what do I know about Emma", how much
of what Vokter actually knows about her does the DETERMINISTIC linker recover — and at
what precision? Two numbers, measured against a human-labeled fact set:

  * link PRECISION — of facts the linker attaches to Emma, how many are truly about her.
    ~100% by construction (link only on a literal name/alias token match).
  * link RECALL   — of facts a human labels "about Emma", how many does the linker catch.
    This is the build-now justification AND the quantified coreference gap: every missed
    fact is a pronoun/implicit one (Phase C), never a named one.

The fact set is a REPRESENTATIVE bilingual (en+es) persona, hand-labeled below. It is not
the user's real store (that's encrypted and not ours to read); real recall skews HIGHER,
because both the explicit "remember that..." UX and the Phase-2 extractor prompt
("keep relationships explicit: 'His daughter Nomi is 6', not 'Nomi is 6'") bias stored
facts toward NAMING the person. So treat this recall as a conservative floor.

Run:  desktop/runtime/venv/bin/python tests/entity_link_eval.py   (or any python3 — stdlib only)
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import entities  # noqa: E402  (pure stdlib module; measures the exact Phase-A logic)

# (id, fact, about_emma, how) — `how` documents the human label, not used by the linker.
#   named     : literally names "Emma"            -> linker SHOULD catch
#   alias     : names a role alias ("my daughter")-> linker SHOULD catch (via alias)
#   coref     : about Emma but names no one       -> linker will MISS (Phase C)
#   other     : not about Emma                    -> linker must NOT link (precision)
DATA = [
    # --- about Emma, NAMED (expect: linked) ---
    (1,  "My daughter Emma is 6 years old",                      True,  "named+alias"),
    (2,  "Emma attends Oakwood Primary School",                  True,  "named"),
    (3,  "Emma is allergic to peanuts",                          True,  "named"),
    (4,  "Mi hija Emma nació el 3 de marzo",                     True,  "named+alias"),
    (5,  "Emma's favourite colour is purple",                    True,  "named"),
    (6,  "I signed Emma up for swimming lessons",                True,  "named"),
    (7,  "Emma's teacher is Mr. Dawson",                         True,  "named"),
    (8,  "Emma and Leo share a bedroom",                         True,  "named"),
    # --- about Emma, ALIAS only, no name (expect: linked via alias) ---
    (9,  "My daughter loves dinosaurs",                          True,  "alias"),
    (10, "Mi hija tiene clase de piano los lunes",               True,  "alias"),
    # --- about Emma, COREFERENCE/implicit, no name or alias (expect: MISS -> Phase C) ---
    (11, "She has swimming on Tuesdays",                         True,  "coref"),
    (12, "Picked her up from school early today",                True,  "coref"),
    (13, "Her birthday party is at the park on Saturday",        True,  "coref"),
    (14, "Le encanta dibujar animales",                          True,  "coref"),
    (15, "The kids' school is closed on Friday",                 True,  "coref"),  # collective
    # --- NOT about Emma (expect: NOT linked -> precision) ---
    (16, "My son Leo plays football on weekends",               False, "other-person"),
    (17, "My wife Sara is a dentist",                            False, "other-person"),
    (18, "Our dog Max needs a vet appointment",                 False, "other"),
    (19, "I work at Siemens in Barcelona",                       False, "other"),
    (20, "I'm allergic to shellfish",                            False, "other-self"),
    (21, "I studied at Emmanuel College in Cambridge",          False, "TRAP: Emma in Emmanuel"),
    (22, "I love the show Emily in Paris",                       False, "TRAP: Emily != Emma"),
]


def main() -> int:
    facts = [(i, text) for i, text, _about, _how in DATA]
    ents = entities.build_entities(facts)

    emma = ents.get("emma")
    if emma is None:
        print("FAIL: linker discovered no 'Emma' entity — bootstrap broken.")
        return 1

    linked = emma.fact_ids
    gold = {i for i, _t, about, _h in DATA if about}
    gold_named = {i for i, _t, about, how in DATA if about and how.startswith(("named", "alias"))}

    tp = linked & gold
    fp = linked - gold
    fn = gold - linked

    precision = len(tp) / len(linked) if linked else 1.0
    recall = len(tp) / len(gold) if gold else 1.0
    recall_vs_named = len(tp) / len(gold_named) if gold_named else 1.0

    print("=== Phase-A deterministic linker — measurement ===")
    print(f"entity discovered : {emma.name}  (aliases: {sorted(emma.aliases)})")
    print(f"facts total       : {len(DATA)}")
    print(f"gold 'about Emma' : {len(gold)}  (named/alias: {len(gold_named)}, "
          f"coref/implicit: {len(gold) - len(gold_named)})")
    print()
    print(f"linked by linker  : {sorted(linked)}")
    print(f"  true positives  : {sorted(tp)}")
    print(f"  FALSE positives : {sorted(fp)}   <- precision killers")
    print(f"  false negatives : {sorted(fn)}   <- recall gap (should be ALL coref)")
    print()
    print(f"LINK PRECISION            : {precision:.3f}  ({len(tp)}/{len(linked)})")
    print(f"LINK RECALL (all about-Emma): {recall:.3f}  ({len(tp)}/{len(gold)})")
    print(f"LINK RECALL (vs NAMED only) : {recall_vs_named:.3f}  ({len(tp)}/{len(gold_named)})")
    print()
    # Honesty check: every miss must be a coreference fact, never a named one.
    missed_how = sorted((i, h) for i, _t, about, h in DATA if i in fn)
    named_misses = [(i, h) for i, h in missed_how if h.startswith(("named", "alias"))]
    print(f"missed facts (id, label): {missed_how}")
    if named_misses:
        print(f"WARNING: missed a NAMED/alias fact (not just coref): {named_misses}")
    if fp:
        print(f"WARNING: false-positive link(s): {sorted(fp)} — precision < 100%")
    coref_gap = (len(gold) - len(gold_named)) / len(gold) if gold else 0
    print(f"\nCOREFERENCE GAP (Phase C scope): {coref_gap:.1%} of about-Emma facts are implicit")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
