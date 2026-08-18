"""
Local TTS via Kokoro-82M (Apache-2.0), run on CPU through kokoro-onnx (onnxruntime —
the same runtime the STT stack already ships; NO PyTorch). Replaces Piper.

ONE model covers every language Kokoro speaks (en/es/fr/it/pt). The voice is chosen by the
REPLY language: the selector value when it is concrete (the reply is forced into it), or a
light local text heuristic when the selector is 'auto'. A language Kokoro cannot speak
(e.g. de/nl/ca) returns voice_not_ready rather than emitting wrong-language audio.

Contract unchanged from Piper: POST /api/voice/speak {text} -> audio/wav (24 kHz mono).
speak() NEVER downloads (fetching is stage 3, voice/fetch.py); a missing model yields
voice_not_ready so the chat keeps working and the UI can say "voice not available".
"""
import io
import os
import re
import threading
import wave

import numpy as np
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from agent_config import get_config
from config import VOICE_MODELS_DIR

router = APIRouter()

# Reply language -> (Kokoro voice id, Kokoro phonemizer lang code). Only the languages
# Kokoro v1.0 can actually speak; a language absent here has no local voice (de/nl/ca) and
# speak() returns voice_not_ready instead of the wrong voice. Kokoro's Portuguese is
# Brazilian (pt-br) — the only pt it ships.
_VOICES: dict[str, tuple[str, str]] = {
    "en": ("af_heart", "en-us"),
    "es": ("ef_dora",  "es"),
    "fr": ("ff_siwis", "fr-fr"),
    "it": ("if_sara",  "it"),
    "pt": ("pf_dora",  "pt-br"),
}


def _kokoro_dir() -> str:
    return os.path.join(VOICE_MODELS_DIR, "kokoro")


def _model_paths() -> tuple[str, str]:
    d = _kokoro_dir()
    return os.path.join(d, "kokoro-v1.0.onnx"), os.path.join(d, "voices-v1.0.bin")


def _present() -> bool:
    m, v = _model_paths()
    return os.path.exists(m) and os.path.exists(v)


_kokoro = None
_lock = threading.Lock()      # load is one-time; speak() runs in FastAPI's threadpool


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


# 'auto' language detection for the reply text. The common case is EN vs ES (the app's two UI
# languages); fr/it/pt get light hints. Zero-dependency on purpose — a concrete selector value
# skips this entirely (the reply was forced into that language, so it's authoritative).
#
# Two tiers: STRONG cues are language-DISTINCTIVE (own accented chars / near-unique words) and
# score 2; WEAK cues are pan-Romance stopwords (de/la/che/uma…) that keep recall but, at score 1,
# can't outvote a neighbour's own strong markers — so an Italian/Portuguese reply isn't mistaken
# for Spanish just because it shares a few little words.
_STRONG = {
    "es": re.compile(r"[ñ¿¡]|\b(est[áa][sn]?|c[óo]mo|qu[ée]|gracias|porque|tambi[ée]n|usted(es)?|espa[ñn]ol|hola)\b", re.I),
    "fr": re.compile(r"[çœ]|\b(bonjour|merci|c'est|être|fran[çc]ais|vous|voilà|aujourd)\b", re.I),
    "it": re.compile(r"\b(perch[ée]|ciao|grazie|per[òo]|sono|molto|italiano|questo)\b", re.I),
    "pt": re.compile(r"[ãõ]|ção|\b(n[ãa]o|voc[êe]|obrigad[oa]|est[áa]|portugu[êe]s|tamb[ée]m)\b", re.I),
}
_WEAK = {
    "es": re.compile(r"\b(que|de|la|el|los|las|una?|con|para|pero)\b", re.I),
    "fr": re.compile(r"\b(le|les|est|pour|avec|une|je|des)\b", re.I),
    "it": re.compile(r"\b(che|di|il|una|sei|come)\b", re.I),
    "pt": re.compile(r"\b(uma|isso|estou|porque|com)\b", re.I),
}


def _detect(text: str) -> str:
    scores = {lang: 2 * len(_STRONG[lang].findall(text)) + len(_WEAK[lang].findall(text))
              for lang in _STRONG}
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "en"


def _lang_for_reply(text: str) -> str:
    sel = (get_config().get("language") or "auto").strip()
    if sel != "auto":
        return sel                    # the reply is forced into the selected language
    return _detect(text)              # 'auto' → guess from the reply text (fixes Piper's auto→EN bug)


class SpeakRequest(BaseModel):
    text: str


@router.post("/api/voice/speak")
def speak(req: SpeakRequest):
    # sync on purpose: FastAPI runs it in the threadpool, so CPU-bound synthesis cannot
    # freeze the event loop.
    if not req.text.strip():
        raise HTTPException(400, "text is empty")

    lang = _lang_for_reply(req.text)
    entry = _VOICES.get(lang)
    if entry is None:
        # Kokoro can't speak this language (de/nl/ca/…) — never emit wrong-language audio.
        return JSONResponse(status_code=503,
                            content={"error": "voice_not_ready", "language": lang, "reason": "no_voice"})
    if not _present():
        # C′: NEVER block on a download here; fetching is stage 3 (voice/fetch.py).
        return JSONResponse(status_code=503,
                            content={"error": "voice_not_ready", "language": lang, "reason": "model_missing"})

    voice, klang = entry
    try:
        samples, sr = _get_kokoro().create(req.text, voice=voice, lang=klang)
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(502, f"Kokoro synthesis failed: {e!r}"[:300])

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav_file:      # float32 [-1,1] → 16-bit PCM WAV, 24 kHz mono
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sr)
        wav_file.writeframes((np.clip(samples, -1.0, 1.0) * 32767.0).astype("<i2").tobytes())
    return Response(content=buf.getvalue(), media_type="audio/wav")
