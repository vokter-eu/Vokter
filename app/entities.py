"""Phase A — person-entity linking for "what do I know about X" (NO model).

A deterministic, fabrication-proof layer over the flat `memory` store: it discovers
PERSON entities from the family-relationship phrasing the user already uses, and links
a fact to an entity ONLY when the entity's name or a known alias LITERALLY appears in
the fact's verbatim text (token-boundary match, not naive substring). So link precision
is ~100% by construction — a link can never claim a fact the text doesn't support.

Scope (honest): this connects facts that NAME the entity (or an alias like "my
daughter"). It does NOT resolve coreference — "she has swimming on Tuesdays" is about
Emma but names no one, so it is NOT linked here. Pronoun/implicit facts are Phase C
(model-based, capable-tier, behind an N>=5 reliability gate). No embeddings are touched;
nothing here needs Ollama.

This module is import-light (re + unicodedata only) so the eval harness can measure the
EXACT linking logic Phase A ships, without booting the backend.
"""
import re
import time
import unicodedata

# Family-relationship words, en + es — mirrors memory._FAMILY_POSS_RE. A possessive +
# one of these + a capitalised name ("my daughter Emma", "mi hija Emma") deterministically
# yields BOTH a person entity ("Emma") and a role alias ("my daughter"/"mi hija").
_REL_EN = (r"husband|wife|spouse|daughter|son|brother|sister|mother|father|mom|mum|dad|"
           r"parent|grandmother|grandfather|grandma|grandpa|fianc\w+|girlfriend|boyfriend|"
           r"partner|kid|child|niece|nephew|aunt|uncle|cousin")
_REL_ES = (r"esposa|esposo|marido|mujer|hija|hijo|hermano|hermana|madre|padre|mam[áa]|pap[áa]|"
           r"abuela|abuelo|pareja|novia|novio|sobrino|sobrina|nieto|nieta|primo|prima|"
           r"t[íi]o|t[íi]a|prometid[oa]")
_POSS = r"my|our|mi|mis|nuestr[oa]s?"
# A capitalised given-name token (allows accented Latin letters).
_NAME = r"[A-ZÁÉÍÓÚÑÜ][\wáéíóúñü]+"

# "my daughter Emma" / "mi hija Emma" / "my daughter named Emma" / "mi hija llamada Emma"
_POSS_REL_NAME = re.compile(
    rf"\b(?P<poss>{_POSS})\s+(?P<rel>{_REL_EN}|{_REL_ES})\s+"
    rf"(?:named\s+|called\s+|llamad[oa]\s+)?(?P<name>{_NAME})\b",
    re.IGNORECASE | re.UNICODE,
)
# "my daughter" / "mi hija" with NO following name — a role alias on its own (fact 12/13).
_POSS_REL = re.compile(
    rf"\b(?P<poss>{_POSS})\s+(?P<rel>{_REL_EN}|{_REL_ES})\b",
    re.IGNORECASE | re.UNICODE,
)
# "I have a daughter named Emma" / "tengo una hija llamada Emma"
_HAVE_REL_NAME = re.compile(
    rf"\b(?:i have|tengo)\s+(?:a |an |one |un |una )?(?P<rel>{_REL_EN}|{_REL_ES})\s+"
    rf"(?:named\s+|called\s+|llamad[oa]\s+)(?P<name>{_NAME})\b",
    re.IGNORECASE | re.UNICODE,
)


def norm(s: str) -> str:
    """Lowercase + strip accents — the matching key (mirrors memory_routes._norm)."""
    s = unicodedata.normalize("NFKD", s.strip().lower())
    return "".join(c for c in s if not unicodedata.combining(c))


def _tokens(text: str) -> set[str]:
    """Normalised word tokens, with a trailing English possessive 's stripped, so
    "Emma's" -> "emma" but "Emmanuel" stays "emmanuel" (no false "emma" token)."""
    out = set()
    for raw in re.findall(r"[^\W_]+(?:'s)?", norm(text), flags=re.UNICODE):
        out.add(raw[:-2] if raw.endswith("'s") else raw)
    return out


