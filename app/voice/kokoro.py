"""
Local TTS via Kokoro-82M (Apache-2.0), run on CPU through kokoro-onnx (onnxruntime —
the same runtime the STT stack already ships; NO PyTorch).

Pure engine: the provider registry (voice/registry.py) owns POST /api/voice/speak and decides
which engine speaks a given reply language. ONE Kokoro model covers en/es/fr/it/pt; a language
Kokoro cannot speak (de/nl/ca) is handled by the Piper engine instead. This module only loads
the model and turns (language, text) into WAV bytes; it NEVER downloads (fetching is
voice/fetch.py) — _present() lets callers answer voice_not_ready when the model is absent.
"""
import io
import os
import threading
import wave

import numpy as np
from fastapi import HTTPException

from config import VOICE_MODELS_DIR

# Reply language -> (Kokoro voice id, Kokoro phonemizer lang code). Only the languages
# Kokoro v1.0 can actually speak. Kokoro's Portuguese is Brazilian (pt-br) — the only pt it ships.
_VOICES: dict[str, tuple[str, str]] = {
    "en": ("af_heart", "en-us"),
    "es": ("ef_dora",  "es"),
    "fr": ("ff_siwis", "fr-fr"),
    "it": ("if_sara",  "it"),
    "pt": ("pf_dora",  "pt-br"),
}

# The languages this engine speaks (the registry routes anything else to Piper).
KOKORO_LANGS = frozenset(_VOICES)


def _kokoro_dir() -> str:
    return os.path.join(VOICE_MODELS_DIR, "kokoro")


def _model_paths() -> tuple[str, str]:
    d = _kokoro_dir()
    return os.path.join(d, "kokoro-v1.0.onnx"), os.path.join(d, "voices-v1.0.bin")


def _present() -> bool:
    m, v = _model_paths()
    return os.path.exists(m) and os.path.exists(v)


_kokoro = None
_lock = threading.Lock()      # load is one-time; synthesize() runs in FastAPI's threadpool


def _get_kokoro():
    global _kokoro
    if _kokoro is not None:
        return _kokoro
    with _lock:
        if _kokoro is None:
            try:
                from kokoro_onnx import Kokoro
            except ImportError:
                raise HTTPException(503, "kokoro-onnx not installed — add it to requirements.txt")
            m, v = _model_paths()
            print("voice: loading Kokoro TTS…")
            _kokoro = Kokoro(m, v)
            print("voice: Kokoro ready.")
    return _kokoro


def synthesize(lang: str, text: str) -> bytes:
    """Render text → WAV bytes (24 kHz mono) in `lang`. Caller guarantees lang in KOKORO_LANGS
    and _present()."""
    voice, klang = _VOICES[lang]
    samples, sr = _get_kokoro().create(text, voice=voice, lang=klang)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav_file:      # float32 [-1,1] → 16-bit PCM WAV
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sr)
        wav_file.writeframes((np.clip(samples, -1.0, 1.0) * 32767.0).astype("<i2").tobytes())
    return buf.getvalue()
