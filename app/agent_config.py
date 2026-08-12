"""
Agent personalisation settings.

All values are stored in the `agent_config` SQLite table as key-value pairs.
Reads always merge stored values with DEFAULTS so new keys are backward-compatible.
"""
from contextlib import closing

from db import get_db
from languages import chat_name

DEFAULTS: dict[str, str] = {
    "agent_name":  "Vokter",
    "tone":        "neutral",         # formal | neutral | friendly
    "mode":        "conversational",  # productive | conversational
    "language":    "auto",            # auto | en | es | de | fr | it | pt | nl | ...
    "chat_model":  "",                # "" = use VOKTER_CHAT_MODEL env var
    "embed_model": "",                # "" = use VOKTER_EMBED_MODEL env var
    "max_history": "20",
    "rag_chunks":  "4",
    "rag_min_score": "0.57",          # cosine floor to treat a chunk as RELEVANT.
                                      # Measured (nomic-embed-text): greetings/off-topic
                                      # peak ~0.55, thematic-but-paraphrased questions
                                      # floor ~0.59 → 0.57 keeps paraphrased matches
                                      # (protect RAG) while dropping chit-chat. Tunable.
    "onboarded":   "0",               # "1" once the first-run welcome wizard is done
}


def get_config() -> dict[str, str]:
    with closing(get_db()) as db:
        rows = db.execute("SELECT key, value FROM agent_config").fetchall()
    stored = {r[0]: r[1] for r in rows}
    return {k: stored.get(k, v) for k, v in DEFAULTS.items()}


def set_config(updates: dict[str, str]) -> None:
    with closing(get_db()) as db:
        for k, v in updates.items():
            db.execute(
                "INSERT INTO agent_config(key, value) VALUES(?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (k, v),
            )
        db.commit()


def build_system_prompt(cfg: dict[str, str]) -> str:
    name = cfg.get("agent_name", "Vokter")
    tone = cfg.get("tone", "neutral")
    mode = cfg.get("mode", "conversational")
    lang = cfg.get("language", "auto")

    parts = [
        f"You are {name}, the user's personal AI guardian — a warm, helpful private "
        "assistant. Chat naturally and be good company. When context from the user's "
        "documents is provided, ground your answer in it and mention which document. "
        "When no document context is given, just answer conversationally and helpfully "
        "— but if you don't know or aren't sure, say so honestly instead of inventing. "
        "Never fabricate details about the user's documents or personal facts."
    ]
    if tone == "formal":
        parts.append("Use formal, professional language.")
    elif tone == "friendly":
        parts.append("Be warm, approachable, and encouraging.")
    if mode == "productive":
        parts.append("Be concise and direct. Avoid unnecessary elaboration.")
    elif mode == "conversational":
        parts.append("Feel free to elaborate when it adds clarity.")
    if lang != "auto":
        # Use the language's human name (and the European-Portuguese nuance) from the shared
        # table; fall back to the raw code for any parked/unknown value (backward-compat).
        parts.append(f"Always respond in {chat_name(lang) or lang}.")
    else:
        # Default: mirror the user, like a bilingual person. Anchor on the
        # QUESTION's language, not the documents' — a Spanish question about an
        # English document must be answered in Spanish (citing the English source).
        parts.append(
            "Always respond in the same language as the user's question, "
            "even when the documents are written in another language."
        )

    return " ".join(parts)