class Entity:
    """A discovered person entity: canonical name + the alias phrases that also refer to
    it (role labels like 'my daughter', and later nicknames). Carries the fact ids linked."""
    def __init__(self, name: str):
        self.name = name
        self.norm = norm(name)
        self.aliases: set[str] = set()      # normalised alias phrases ("my daughter")
        self.fact_ids: set[int] = set()


def _is_proper_name(tok: str) -> bool:
    """A real given-name token is capitalised in the source ("Emma"), not an ordinary word
    that merely followed the relation ("My daughter LOVES dinosaurs"). The name classes use
    IGNORECASE for the surrounding words, so this uppercase-initial check — on the ORIGINAL
    text — is what actually separates "Emma" from "loves". Unicode-aware (accents included)."""
    return bool(tok) and tok[0].isupper()


def extract_person_candidates(text: str) -> list[tuple[str, str | None]]:
    """(name, role_alias | None) pairs this fact INTRODUCES via family phrasing. The role
    alias is the possessive+relation phrase ("my daughter") so "what do I know about my
    daughter" can later resolve. Deterministic; no model; no fabrication. A match whose
    'name' token isn't capitalised is NOT a name (just the relation's verb/object) → skipped;
    the alias ("my daughter") is still attached from any name-bearing fact for that person."""
    out: list[tuple[str, str | None]] = []
    for m in _POSS_REL_NAME.finditer(text):
        if not _is_proper_name(m.group("name")):
            continue
        out.append((m.group("name"), f"{m.group('poss')} {m.group('rel')}"))
    for m in _HAVE_REL_NAME.finditer(text):
        if not _is_proper_name(m.group("name")):
            continue
        out.append((m.group("name"), None))
    return out


def extract_role_aliases(text: str) -> list[str]:
    """Role-alias phrases present WITHOUT a name ("my daughter loves dinosaurs"). Used both
    to attach aliases to a known entity and to link alias-only facts."""
    return [f"{m.group('poss')} {m.group('rel')}" for m in _POSS_REL.finditer(text)]


def fact_links_entity(fact_text: str, ent: "Entity") -> bool:
    """True iff `ent`'s NAME (token-boundary) OR one of its alias phrases literally appears
    in the fact — the ~100%-precision link rule. Token match avoids the "Emma" in
    "Emmanuel" trap; alias match is a word-boundary phrase search on the normalised text."""
    if ent.norm in _tokens(fact_text):
        return True
    ntext = norm(fact_text)
    for alias in ent.aliases:
        if re.search(rf"\b{re.escape(alias)}\b", ntext):
            return True
    return False


def build_entities(facts: list[tuple[int, str]]) -> dict[str, Entity]:
    """Two passes over (id, content) facts: (1) discover person entities + role aliases
    from family phrasing; (2) link every fact to every entity whose name/alias it names.
    Returns {entity_norm: Entity}. Pure function — the DB-backed Phase A mirrors this."""
    ents: dict[str, Entity] = {}
    # Pass 1 — discover entities and their aliases (name-bearing phrasings).
    for _id, content in facts:
        for name, alias in extract_person_candidates(content):
            e = ents.setdefault(norm(name), Entity(name))
            if alias:
                e.aliases.add(norm(alias))
    # Pass 1b — attach role aliases to the entity they were introduced with, so an
    # alias-only fact can resolve. An alias binds to an entity if they ever co-occurred
    # in a name-bearing fact (handled above); here we only ensure the alias set is seeded.
    # Pass 2 — link facts by literal name/alias match.
    for _id, content in facts:
        for e in ents.values():
            if fact_links_entity(content, e):
                e.fact_ids.add(_id)
    return ents


# ===========================================================================
# Phase B — HEURISTIC typed relations (closed predicate set, NO model).
#
# A relation is (src_entity) --predicate--> (dst_entity), grounded in a fact's verbatim
# text. UNLIKE Phase A linking, grounding does NOT make a predicate TRUE — "Emma doesn't
# live in Madrid" / "used to work at Siemens" / "Emma's brother works at Siemens" all
# contain the words but assert nothing (or something else). So precision here is EARNED by
# guards, not free: (1) the subject must sit IMMEDIATELY before the verb (no intervening
# tokens, no possessive 's) so a misattributed subject fails to match rather than lying;
# (2) the subject must be a KNOWN person entity; (3) a negation/past-tense marker in the
# window before the match vetoes it; (4) the object must be a proper-noun span, not a
# pronoun/possessive ("Our house"). Measured on an ADVERSARIAL set (tests/
# entity_relation_eval.py) — precision is REPORTED including the traps, not assumed.
#
# Closed set ONLY: child-of / parent-of (from the family possessives Phase A already
# parses), attends, lives-in, works-at. Anything outside it = Phase C (model, capable-tier,
# N>=5 gate). The destination of attends/lives-in/works-at is a place/org entity created as
# the relation ENDPOINT (delimited by the pattern) — NOT general place discovery.
PREDICATES = ("child-of", "parent-of", "attends", "lives-in", "works-at")
SELF_NORM = "__self__"      # the reserved node for "you" (the user), dst of child/parent-of
SELF_NAME = "you"

