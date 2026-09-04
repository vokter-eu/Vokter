"""Phase 1 — personal memory (explicit, `told`).

Facts the user asks Vokter to remember, stored in the SAME encrypted DB (sqlcipher,
keychain-backed key). Never leaves the device; fully user-visible/editable/deletable.

Phase 1 scope: EXPLICIT saves only ("remember that ...") and the review window.
No chat injection yet (that is 1b), no learning-from-conversation (that is Phase 2),
no similarity retrieval (Phase 1 includes ALL facts, so no embeddings are computed).
"""
import json
import re
import time
from contextlib import closing

import numpy as np

from config import CORE_BUDGET_TOKENS, MEMORY_MIN_SCORE, MEMORY_TOP_K
from db import get_db
from embedding import pack_embedding, unpack_embedding
from engine import get_engine, ChatRequest
from fts import to_match_query
from rag import embed, rrf

# Explicit, predictable triggers — NOT fuzzy NLP. The user always knows when a
# save happens (Vokter confirms), and can see/delete it in the review window.
_ES_PREFIXES = (
    "recuérdame que ", "recuerdame que ", "recuérdame: ", "recuerdame: ",
    "recuerda que ", "apunta que ", "no olvides que ",
)
_EN_PREFIXES = (
    "remember that ", "remember: ", "note that ", "keep in mind that ",
)
_PREFIXES = _ES_PREFIXES + _EN_PREFIXES


def parse_remember(text: str) -> str | None:
    """If `text` is an explicit 'remember ...' command, return the fact (verbatim,
    original case), else None. Trailing punctuation is trimmed."""
    stripped = text.strip()
    low = stripped.lower()
    for p in _PREFIXES:
        if low.startswith(p):
            fact = stripped[len(p):].strip().rstrip(".!").strip()
            return fact or None
    return None


def trigger_lang(text: str) -> str:
    """'es' or 'en' — so Vokter confirms in the language you asked in."""
    low = text.strip().lower()
    return "es" if any(low.startswith(p) for p in _ES_PREFIXES) else "en"


# --- Direction A / A1: "core" (identity) facts — the ones ALWAYS injected -------------
# A deliberately NARROW allow-list of durable identity signals: own name, health/
# allergies, and family/relationships. Everything else (favourite colour, team, one-off
# likes) is core=0 and reaches the prompt ONLY when the message is relevant — that is
# exactly what retires the old dump-all (see the eval's "colours on an unrelated question"
# check). Keep this TIGHT: every entry widens what is unconditionally in the prompt. A
# false negative is cheap (the fact is still retrieved when relevant); a false positive
# just over-includes. Bilingual (en+es) to match the extractor, which writes in the user's
# language. The `add()` extractor gives us no category tags, so we classify from the text.
_CORE_STEMS = (
    # health / allergies
    "allerg", "alérg", "alergi", "intoleran", "celiac", "celíac", "diabet",
    "asthma", "asma", "epilep", "hipertens", "hypertens",
    # own name
    "name is", "named", "nombre", "se llama", "llamad", "me llamo",
    # family / relationships (unambiguous stems)
    "husband", "wife", "spouse", "daughter", "brother", "sister", "mother",
    "father", "parent", "sibling", "married", "fianc", "girlfriend", "boyfriend",
    "grandmother", "grandfather", "esposa", "esposo", "marido", "hermano",
    "hermana", "madre", "padre", "abuel", "sobrin", "pareja", "casad",
    "prometid", "family", "familia",
)
# Short, ambiguous relationship words matched only on a WHOLE-WORD boundary, so "son"
# never fires inside "person"/"reason" and "hija" never inside a longer token.
_CORE_WORDS = re.compile(
    r"\b(son|sons|dad|mom|mum|kid|kids|child|children|hijo|hija|hijos|hijas|"
    r"mamá|papá|mujer|novia|novio|tía|tío|primo|prima|nieto|nieta)\b",
    re.IGNORECASE | re.UNICODE,
)


