"""
Voice provider registry — owns POST /api/voice/speak and routes a reply language to the engine
that can speak it:

  * Kokoro (voice/kokoro.py): en/es/fr/it/pt, one always-present model (the "tts" asset).
  * Piper  (voice/piper.py): de/nl/ca, one downloadable voice pack per language.

The reply language is the selector value when it is concrete (the reply is forced into it), or a
light local text heuristic when the selector is 'auto'. A language no engine covers returns
voice_not_ready (reason 'no_voice'); a covered language whose model/pack isn't downloaded yet
returns voice_not_ready (reason 'model_missing') with the `asset` the UI should fetch
(voice/fetch.py) — never a blocking download here (C′).
"""
import re

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from agent_config import get_config
from voice import kokoro, piper

router = APIRouter()

# Languages Kokoro can't speak → the Piper voice pack that can. Voice ids match the mirrored
# pack filenames (voice/fetch.py) and the fetch asset id is the language code.
PIPER_PACKS: dict[str, str] = {
    "de": "de_DE-thorsten-medium",
    "nl": "nl_NL-mls-medium",
    "ca": "ca_ES-upc_ona-medium",
}


# 'auto' language detection for the reply text. Common case is EN vs ES (the app's two UI
# languages); fr/it/pt get light hints. Zero-dependency on purpose — a concrete selector value
# skips this entirely (the reply was forced into that language, so it's authoritative). Only the
# Kokoro languages are auto-detected; de/nl/ca need an explicit selection (auto can't tell ca
# from es on a small model), same as before.
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
    return _detect(text)              # 'auto' → guess from the reply text


def _not_ready(lang: str, reason: str, asset: str | None = None) -> JSONResponse:
    body = {"error": "voice_not_ready", "language": lang, "reason": reason}
    if asset:
        body["asset"] = asset         # what the UI should download (tts, or a pack lang)
    return JSONResponse(status_code=503, content=body)


class SpeakRequest(BaseModel):
    text: str


@router.post("/api/voice/speak")
def speak(req: SpeakRequest):
    # sync on purpose: FastAPI runs it in the threadpool, so CPU-bound synthesis can't
    # freeze the event loop.
    if not req.text.strip():
        raise HTTPException(400, "text is empty")

    lang = _lang_for_reply(req.text)

    if lang in kokoro.KOKORO_LANGS:
        if not kokoro._present():
            return _not_ready(lang, "model_missing", asset="tts")
        engine, synth = "Kokoro", lambda: kokoro.synthesize(lang, req.text)
    elif lang in PIPER_PACKS:
        voice_id = PIPER_PACKS[lang]
        if not piper.present(voice_id):
            return _not_ready(lang, "model_missing", asset=lang)
        engine, synth = "Piper", lambda: piper.synthesize(voice_id, req.text)
    else:
        # No engine speaks this language — never emit wrong-language audio.
        return _not_ready(lang, "no_voice")

    try:
        wav = synth()
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(502, f"{engine} synthesis failed: {e!r}"[:300])
    return Response(content=wav, media_type="audio/wav")