_CHILD_WORDS = {"daughter", "daughters", "son", "sons", "child", "children", "kid", "kids",
                "hija", "hijo", "hijas", "hijos"}
_PARENT_WORDS = {"mother", "father", "mom", "mum", "dad", "parent", "parents",
                 "madre", "padre", "mama", "papa"}   # norm() has already stripped accents

# A proper-noun object span: capitalised tokens, with lowercase connectors ("de", "of"…)
# allowed only BETWEEN them ("Universidad de Valencia", "University of the Arts"). Bounded
# so it can't run away. NOT compiled with IGNORECASE — the capitalisation IS the signal that
# the object is a proper noun (a place/org), so "lives in barcelona city centre" won't drag
# in the lowercase tail.
_CONNECT = r"de|del|of|the|la|las|los|y|&"
_CAPSPAN = rf"{_NAME}(?:\s+(?:{_CONNECT})\s+{_NAME}|\s+{_NAME}){{0,4}}"
# Optional article immediately after the verb, consumed so the object starts at the name.
_ART = r"(?:the\s+|a\s+|an\s+|la\s+|el\s+|los\s+|las\s+|una?\s+)?"

# Subject-verb-object predicate patterns. Subject ADJACENT to the verb (\s+, no 's). Verbs
# lowercased literals (how facts actually read: "Emma lives in Barcelona"). Case-sensitive
# so the object stays a proper noun.
_ATTENDS_RE = re.compile(
    rf"\b(?P<subj>{_NAME})\s+(?:attends|studies\s+at|is\s+enrolled\s+at|"
    rf"asiste\s+a|estudia\s+en|estudian\s+en)\s+{_ART}(?P<obj>{_CAPSPAN})", re.UNICODE)
_LIVES_RE = re.compile(
    rf"\b(?P<subj>{_NAME})\s+(?:lives\s+in|live\s+in|lives\s+at|resides\s+in|"
    rf"vive\s+en|vivo\s+en|viven\s+en)\s+{_ART}(?P<obj>{_CAPSPAN})", re.UNICODE)
_WORKS_RE = re.compile(
    rf"\b(?P<subj>{_NAME})\s+(?:works\s+at|work\s+at|works\s+for|is\s+employed\s+(?:at|by)|"
    rf"employed\s+(?:at|by)|trabaja\s+en|trabajo\s+en|trabajan\s+en)\s+{_ART}(?P<obj>{_CAPSPAN})",
    re.UNICODE)
_SVO = (("attends", _ATTENDS_RE, "org"),
        ("lives-in", _LIVES_RE, "place"),
        ("works-at", _WORKS_RE, "org"))

# Negation / non-current markers that, if they appear just before the match, veto it.
_NEG = re.compile(
    r"\b(?:doesn't|does not|do not|don't|didn't|did not|not|no longer|never|used to|"
    r"formerly|once|ya no|nunca|sol[íi]a|dej[óo] de)\b", re.IGNORECASE | re.UNICODE)
# Object first-token stoplist: a pronoun/possessive/demonstrative is never a proper place.
_OBJ_STOP = {"our", "my", "your", "his", "her", "their", "its", "this", "that", "these",
             "those", "nuestro", "nuestra", "nuestros", "nuestras", "mi", "mis", "tu", "tus",
             "su", "sus", "este", "esta", "esos", "esas", "ese", "esa"}


def _family_predicate(rel_norm: str) -> str | None:
    if rel_norm in _CHILD_WORDS:
        return "child-of"
    if rel_norm in _PARENT_WORDS:
        return "parent-of"
    return None          # other family words (brother/wife…) are NOT in the closed set