def _is_core(content: str) -> bool:
    """True when a fact is an identity fact (name / health / family) → always injected."""
    low = content.lower()
    if any(s in low for s in _CORE_STEMS):
        return True
    return bool(_CORE_WORDS.search(content))


# --- Core-budget cap: health is the ONE always-on category EXEMPT from the budget --------
# Health/allergy facts are safety-critical and must NEVER be demoted, so they are the only
# always-on facts the token budget can't touch. That makes this the UNCAPPED path, so its
# PRECISION matters more than its recall: an over-included health fact sits in the prompt
# forever, uncleanable. Hence first-person, condition-shaped phrasing only — "I'm allergic
# to X" / "tengo diabetes", NOT a bare "allerg"/"diabet" appearing anywhere ("diabetic-
# friendly recipe" must NOT qualify). A genuine health fact this misses is cheap: it is
# still `core` via _is_core and still retrieved on relevance — it just isn't budget-exempt.
_HEALTH_RE = re.compile(
    r"\bi(?:'m| am) (?:allergic|asthmatic|diabetic|coeliac|celiac|epileptic|hypertensive|"
    r"lactose[- ]intolerant|intolerant to)\b"
    r"|\bi have (?:asthma|diabetes|coeliac|celiac|epilepsy|hypertension|high blood pressure|"
    r"an? (?:allergy|intolerance)|allergies)\b"
    r"|\bsoy (?:alérgic|asmátic|diabétic|celíac|hipertens|epilépt|intoleran)"
    r"|\btengo (?:asma|diabetes|epilepsia|hipertensión|una? alergia|alergias|"
    r"celiaqu|intolerancia)",
    re.IGNORECASE | re.UNICODE,
)


def _is_health(content: str) -> bool:
    """True for a first-person health/allergy fact — the budget-EXEMPT, never-demoted class."""
    return bool(_HEALTH_RE.search(content))


