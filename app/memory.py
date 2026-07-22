"""Phase 1 — personal memory (explicit, `told`).

Facts the user asks Vokter to remember, stored in the SAME encrypted DB (sqlcipher,
keychain-backed key). Never leaves the device; fully user-visible/editable/deletable.

Phase 1 scope: EXPLICIT saves only ("remember that ...") and the review window.
No chat injection yet (that is 1b), no learning-from-conversation (that is Phase 2),
no similarity retrieval (Phase 1 includes ALL facts, so no embeddings are computed).
"""
import json
import time
from contextlib import closing

from db import get_db
from engine import ENGINE, ChatRequest

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


def add(content: str, source: str = "told") -> dict:
    """Store a fact. Returns the created row. `content` is stored VERBATIM so the
    user sees exactly what was saved."""
    content = content.strip()
    now = time.time()
    with closing(get_db()) as db:
        cur = db.execute(
            "INSERT INTO memory(content, source, created_at, confidence) VALUES(?,?,?,?)",
            (content, source, now, 1.0),
        )
        db.commit()
        new_id = cur.lastrowid
    return {"id": new_id, "content": content, "source": source, "created_at": now}


def list_all() -> list[dict]:
    """Every fact Vokter holds, newest first — the whole of what it knows about you."""
    with closing(get_db()) as db:
        rows = db.execute(
            "SELECT id, content, source, created_at FROM memory ORDER BY created_at DESC, id DESC"
        ).fetchall()
    return [{"id": r[0], "content": r[1], "source": r[2], "created_at": r[3]} for r in rows]


def edit(mem_id: int, content: str) -> bool:
    """Correct a fact. Returns False if the id doesn't exist."""
    content = content.strip()
    with closing(get_db()) as db:
        cur = db.execute("UPDATE memory SET content=? WHERE id=?", (content, mem_id))
        db.commit()
        return cur.rowcount > 0


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

    Injected only into the HUMAN's chat (chat.py is the sole caller of
    build_system_prompt), never into agent-to-agent / A2A prompts — the user's
    personal facts must not leak to peers.

    Wording is deliberately gentle: 'refer to these when relevant, don't recite them
    unprompted'. A forceful 'always use these facts' makes a small local model blurt
    the list into a plain greeting and breaks natural conversation.
    """
    facts = list_all()
    if not facts:
        return ""
    lines = "\n".join(f"- {f['content']}" for f in facts)
    return (
        "\n\nThings the user has asked you to remember about them "
        "(personal, private to this chat). Refer to them when they are relevant "
        "to the user's message; do not recite or list them unprompted. These are "
        "things the USER TOLD YOU, not documents — never attribute them to a named "
        "document, file, or source; only cite a document when its text is actually "
        "given to you in the message:\n"
        f"{lines}"
    )


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
        raw = await ENGINE.chat(ChatRequest(
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


def forget_all() -> int:
    """'Forget everything about me' — a REAL delete, then VACUUM to reclaim and
    overwrite the freed pages (not a hidden flag). Returns how many were removed."""
    with closing(get_db()) as db:
        n = db.execute("SELECT COUNT(*) FROM memory").fetchone()[0]
        db.execute("DELETE FROM memory")
        db.commit()
        db.execute("VACUUM")   # scrub freed pages so erased facts don't linger on disk
    return n