def _negated(text: str, start: int) -> bool:
    """A negation/past-tense marker in the ~30 chars before the match → veto (grounded
    but not asserted-as-current)."""
    return bool(_NEG.search(text[max(0, start - 30):start]))


def extract_family_relations(text: str) -> list[tuple[str, str]]:
    """(name, predicate) for child-of / parent-of, from the SAME family phrasing Phase A
    parses ("my daughter Emma" → Emma child-of you). Deterministic, grounded, no model."""
    out: list[tuple[str, str]] = []
    for rx in (_POSS_REL_NAME, _HAVE_REL_NAME):
        for m in rx.finditer(text):
            if not _is_proper_name(m.group("name")):
                continue
            pred = _family_predicate(norm(m.group("rel")))
            if pred:
                out.append((m.group("name"), pred))
    return out


def extract_typed_relations(text: str, known_person_norms: set[str]) -> list[tuple[str, str, str, str]]:
    """(subject, predicate, object, object_type) for attends / lives-in / works-at. Subject
    must be a KNOWN person (bound adjacent to the verb); guarded against negation, past tense
    and non-proper objects. Grounded — but precision is MEASURED, not assumed."""
    out: list[tuple[str, str, str, str]] = []
    for pred, rx, otype in _SVO:
        for m in rx.finditer(text):
            if norm(m.group("subj")) not in known_person_norms:
                continue                                   # subject not a known person → skip
            if _negated(text, m.start()):
                continue                                   # "doesn't / used to / no longer" → skip
            obj = m.group("obj").strip()
            toks = norm(obj).split()
            if not toks or toks[0] in _OBJ_STOP:
                continue                                   # "Our house" etc. → skip
            out.append((m.group("subj"), pred, obj, otype))
    return out


def extract_relations(text: str, known_person_norms: set[str]) -> list[tuple[str, str, str, str]]:
    """All closed-set relations in a fact: (subject, predicate, object, object_type). Family
    relations carry object = SELF_NAME, object_type 'self'. Pure — the DB reindex mirrors it."""
    rels: list[tuple[str, str, str, str]] = [
        (name, pred, SELF_NAME, "self") for name, pred in extract_family_relations(text)]
    rels += extract_typed_relations(text, known_person_norms)
    return rels


# ---------------------------------------------------------------------------
# DB-backed Phase A + B: discover + link into the entity tables, extract typed
# relations, resolve an entity query, and gather its connected facts + connections.
# The `db` connection is ALWAYS passed in (never imported here) so this module stays
# import-light and the eval harness can measure the pure logic above without booting
# the backend / opening a DB.
# ---------------------------------------------------------------------------

# The ONLY trigger for entity-gather. Strict, whole-message templates anchored at the
# start — so a normal conversational turn NEVER matches and NEVER bypasses the similarity
# gate. This is the load-bearing invariant: entity-gather (which ignores the margin gate
# by design) fires exclusively on an explicit "what do I know about X" / "qué sabes de X".
_ENTITY_QUERY = re.compile(
    r"^\s*(?:"
    r"what\s+do\s+(?:i|you|we)\s+know\s+(?:about|of)\s+|"
    r"what\s+do\s+(?:i|you|we)\s+remember\s+about\s+|"
    r"tell\s+me\s+(?:about|what\s+you\s+know\s+about)\s+|"
    r"qu[ée]\s+s[ée]\s+(?:de|sobre|acerca\s+de)\s+|"
    r"qu[ée]\s+sabes\s+(?:de|sobre|acerca\s+de)\s+|"
    r"qu[ée]\s+sabemos\s+(?:de|sobre|acerca\s+de)\s+|"
    r"cu[ée]ntame\s+(?:sobre|de|acerca\s+de)\s+|"
    r"h[áa]blame\s+(?:de|sobre|acerca\s+de)\s+"
    r")(?P<term>.+?)\s*[?.!]*\s*$",
    re.IGNORECASE | re.UNICODE,
)


