import os
import tempfile

from fastapi import APIRouter, HTTPException, UploadFile

from agent_config import get_config
from config import VOICE_MODELS_DIR, WHISPER_DEVICE
from languages import whisper_lang

router = APIRouter()

_model = None


def _get_model():
    global _model
    if _model is not None:
        return _model
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise HTTPException(503, "faster-whisper not installed — add it to requirements.txt")
    # Sovereign: load ONLY the mirrored faster-whisper-small directory (fetched by voice/fetch.py
    # from our OWN host). Never pass a model NAME — that would make faster-whisper phone HuggingFace.
    # Absent → voice_not_ready (503); the UI kicks the download (POST /api/voice/ensure {asset:stt})
    # and shows progress, so transcribe never silently hangs on a multi-hundred-MB fetch.
    local = os.path.join(VOICE_MODELS_DIR, "whisper", "faster-whisper-small")
    if not os.path.exists(os.path.join(local, "model.bin")):
        raise HTTPException(503, "voice_not_ready: speech-to-text model not downloaded yet")
    print(f"voice: loading Whisper (small) from {local} on {WHISPER_DEVICE}…")
    _model = WhisperModel(local, device=WHISPER_DEVICE, compute_type="int8")
    print("voice: Whisper ready.")
    return _model


@router.post("/api/voice/transcribe")
async def transcribe(audio: UploadFile):
    suffix = os.path.splitext(audio.filename or "audio.webm")[1] or ".webm"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(await audio.read())
        tmp_path = tmp.name
    try:
        model = _get_model()
        # Pass the selected language to Whisper when it's concrete (more accurate); None for
        # 'auto'/unknown → Whisper auto-detects, today's behaviour.
        lang = whisper_lang(get_config().get("language", "auto"))
        segments, _ = model.transcribe(tmp_path, beam_size=5, language=lang)
        text = " ".join(s.text.strip() for s in segments).strip()
        return {"text": text}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f"Transcription failed: {exc}")
    finally:
        os.unlink(tmp_path)
