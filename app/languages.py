"""
Single source of truth for the v1 language selector (voice front — docs/Vokter_Voz.md §8).

One row per supported language: the Piper voice for TTS, the Whisper language code for STT,
and the human name used in the chat system prompt. The three capas (chat, STT, TTS) all read
THIS table, so they can never drift apart. Adding a language = one row here (plus shipping /
fetching its Piper voice). v1 scope = these 7; catalan/polish are parked for the next model
swap (the 3B breaks them), greek/basque/galician are out.
"""

# code -> {piper voice id, whisper language code, name for the chat prompt}
V1_LANGUAGES: dict[str, dict[str, str]] = {
    "en": {"piper": "en_US-lessac-medium",   "whisper": "en", "chat": "English"},
    "es": {"piper": "es_ES-davefx-medium",   "whisper": "es", "chat": "Spanish"},
    "fr": {"piper": "fr_FR-siwis-medium",    "whisper": "fr", "chat": "French"},
    "de": {"piper": "de_DE-thorsten-medium", "whisper": "de", "chat": "German"},
    "it": {"piper": "it_IT-paola-medium",    "whisper": "it", "chat": "Italian"},
    # pt: European Portuguese on purpose (voice + prompt) — the 3B mixes PT-PT/PT-BR (§5).
    "pt": {"piper": "pt_PT-tugão-medium",    "whisper": "pt", "chat": "European Portuguese (Portugal)"},
    "nl": {"piper": "nl_NL-ronnie-medium",   "whisper": "nl", "chat": "Dutch"},
}

# When the selector is "auto" we do NOT detect the text's language in v1 (design §8.2 — no
# new dependency): the voice falls back to this language. English is the safe universal default.
DEFAULT_VOICE_LANG = "en"


def voice_for(language: str) -> str:
    """Piper voice id for a selector value. 'auto' or anything unknown → the default voice."""
    entry = V1_LANGUAGES.get(language) or V1_LANGUAGES[DEFAULT_VOICE_LANG]
    return entry["piper"]


def whisper_lang(language: str) -> str | None:
    """Whisper language code for a concrete selector value, or None for 'auto'/unknown so
    Whisper auto-detects (today's behaviour)."""
    entry = V1_LANGUAGES.get(language)
    return entry["whisper"] if entry else None


def chat_name(language: str) -> str | None:
    """Human language name for the chat prompt, or None for 'auto'/unknown (caller decides
    the fallback — e.g. keep backward-compat for a parked code)."""
    entry = V1_LANGUAGES.get(language)
    return entry["chat"] if entry else None
