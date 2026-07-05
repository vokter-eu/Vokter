import io
import os
import urllib.request
import wave

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from config import PIPER_VOICE, VOICE_MODELS_DIR

router = APIRouter()

_voice = None


class SpeakRequest(BaseModel):
    text: str


def _models_dir() -> str:
    d = os.path.join(VOICE_MODELS_DIR, "piper")
    os.makedirs(d, exist_ok=True)
    return d


def _voice_urls(voice_id: str) -> tuple[str, str]:
    """Build the HuggingFace URL for a piper voice ID like 'en_US-lessac-medium'."""
    parts = voice_id.split("-")
    lang_full = parts[0]                      # e.g. en_US
    lang = lang_full.split("_")[0].lower()    # e.g. en
    speaker = parts[1]                        # e.g. lessac
    quality = parts[2]                        # e.g. medium
    base = (
        "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0"
        f"/{lang}/{lang_full}/{speaker}/{quality}/{voice_id}"
    )
    return f"{base}.onnx", f"{base}.onnx.json"


def _get_voice():
    global _voice
    if _voice is not None:
        return _voice
    try:
        from piper.voice import PiperVoice
    except ImportError:
        raise HTTPException(503, "piper-tts not installed — add it to requirements.txt")

    d = _models_dir()
    model_path = os.path.join(d, f"{PIPER_VOICE}.onnx")
    config_path = os.path.join(d, f"{PIPER_VOICE}.onnx.json")

    if not os.path.exists(model_path):
        onnx_url, cfg_url = _voice_urls(PIPER_VOICE)
        print(f"voice: downloading piper voice '{PIPER_VOICE}'…")
        urllib.request.urlretrieve(onnx_url, model_path)
        urllib.request.urlretrieve(cfg_url, config_path)
        print("voice: piper voice ready.")

    _voice = PiperVoice.load(model_path, config_path=config_path, use_cuda=False)
    return _voice


@router.post("/api/voice/speak")
async def speak(req: SpeakRequest):
    if not req.text.strip():
        raise HTTPException(400, "text is empty")
    try:
        voice = _get_voice()
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wav_file:
            voice.synthesize_wav(req.text, wav_file)
        return Response(content=buf.getvalue(), media_type="audio/wav")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f"TTS failed: {exc}")
