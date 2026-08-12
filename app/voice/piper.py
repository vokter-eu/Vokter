import io
import os
import threading
import wave
from collections import OrderedDict

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from agent_config import get_config
from config import VOICE_MODELS_DIR
from languages import voice_for

router = APIRouter()

# LRU cache of loaded Piper voices, keyed by voice id. Capped so we never hold more than a
# couple of ~60MB models in RAM at once (8GB target): the user speaks one language at a time,
# so 2 is plenty and switching languages evicts the oldest. Dropping the reference lets the
# onnxruntime session be freed.
_VOICE_CACHE_MAX = 2
_voices: "OrderedDict[str, object]" = OrderedDict()
# load/evict must be atomic: speak() runs in FastAPI's threadpool (many threads).
_voice_lock = threading.Lock()


class SpeakRequest(BaseModel):
    text: str


def _piper_dir() -> str:
    d = os.path.join(VOICE_MODELS_DIR, "piper")
    os.makedirs(d, exist_ok=True)
    return d


def _paths(voice_id: str) -> tuple[str, str]:
    d = _piper_dir()
    return (os.path.join(d, f"{voice_id}.onnx"),
            os.path.join(d, f"{voice_id}.onnx.json"))


def _present(voice_id: str) -> bool:
    """True iff both the model and its config are on disk. speak() NEVER downloads (C′:
    fetching is stage 3, onboarding + retry) — a missing voice yields voice_not_ready."""
    model_path, config_path = _paths(voice_id)
    return os.path.exists(model_path) and os.path.exists(config_path)


def _get_voice(voice_id: str):
    """Return a loaded PiperVoice for voice_id via an LRU cache (max 2). The caller MUST
    have checked _present() first — this loads from disk and never downloads."""
    with _voice_lock:
        cached = _voices.get(voice_id)
        if cached is not None:
            _voices.move_to_end(voice_id)          # mark most-recently-used
            return cached
        try:
            from piper.voice import PiperVoice
        except ImportError:
            raise HTTPException(503, "piper-tts not installed — add it to requirements.txt")
        model_path, config_path = _paths(voice_id)
        voice = PiperVoice.load(model_path, config_path=config_path, use_cuda=False)
        _voices[voice_id] = voice
        _voices.move_to_end(voice_id)
        while len(_voices) > _VOICE_CACHE_MAX:
            _voices.popitem(last=False)             # evict least-recently-used → frees RAM
        return voice


@router.post("/api/voice/speak")
def speak(req: SpeakRequest):
    # sync on purpose: FastAPI runs it in the threadpool, so CPU-bound synthesis cannot
    # freeze the event loop.
    if not req.text.strip():
        raise HTTPException(400, "text is empty")

    # The selected language governs the voice (single source: agent_config, read here so we
    # don't trust the client). 'auto' → default voice (v1 does no text language detection).
    language = get_config().get("language", "auto")
    voice_id = voice_for(language)

    # C′ robustness (design §8.3): NEVER block on a download here. If the voice for the
    # selected language is not on disk yet, answer voice_not_ready INSTANTLY so the chat keeps
    # working and the UI can show "voice not available — retry". Fetching is stage 3.
    if not _present(voice_id):
        return JSONResponse(
            status_code=503,
            content={"error": "voice_not_ready", "language": language, "voice": voice_id},
        )

    try:
        voice = _get_voice(voice_id)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wav_file:
            voice.synthesize_wav(req.text, wav_file)
        return Response(content=buf.getvalue(), media_type="audio/wav")
    except HTTPException:
        raise
    except wave.Error:
        # piper yielded zero audio chunks (e.g. punctuation-only text), so the wav header was
        # never set and wave raises on close: caller's problem.
        raise HTTPException(400, "text contains nothing speakable")
    except Exception as exc:
        raise HTTPException(500, f"TTS failed: {exc}")
