"""
Local TTS via Piper (MIT) — the second engine, for the languages Kokoro can't speak
(de/nl/ca). One downloadable voice pack per language (a .onnx model + its .onnx.json config),
mirrored on Vokter's OWN host (voice/fetch.py); Piper runs them on the same onnxruntime the
rest of the voice stack already ships. No PyTorch, no runtime call to any third-party host.

Pure engine: the provider registry (voice/registry.py) owns POST /api/voice/speak and routes a
reply language to Kokoro or to Piper. This module only knows how to load a voice id and turn
text into WAV bytes. It NEVER downloads — a missing pack yields voice_not_ready upstream so the
chat keeps working and the UI can offer the download (C′).
"""
import io
import threading
import wave
from collections import OrderedDict

from fastapi import HTTPException

from config import VOICE_MODELS_DIR
import os

# LRU cache of loaded Piper voices, keyed by voice id. Capped so we never hold more than a
# couple of ~60MB models in RAM at once: the user speaks one language at a time, so 2 is plenty
# and switching languages evicts the oldest, letting onnxruntime free the session.
_VOICE_CACHE_MAX = 2
_voices: "OrderedDict[str, object]" = OrderedDict()
_voice_lock = threading.Lock()      # load/evict must be atomic: speak() runs in the threadpool


def piper_dir() -> str:
    return os.path.join(VOICE_MODELS_DIR, "piper")


def paths(voice_id: str) -> tuple[str, str]:
    d = piper_dir()
    return (os.path.join(d, f"{voice_id}.onnx"),
            os.path.join(d, f"{voice_id}.onnx.json"))


def present(voice_id: str) -> bool:
    """True iff both the model and its config are on disk. Callers check this BEFORE
    synthesize() — a missing pack is voice_not_ready (fetching is voice/fetch.py), never a
    blocking download here."""
    model_path, config_path = paths(voice_id)
    return os.path.exists(model_path) and os.path.exists(config_path)


def _get_voice(voice_id: str):
    """Return a loaded PiperVoice for voice_id via an LRU cache (max 2). The caller MUST have
    checked present() first — this loads from disk and never downloads."""
    with _voice_lock:
        cached = _voices.get(voice_id)
        if cached is not None:
            _voices.move_to_end(voice_id)          # mark most-recently-used
            return cached
        try:
            from piper.voice import PiperVoice
        except ImportError:
            raise HTTPException(503, "piper-tts not installed — add it to requirements.txt")
        model_path, config_path = paths(voice_id)
        print(f"voice: loading Piper voice {voice_id}…")
        voice = PiperVoice.load(model_path, config_path=config_path, use_cuda=False)
        _voices[voice_id] = voice
        _voices.move_to_end(voice_id)
        while len(_voices) > _VOICE_CACHE_MAX:
            _voices.popitem(last=False)             # evict least-recently-used → frees RAM
        return voice


def synthesize(voice_id: str, text: str) -> bytes:
    """Render text → WAV bytes with the given Piper voice. Caller guarantees present(voice_id)."""
    voice = _get_voice(voice_id)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav_file:
        voice.synthesize_wav(text, wav_file)       # piper-tts 1.4.x API
    return buf.getvalue()