def _est_tokens(text: str) -> int:
    """Rough token estimate (~4 chars/token) — matches the budget-sizing measurement. Only
    used to bound the always-on block; approximate is fine (the budget has ample headroom)."""
    return max(1, len(text) // 4)


def enforce_core_budget() -> list[dict]:
    """Bound the ALWAYS-ON block: if the non-health, non-pinned core pool exceeds
    CORE_BUDGET_TOKENS, demote the OLDEST identity facts (created_at) to core=0 until it fits.
    Demotion is core=1 → core=0: the fact is NOT deleted and stays retrievable via Direction A
    when a message is relevant — it just stops being unconditionally injected. Health/allergy
    and user-pinned facts are EXEMPT (never counted, never demoted). Idempotent. Returns the
    facts demoted THIS call (for the UI's transparent 'moved from always-on' note)."""
    demoted: list[dict] = []
    with closing(get_db()) as db:
        # newest first → keep the most recent within budget, demote the oldest overflow
        rows = db.execute(
            "SELECT id, content FROM memory "
            "WHERE core=1 AND health=0 AND pinned=0 "
            "ORDER BY created_at DESC, id DESC"
        ).fetchall()
        used = 0
        for mid, content in rows:
            t = _est_tokens(content)
            if used + t <= CORE_BUDGET_TOKENS:
                used += t
            else:
                db.execute("UPDATE memory SET core=0 WHERE id=?", (mid,))
                demoted.append({"id": mid, "content": content})
        if demoted:
            db.commit()
    return demoted


def pin(mem_id: int) -> bool:
    """User pins a fact as always-on. Sets pinned=1 AND core=1, so it is unconditionally
    injected and EXEMPT from the budget — the escape hatch for anything the user wants kept
    always-on regardless of age. Returns False if the id doesn't exist."""
    with closing(get_db()) as db:
        cur = db.execute("UPDATE memory SET pinned=1, core=1 WHERE id=?", (mem_id,))
        db.commit()
        return cur.rowcount > 0


def unpin(mem_id: int) -> bool:
    """Un-pin: the fact re-competes in the budget. core reverts to its classified value
    (identity/health → still core, else demoted to relevance-only), then the budget is
    re-enforced. Returns False if the id doesn't exist."""
    with closing(get_db()) as db:
        row = db.execute("SELECT content, health FROM memory WHERE id=?", (mem_id,)).fetchone()
        if not row:
            return False
        content, health = row
        core = 1 if (_is_core(content) or health) else 0
        db.execute("UPDATE memory SET pinned=0, core=? WHERE id=?", (core, mem_id))
        db.commit()
    enforce_core_budget()
    return True


# Shared wording for the memory system-prompt block, so the always-on dump (system_block,
# kept for the eval BEFORE baseline) and the query-aware relevant_block render identically.
def _render_block(lines: str) -> str:
    return (
        "\n\nThings the user has asked you to remember about them "
        "(personal, private to this chat). Refer to them when they are relevant "
        "to the user's message; do not recite or list them unprompted. These are "
        "things the USER TOLD YOU, not documents — never attribute them to a named "
        "document, file, or source; only cite a document when its text is actually "
        "given to you in the message:\n"
        f"{lines}"
    )


def add(content: str, source: str = "told", confidence: float = 1.0) -> dict:
    """Store a fact. Returns the created row. `content` is stored VERBATIM so the
    user sees exactly what was saved. `confidence` < 1 marks a Phase-2 'learned'
    fact for scrutiny in the review window — it is a display marker, NEVER a gate
    on injection (a stored fact was confirmed by the user, so it counts fully)."""
    content = content.strip()
    now = time.time()
    health = 1 if _is_health(content) else 0
    core = 1 if (_is_core(content) or health) else 0   # health is always core (always-on)
    with closing(get_db()) as db:
        cur = db.execute(
            "INSERT INTO memory(content, source, created_at, core, health, confidence) "
            "VALUES(?,?,?,?,?,?)",
            (content, source, now, core, health, confidence),
        )
        db.commit()
        new_id = cur.lastrowid
    # A new core fact may push the always-on pool over budget → demote the oldest (never this
    # newest one, never health/pinned). Cheap + idempotent.
    demoted = enforce_core_budget()
    # The AFTER-INSERT trigger indexes the fact in memory_fts immediately (keyword-
    # retrievable at once). `embedding` is left NULL; embed_pending() computes the vector
    # in the background — scheduled fire-and-forget by the async caller (memory_add / ask).
    return {"id": new_id, "content": content, "source": source, "created_at": now,
            "core": core, "health": health, "pinned": 0, "confidence": confidence,
            "demoted": demoted}


def list_all() -> list[dict]:
    """Every fact Vokter holds, newest first — the whole of what it knows about you."""
    with closing(get_db()) as db:
        rows = db.execute(
            "SELECT id, content, source, created_at, confidence, core, health, pinned FROM memory"
            " ORDER BY created_at DESC, id DESC"
        ).fetchall()
    return [{"id": r[0], "content": r[1], "source": r[2], "created_at": r[3],
             "confidence": r[4], "core": r[5], "health": r[6], "pinned": r[7]} for r in rows]


def edit(mem_id: int, content: str) -> bool:
    """Correct a fact. Returns False if the id doesn't exist. Re-classifies core (the new
    text may change identity status) and NULLs the embedding so embed_pending re-embeds the
    corrected text; the AFTER-UPDATE trigger re-syncs memory_fts to the new content."""
    content = content.strip()
    health = 1 if _is_health(content) else 0
    core = 1 if (_is_core(content) or health) else 0
    with closing(get_db()) as db:
        # pinned is preserved (a user pin survives an edit); health/core re-derive from text.
        cur = db.execute("UPDATE memory SET content=?, core=?, health=?, embedding=NULL WHERE id=?",
                         (content, core, health, mem_id))
        db.commit()
        ok = cur.rowcount > 0
    if ok:
        enforce_core_budget()
    return ok


def delete(mem_id: int) -> bool:
    """Forget one fact. Returns False if the id doesn't exist."""
    with closing(get_db()) as db:
        cur = db.execute("DELETE FROM memory WHERE id=?", (mem_id,))
        db.commit()
        return cur.rowcount > 0


def system_block() -> str:
    """Phase 1b — the memory as a SYSTEM-prompt section, ALWAYS the whole of it
    (Phase 1 has no similarity retrieval; every fact is included). Returns "" when
    there are no facts, so a memory-less Vokter builds a system prompt BYTE-IDENTICAL
    to Phase 0 — memory can only ever add, never alter the baseline.

    This block reaches ONLY the local human session — never an agent-to-agent / A2A /
    Nostr / MCP caller. That invariant is not promised here, it is ENFORCED at the one
    point that can keep it: chat.build_chat_system appends this block only when
    chat.is_local_human_session(request) is true (deny-by-default; internal callers
    don't hold the per-launch human-session token). See
    docs/threat-model-prompt-injection.md §7-8. (A previous comment here *asserted*
    "never in A2A" without enforcing it — a promise with no test is worse than none.)

    Wording is deliberately gentle: 'refer to these when relevant, don't recite them
    unprompted'. A forceful 'always use these facts' makes a small local model blurt
    the list into a plain greeting and breaks natural conversation.
    """
    facts = list_all()
    if not facts:
        return ""
    lines = "\n".join(f"- {f['content']}" for f in facts)
    return _render_block(lines)


def has_facts() -> bool:
    """Cheap existence check — does the store hold ANY fact? Used for the fail-closed
    `memory_withheld` signal without materialising or ranking the whole memory."""
    with closing(get_db()) as db:
        return db.execute("SELECT EXISTS(SELECT 1 FROM memory)").fetchone()[0] == 1


async def _rank_relevant(query: str, noncore: list[tuple]) -> list[int]:
    """Rank NON-core facts against `query` (hybrid vector + FTS, RRF-fused) and return up
    to MEMORY_TOP_K ids that clear the semantic floor OR are a genuine keyword hit. Core
    facts are handled separately (always injected), so they are excluded here."""
    if not noncore:
        return []
    ncids = {cid for cid, _content, _emb in noncore}

    # vector arm — cosine of the query to each embedded non-core fact
    vec_ranked: list[int] = []
    sim_by_id: dict[int, float] = {}
    qn = 0.0
    try:
        q = np.asarray(await embed(query), dtype=np.float32)
        qn = float(np.linalg.norm(q))
    except Exception:
        qn = 0.0                      # engine down → lean on the keyword arm alone
    if qn:
        dim = q.shape[0]
        ids, vecs = [], []
        for cid, _content, emb in noncore:
            v = unpack_embedding(emb)
            if v is not None and v.shape[0] == dim:
                ids.append(cid)
                vecs.append(v)
        if vecs:
            matrix = np.vstack(vecs)
            sims = (matrix @ q) / (np.linalg.norm(matrix, axis=1) * qn + 1e-12)
            order = np.argsort(-sims)[: MEMORY_TOP_K * 4]
            vec_ranked = [ids[i] for i in order]
            sim_by_id = {ids[i]: float(sims[i]) for i in order}

    # keyword arm — FTS5 over the sanitised query, restricted to non-core rows
    kw_ranked: list[int] = []
    match = to_match_query(query)
    if match:
        with closing(get_db()) as db:
            try:
                kw = db.execute(
                    "SELECT rowid FROM memory_fts WHERE memory_fts MATCH ? "
                    "ORDER BY rank LIMIT ?", (match, MEMORY_TOP_K * 4)
                ).fetchall()
                kw_ranked = [r[0] for r in kw if r[0] in ncids]
            except Exception:
                kw_ranked = []

    scores = rrf(vec_ranked, kw_ranked)
    if not scores:
        return []
    kw_set = set(kw_ranked)
    eligible = [cid for cid in scores
                if cid in kw_set or sim_by_id.get(cid, 0.0) >= MEMORY_MIN_SCORE]
    eligible.sort(key=lambda cid: scores[cid], reverse=True)
    return eligible[:MEMORY_TOP_K]


async def relevant_block(query: str) -> str:
    """Direction A / A1 — the query-aware replacement for system_block()'s dump-all.

    Injects CORE (identity) facts always + up to MEMORY_TOP_K facts RELEVANT to `query`
    (hybrid vector+FTS, RRF-fused, gated by the semantic floor OR a keyword hit). Bounded —
    NEVER the whole store. Same "" -when-empty and wording contract as system_block, and the
    SAME P2 guarantee: chat.build_chat_system calls it only for the local human session, so
    it never reaches an A2A/MCP/peer caller. An off-topic message pulls in no non-core fact,
    so a bare greeting still injects nothing beyond the (usually few) core identity facts."""
    with closing(get_db()) as db:
        rows = db.execute("SELECT id, content, core, embedding FROM memory").fetchall()
    if not rows:
        return ""
    content_by_id = {cid: content for cid, content, _core, _emb in rows}
    core_ids = [cid for cid, _content, core, _emb in rows if core]
    noncore = [(cid, content, emb) for cid, content, core, emb in rows if not core]

    picked = await _rank_relevant(query, noncore)
    ordered = core_ids + picked                      # core first, then relevant preferences
    if not ordered:
        return ""
    lines = "\n".join(f"- {content_by_id[cid]}" for cid in ordered)
    return _render_block(lines)


# --- Phase 2 (learn from conversation) — DETECTION ONLY -----------------------
# extract_candidate() PROPOSES facts it notices in chat; it NEVER stores. Nothing
# enters the memory table until the user clicks Guardar (that path is add()). So
# "never remember without the user's OK" holds BY CONSTRUCTION: a candidate lives
# only in the response/frontend, never on disk, and so can never reach
# system_block() (1b). Deterministic (temperature=0) and JSON-only; on any doubt
# it returns [] — proposing nothing beats proposing noise.
#
# Fabricated facts are as trust-eroding as 1b's false citations. llama3.2:3b, on a
# message that closely matches an example, echoes the OTHER examples' content as if
# the user had said it (measured: 6/6 runs invented "Vive en Madrid" from a Madrid
# example — it is NOT deterministic at temp 0 on CPU). Two structural defences,
# both measured over N runs (fiction examples + post-filter → 0 bleed, 0 trap
# noise, recall unchanged):
#   1) example CONTENT is made-up (Zolbria, Klemtar…) so an echo can never be a
#      real user fact — and cannot collide with a legitimate answer;
#   2) _drop_example_echoes() deletes any output carrying an example-only token,
#      turning "0/6 this run" into a HARD zero that does not rely on model luck.

_EXTRACT_SYSTEM = (
    "You watch a chat and note DURABLE personal facts the user reveals about "
    "THEMSELVES — things worth remembering for months. Read the conversation but "
    "focus on the user's LAST message; earlier lines are only context to resolve "
    "references (a name, 'she', 'there').\n"
    "Return STRICT JSON: {\"facts\": [\"...\", ...]} — short third-person facts, "
    "or {\"facts\": []} when the last message reveals none.\n"
    "DO note: where they live, their job or studies, health conditions and "
    "allergies, family and relationships, lasting likes/dislikes, important "
    "recurring dates.\n"
    "Do NOT note: greetings, questions, how they feel right now (tired, hungry, "
    "bored), one-off plans or errands, opinions about the world/weather/sports, or "
    "anything that is not a lasting fact about this person.\n"
    "Write each fact in the SAME LANGUAGE as the user's last message. Keep "
    "relationships explicit ('His daughter Nomi is 6', not 'Nomi is 6'). Split "
    "distinct facts into separate items.\n"
    "The examples below use MADE-UP names to show FORMAT only — never copy their "
    "content into a real answer.\n"
    "Examples:\n"
    "  U: me mudé a Zolbria y trabajo en Klemtar -> {\"facts\":[\"Vive en Zolbria\","
    "\"Trabaja en Klemtar\"]}\n"
    "  U: I'm allergic to quixel -> {\"facts\":[\"Allergic to quixel\"]}\n"
    "  U: estoy cansado hoy -> {\"facts\":[]}\n"
    "  U: ¿qué me recomiendas para cenar? -> {\"facts\":[]}\n"
    "  U: creo que mañana lloverá -> {\"facts\":[]}\n"
    "  [context: tengo un hijo] U: se llama Vexnol y tiene 6 -> "
    "{\"facts\":[\"Su hijo Vexnol tiene 6 años\"]}"
)

# Made-up tokens that appear ONLY in the examples above. A real user fact never
# contains them, so dropping any fact that does can never remove a genuine fact —
# it only catches the model regurgitating an example.
_EXAMPLE_ECHO_TOKENS = ("zolbria", "klemtar", "quixel", "vexnol")


def _drop_example_echoes(facts: list[str]) -> list[str]:
    return [f for f in facts
            if not any(tok in f.lower() for tok in _EXAMPLE_ECHO_TOKENS)]


async def extract_candidate(message: str, context: list[str] | None = None,
                            model: str | None = None) -> list[str]:
    """Notice durable personal facts in the user's latest `message` (optionally
    given recent `context` turns to resolve references). Returns a list of proposed
    fact strings — possibly empty. PROPOSES only; storing is a separate, explicit
    user action. Never raises for a bad model reply: returns [] instead."""
    msgs: list[dict] = [{"role": "system", "content": _EXTRACT_SYSTEM}]
    for turn in (context or []):
        msgs.append({"role": "user", "content": turn})
    msgs.append({"role": "user", "content": message})
    try:
        raw = await get_engine().chat(ChatRequest(
            messages=msgs, model=model, json_mode=True,
            temperature=0, context_size=8192, timeout=60,
        ))
        facts = json.loads(raw).get("facts", [])
    except Exception:
        return []   # a garbled reply proposes nothing — never noise, never a crash
    if not isinstance(facts, list):
        return []
    # Keep only non-empty strings, trimmed; then drop any example echo (see above).
    facts = [f.strip() for f in facts if isinstance(f, str) and f.strip()]
    return _drop_example_echoes(facts)


async def embed_pending(batch: int = 128) -> int:
    """Direction A backfill — embed facts that have no vector yet (embedding IS NULL)
    and store it as a packed float32 BLOB. Idempotent: run once at boot AND fire-and-
    forget after each add(), so a fact added mid-session is embedded within seconds
    rather than at the next restart. Needs Ollama; on the first embed failure it stops
    quietly (Ollama down / model not pulled) — the fact stays keyword-retrievable via
    FTS meanwhile, so retrieval degrades cleanly instead of breaking. Returns the count
    embedded this pass. The UPDATE re-fires the FTS sync trigger harmlessly (same text)."""
    with closing(get_db()) as db:
        rows = db.execute(
            "SELECT id, content FROM memory WHERE embedding IS NULL ORDER BY id LIMIT ?",
            (batch,),
        ).fetchall()
    done = 0
    for mid, content in rows:
        try:
            vec = await embed(content)
        except Exception:
            break   # engine unavailable → leave NULL, retry on the next pass/boot
        with closing(get_db()) as db:
            db.execute("UPDATE memory SET embedding=? WHERE id=?",
                       (pack_embedding(vec), mid))
            db.commit()
        done += 1
    return done


def forget_all() -> int:
    """'Forget everything about me' — a REAL delete, then VACUUM to reclaim and
    overwrite the freed pages (not a hidden flag). Returns how many were removed."""
    with closing(get_db()) as db:
        n = db.execute("SELECT COUNT(*) FROM memory").fetchone()[0]
        db.execute("DELETE FROM memory")
        db.commit()
        db.execute("VACUUM")   # scrub freed pages so erased facts don't linger on disk
    return n
