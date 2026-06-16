import os
import tempfile

from fastapi import APIRouter, HTTPException, UploadFile

from config import VOICE_MODELS_DIR, WHISPER_DEVICE, WHISPER_MODEL

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
    download_root = os.path.join(VOICE_MODELS_DIR, "whisper")
    os.makedirs(download_root, exist_ok=True)
    print(f"voice: loading Whisper model '{WHISPER_MODEL}' on {WHISPER_DEVICE}…")
    _model = WhisperModel(
        WHISPER_MODEL,
        device=WHISPER_DEVICE,
        compute_type="int8",
        download_root=download_root,
    )
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
        segments, _ = model.transcribe(tmp_path, beam_size=5)
        text = " ".join(s.text.strip() for s in segments).strip()
        return {"text": text}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f"Transcription failed: {exc}")
    finally:
        os.unlink(tmp_path)