def parse_entity_query(text: str) -> str | None:
    """The person term of an explicit 'what do I know about X' query, else None. A None
    (the overwhelmingly common case) means: leave the normal gated retrieval path alone."""
    m = _ENTITY_QUERY.match(text or "")
    if not m:
        return None
    term = m.group("term").strip()
    # Drop a leading possessive so "my daughter" still resolves via the alias table.
    return term or None


def _upsert_entity(db, name: str) -> int:
    """Entity id for `name`, creating it if new. If `name` is already an ALIAS of another
    entity (e.g. after a user merge), return that entity — so discovery can't recreate the
    merged-away node and the merge survives reindex."""
    n = norm(name)
    row = db.execute("SELECT id FROM entities WHERE norm=? AND type='person'", (n,)).fetchone()
    if row:
        return row[0]
    row = db.execute("SELECT entity_id FROM entity_aliases WHERE alias_norm=?", (n,)).fetchone()
    if row:
        return row[0]
    cur = db.execute(
        "INSERT INTO entities(name, norm, type, created_at) VALUES(?,?, 'person', ?)",
        (name, n, time.time()))
    return cur.lastrowid


def _upsert_typed_entity(db, name: str, etype: str) -> int:
    """Entity id for a non-person relation endpoint (place/org), by (norm, type). No alias/
    merge logic — these exist only as the destination of a grounded relation."""
    n = norm(name)
    row = db.execute("SELECT id FROM entities WHERE norm=? AND type=?", (n, etype)).fetchone()
    if row:
        return row[0]
    cur = db.execute(
        "INSERT INTO entities(name, norm, type, created_at) VALUES(?,?,?,?)",
        (name, n, etype, time.time()))
    return cur.lastrowid


def _self_entity_id(db) -> int:
    """The reserved 'you' node (the user), destination of child-of / parent-of. Created on
    demand; kept alive by the family relations that reference it (GC keeps relation endpoints)."""
    row = db.execute("SELECT id FROM entities WHERE norm=? AND type='person'",
                     (SELF_NORM,)).fetchone()
    if row:
        return row[0]
    cur = db.execute(
        "INSERT INTO entities(name, norm, type, created_at) VALUES(?,?, 'person', ?)",
        (SELF_NAME, SELF_NORM, time.time()))
    return cur.lastrowid


def reindex(db) -> None:
    """Rebuild the entity index + typed relations from the CURRENT facts (cheap: personal
    stores are small). Additive + correction-preserving: INSERT OR IGNORE links never
    resurrect a user-suppressed row, entity discovery honours merge-aliases, and a suppressed
    link also vetoes any relation derived from that fact+person. Relations are fully rebuilt
    (derived). Then GC entities referenced by NEITHER a fact-link NOR a relation. Caller
    commits via this function. Deterministic, no model, no re-embed."""
    facts = db.execute("SELECT id, content FROM memory").fetchall()
    # Pass 1 — discover person entities + their role aliases.
    for _fid, content in facts:
        for name, alias in extract_person_candidates(content):
            eid = _upsert_entity(db, name)
            if alias:
                db.execute(
                    "INSERT OR IGNORE INTO entity_aliases(entity_id, alias_norm) VALUES(?,?)",
                    (eid, norm(alias)))
    # Pass 2 — link each fact to every entity it literally names (preserving suppressed).
    # EXCLUDE the reserved self node: its stored norm is '__self__' but Entity("you").norm is
    # "you", so leaving it in would link any fact containing the word "you" to self (and make
    # "you" a relation subject). Self is created in Pass 3 as the family dst and kept by the
    # relation-referenced GC — it must never be a fact-link target or an SVO subject.
    ent_rows = db.execute(
        "SELECT id, name FROM entities WHERE type='person' AND norm != ?", (SELF_NORM,)).fetchall()
    ents = []
    for eid, ename in ent_rows:
        e = Entity(ename)
        e.aliases = {r[0] for r in db.execute(
            "SELECT alias_norm FROM entity_aliases WHERE entity_id=?", (eid,)).fetchall()}
        ents.append((eid, e))
    for fid, content in facts:
        for eid, e in ents:
            if fact_links_entity(content, e):
                db.execute(
                    "INSERT OR IGNORE INTO fact_entities(memory_id, entity_id) VALUES(?,?)",
                    (fid, eid))

    # Pass 3 — Phase B: derive typed relations from the current text (full rebuild). A person
    # is addressable by its canonical norm OR an alias (so a merged 'Em' still resolves). A
    # relation is skipped when the fact→subject link is user-suppressed (honours Phase A unlink).
    persons: dict[str, int] = {}
    for eid, e in ents:
        persons.setdefault(e.norm, eid)
        for a in e.aliases:
            persons.setdefault(a, eid)
    suppressed = {(r[0], r[1]) for r in db.execute(
        "SELECT memory_id, entity_id FROM fact_entities WHERE suppressed=1").fetchall()}
    known = set(persons)
    db.execute("DELETE FROM relations")
    now = time.time()
    for fid, content in facts:
        for subj, pred, obj, otype in extract_relations(content, known):
            src_id = persons.get(norm(subj))
            if src_id is None or (fid, src_id) in suppressed:
                continue
            dst_id = _self_entity_id(db) if otype == "self" else _upsert_typed_entity(db, obj, otype)
            db.execute(
                "INSERT OR IGNORE INTO relations(src_entity, predicate, dst_entity, memory_id, "
                "created_at) VALUES(?,?,?,?,?)", (src_id, pred, dst_id, fid, now))

    # GC — drop entities referenced by NEITHER a fact-link NOR a relation endpoint. NOT EXISTS
    # (never NOT IN) so a NULL can't silently wipe or spare the lot; src/dst are NOT NULL too.
    # This keeps place/org endpoints and the self node (relation-referenced) while removing a
    # person whose naming facts were all deleted. A merely-suppressed link keeps its entity.
    db.execute(
        "DELETE FROM entities WHERE NOT EXISTS "
        "(SELECT 1 FROM fact_entities fe WHERE fe.entity_id = entities.id) "
        "AND NOT EXISTS (SELECT 1 FROM relations r "
        "WHERE r.src_entity = entities.id OR r.dst_entity = entities.id)")
    db.commit()


