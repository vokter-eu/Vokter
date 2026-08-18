"""
Single source of truth for the v1 language selector (voice front — docs/Vokter_Voz.md §8).

One row per language: the Whisper language code for STT and the human name used in the chat
system prompt. Both capas (chat, STT) read THIS table so they can never drift apart. TTS is no
longer here: Kokoro (see voice/kokoro.py) owns its own voice map, since it does NOT cover the
same set (Kokoro speaks en/es/fr/it/pt; it has no de/nl/ca — those keep STT + chat but have no
local voice for now). Adding a language for STT/chat = one row here.
"""

# code -> {whisper language code, name for the chat prompt}
V1_LANGUAGES: dict[str, dict[str, str]] = {
    "en": {"whisper": "en", "chat": "English"},
    "es": {"whisper": "es", "chat": "Spanish"},
    "fr": {"whisper": "fr", "chat": "French"},
    "de": {"whisper": "de", "chat": "German"},
    "it": {"whisper": "it", "chat": "Italian"},
    # pt: European Portuguese in the chat prompt on purpose (the 3B mixes PT-PT/PT-BR §5);
    # note Kokoro's pt VOICE is Brazilian — the only pt it ships.
    "pt": {"whisper": "pt", "chat": "European Portuguese (Portugal)"},
    "nl": {"whisper": "nl", "chat": "Dutch"},
}


def whisper_lang(language: str) -> str | None:
    """Whisper language code for a concrete selector value, or None for 'auto'/unknown so
    Whisper auto-detects (today's behaviour; auto-detect also covers Catalan etc.)."""
    entry = V1_LANGUAGES.get(language)
    return entry["whisper"] if entry else None


def chat_name(language: str) -> str | None:
    """Human language name for the chat prompt, or None for 'auto'/unknown (caller decides
    the fallback — e.g. keep backward-compat for a parked code)."""
    entry = V1_LANGUAGES.get(language)
    return entry["chat"] if entry else None
