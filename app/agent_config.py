"""
Agent personalisation settings.

All values are stored in the `agent_config` SQLite table as key-value pairs.
Reads always merge stored values with DEFAULTS so new keys are backward-compatible.
"""
from contextlib import closing

from db import get_db

DEFAULTS: dict[str, str] = {
    "agent_name":  "Vokter",
    "tone":        "neutral",         # formal | neutral | friendly
    "mode":        "conversational",  # productive | conversational
    "language":    "auto",            # auto | en | es | de | fr | it | pt | nl | ...
    "chat_model":  "",                # "" = use VOKTER_CHAT_MODEL env var
    "embed_model": "",                # "" = use VOKTER_EMBED_MODEL env var
    "max_history": "20",
    "rag_chunks":  "4",
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
        f"You are {name}, the user's personal AI guardian. "
        "Answer using ONLY the provided context from their documents. "
        "If the answer is not in the context, say so honestly — never make things up."
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
        parts.append(f"Always respond in {lang}.")

    return " ".join(parts)