def resolve_entity(db, term: str) -> int | None:
    """Entity id a query term refers to (by canonical name or alias), else None."""
    n = norm(term)
    row = db.execute("SELECT id FROM entities WHERE norm=? AND type='person'", (n,)).fetchone()
    if row:
        return row[0]
    row = db.execute("SELECT entity_id FROM entity_aliases WHERE alias_norm=?", (n,)).fetchone()
    return row[0] if row else None


def gather_entity_facts(db, entity_id: int) -> list[str]:
    """All (non-suppressed) fact contents connected to the entity, newest first."""
    rows = db.execute(
        "SELECT m.content FROM fact_entities fe JOIN memory m ON m.id = fe.memory_id "
        "WHERE fe.entity_id=? AND fe.suppressed=0 ORDER BY m.created_at DESC, m.id DESC",
        (entity_id,)).fetchall()
    return [r[0] for r in rows]


def gather_relations(db, entity_id: int) -> list[tuple[str, str, str]]:
    """Phase B 1-hop traversal: typed connections where this entity is the SUBJECT —
    list of (predicate, dst_name, dst_type)."""
    rows = db.execute(
        "SELECT r.predicate, d.name, d.type FROM relations r "
        "JOIN entities d ON d.id = r.dst_entity WHERE r.src_entity=? ORDER BY r.predicate",
        (entity_id,)).fetchall()
    return [(p, dn, dt) for p, dn, dt in rows]


def _role_noun(db, entity_id: int) -> str | None:
    """A family role noun (daughter/son/hija…) for the entity, read from its aliases, so a
    child-of/parent-of edge renders as 'is your daughter' not a bare 'is your child'."""
    for (a,) in db.execute(
            "SELECT alias_norm FROM entity_aliases WHERE entity_id=?", (entity_id,)).fetchall():
        for w in a.split():
            if w in _CHILD_WORDS or w in _PARENT_WORDS:
                return w
    return None


def render_connections(db, entity_id: int, name: str) -> list[str]:
    """The 1-hop typed connections as short guidance sentences for the entity render block
    ("Emma is your daughter", "Emma attends Oakwood Primary School"). Closed set only."""
    role = _role_noun(db, entity_id)
    out: list[str] = []
    for pred, dst_name, _dst_type in gather_relations(db, entity_id):
        if pred == "child-of":
            out.append(f"{name} is your {role or 'child'}")
        elif pred == "parent-of":
            out.append(f"{name} is your {role or 'parent'}")
        elif pred == "attends":
            out.append(f"{name} attends {dst_name}")
        elif pred == "lives-in":
            out.append(f"{name} lives in {dst_name}")
        elif pred == "works-at":
            out.append(f"{name} works at {dst_name}")
    return out


