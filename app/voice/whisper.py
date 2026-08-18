import os
import tempfile

from fastapi import APIRouter, HTTPException, UploadFile

from agent_config import get_config
from config import VOICE_MODELS_DIR, WHISPER_DEVICE, WHISPER_MODEL
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
    download_root = os.path.join(VOICE_MODELS_DIR, "whisper")
    os.makedirs(download_root, exist_ok=True)
    # Prefer the model bundled + seeded by the desktop package (offline, no
    # download). Fall back to the model NAME, which faster-whisper downloads into
    # download_root on demand — the behaviour when there is no seeded copy.
    seeded = os.path.join(download_root, f"{WHISPER_MODEL}-int8")
    model_ref = seeded if os.path.exists(os.path.join(seeded, "model.bin")) else WHISPER_MODEL
    print(f"voice: loading Whisper model '{model_ref}' on {WHISPER_DEVICE}…")
    _model = WhisperModel(
        model_ref,
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