def entity_query_facts(db, query: str) -> tuple[str, list[str], list[str]] | None:
    """(canonical_name, connected_facts, connections) for an explicit entity query, or None
    to fall through to normal retrieval. None when: not an entity query / the person is
    unknown / no linked facts — so Vokter still answers via the usual gated hybrid path in
    every non-match case. `connections` is the Phase B typed 1-hop summary (may be empty);
    the name is the canonical entity name (so "qué sabes de mi hija" addresses "Emma")."""
    term = parse_entity_query(query)
    if not term:
        return None
    eid = resolve_entity(db, term)
    if eid is None:
        return None
    facts = gather_entity_facts(db, eid)
    if not facts:
        return None
    row = db.execute("SELECT name FROM entities WHERE id=?", (eid,)).fetchone()
    name = row[0] if row else term
    return name, facts, render_connections(db, eid, name)


def list_entities(db) -> list[dict]:
    """The user-visible 'People' layer: each REAL person + aliases + their linked facts (and
    which are suppressed) + the Phase B typed connections ("is your daughter", "attends X").
    Filtered to type='person' EXCLUDING the reserved self node — place/org nodes are internal
    relation endpoints, and 'you' is the user, neither is a person to show (per the UI
    decision). For the transparency view and corrections (unlink/merge)."""
    out = []
    for eid, name in db.execute(
            "SELECT id, name FROM entities WHERE type='person' AND norm != ? "
            "ORDER BY name COLLATE NOCASE", (SELF_NORM,)).fetchall():
        aliases = [r[0] for r in db.execute(
            "SELECT alias_norm FROM entity_aliases WHERE entity_id=?", (eid,)).fetchall()]
        facts = [{"id": r[0], "content": r[1], "suppressed": bool(r[2])} for r in db.execute(
            "SELECT m.id, m.content, fe.suppressed FROM fact_entities fe "
            "JOIN memory m ON m.id = fe.memory_id WHERE fe.entity_id=? "
            "ORDER BY m.created_at DESC, m.id DESC", (eid,)).fetchall()]
        out.append({"id": eid, "name": name, "type": "person", "aliases": aliases,
                    "facts": facts, "connections": render_connections(db, eid, name)})
    return out


def unlink_fact(db, entity_id: int, memory_id: int) -> bool:
    """User correction 'this fact isn't about them': mark the link suppressed so gather
    skips it AND reindex never re-adds it. Returns False if there was no such link."""
    cur = db.execute(
        "UPDATE fact_entities SET suppressed=1 WHERE entity_id=? AND memory_id=?",
        (entity_id, memory_id))
    db.commit()
    return cur.rowcount > 0


def merge_entities(db, from_id: int, into_id: int) -> bool:
    """Merge two surface forms of the same person into one node. The from-entity's name and
    aliases become aliases of into (so discovery resolves them there, surviving reindex);
    its links move over; then it's deleted and the index reconciled. Returns False on a bad
    id or a self-merge. (SPLITTING a wrongly-merged node is Phase C — it needs per-fact
    disambiguation; the manual workaround is unlink_fact on each misattributed fact.)"""
    if from_id == into_id:
        return False
    frm = db.execute("SELECT name FROM entities WHERE id=?", (from_id,)).fetchone()
    into = db.execute("SELECT id FROM entities WHERE id=?", (into_id,)).fetchone()
    if not frm or not into:
        return False
    db.execute("INSERT OR IGNORE INTO entity_aliases(entity_id, alias_norm) VALUES(?,?)",
               (into_id, norm(frm[0])))
    db.execute("UPDATE OR IGNORE entity_aliases SET entity_id=? WHERE entity_id=?",
               (into_id, from_id))
    db.execute("UPDATE OR IGNORE fact_entities SET entity_id=? WHERE entity_id=?",
               (into_id, from_id))
    db.execute("DELETE FROM entities WHERE id=?", (from_id,))
    db.commit()
    reindex(db)
    return True
