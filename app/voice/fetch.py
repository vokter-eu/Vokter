"""
Stage 3 — voice asset fetching, from Vokter's OWN mirror (sovereign: no third-party host is
contacted at runtime — not thewh1teagle, not HuggingFace, not rhasspy). Assets:

  * TTS  = Kokoro model + voices (2 files, ~337 MB) → voice/kokoro.py loads them (en/es/fr/it/pt).
  * STT  = faster-whisper-small, one tar of 4 files (~464 MB) → extracted, voice/whisper.py
           loads the directory (so faster-whisper never phones HuggingFace).
  * PACKS = one Piper voice per language Kokoro can't speak (de/nl/ca): a .onnx + .onnx.json,
            downloaded on demand → voice/piper.py loads them. NOT fetched at boot (per-language,
            opt-in) and NOT bundled in the .deb (keeps it lean).

Each file is downloaded to a temp path, sha256-verified, and only then moved/extracted into
place, so a partial or tampered download is never seen as ready. Guarded per-asset by an
in-flight lock so the onboarding / Settings / retry / startup triggers coalesce instead of
racing. Best-effort at startup for tts+stt: any failure leaves the asset absent and the backend
keeps running (speak()/transcribe() answer voice_not_ready) — NEVER blocks or crashes boot.

State the UI polls: GET /api/voice/state → {tts:{…}, stt:{…}, packs:{de:{…}, nl:{…}, ca:{…}}}.
POST /api/voice/ensure {asset:"tts"|"stt"|"de"|"nl"|"ca"} kicks (or joins) that download.
"""
import hashlib
import os
import tarfile
import tempfile
import threading
import urllib.request

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from config import VOICE_MODELS_DIR
from voice import piper
from voice.kokoro import _kokoro_dir, _model_paths as _kokoro_paths, _present as _tts_present

router = APIRouter()

# Sovereign mirror: our own GitHub release. Overridable (tests / a future CDN we control).
BASE = os.getenv(
    "VOKTER_VOICE_ASSETS_BASE",
    "https://github.com/vokter-eu/Vokter/releases/download/voice-assets-v1",
).rstrip("/")
_HTTP_TIMEOUT = 60
_CHUNK = 1 << 16

# (filename, sha256, exact size) — sha256 is the integrity gate; size drives the progress bar.
_TTS_FILES = [
    ("kokoro-v1.0.onnx", "7d5df8ecf7d4b1878015a32686053fd0eebe2bc377234608764cc0ef3636a6c5", 325532387),
    ("voices-v1.0.bin",  "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d",  28214398),
]
_STT_TAR = ("faster-whisper-small.tar", "9fd25a74442fe559a72d99b38ad3b3c24a25eeb9c60435b1b506789956e57298", 486225920)

# Piper packs: lang → (voice id, [(filename, sha256, size), …]). Filenames match the mirrored
# release assets AND what voice/piper.py loads (voice_id + .onnx / .onnx.json).
_PACKS: dict[str, tuple[str, list]] = {
    "ca": ("ca_ES-upc_ona-medium", [
        ("ca_ES-upc_ona-medium.onnx",      "fdb652db8c11a4475527346cf3241cb064d1ba393cf370f3f2ec09a872d118fd", 63201294),
        ("ca_ES-upc_ona-medium.onnx.json", "7f76acc9c06f4eda9e6aef2997b75782d97855aab48d4b401eb956a6e655eddc",     4875),
    ]),
    "de": ("de_DE-thorsten-medium", [
        ("de_DE-thorsten-medium.onnx",      "7e64762d8e5118bb578f2eea6207e1a35a8e0c30595010b666f983fc87bb7819", 63201294),
        ("de_DE-thorsten-medium.onnx.json", "974adee790533adb273a1ac88f49027d2a1b8f0f2cf4905954a4791e79264e85",     4819),
    ]),
    "nl": ("nl_NL-mls-medium", [
        ("nl_NL-mls-medium.onnx",      "88312e0fbf505b87caf2373d94c1384892e86b1bf2ee482cf65dc8ba179cc7d3", 76584246),
        ("nl_NL-mls-medium.onnx.json", "6ddb215d38f1392ab935ad45441b82ada1eeae0452a2d6849ed71ea4f2e0aa63",     5856),
    ]),
}
_PACK_LANGS = tuple(_PACKS)                       # ("ca", "de", "nl")
_ASSETS = ("tts", "stt") + _PACK_LANGS

# faster-whisper loads THIS directory (voice/whisper.py points at it), never a HuggingFace name.
WHISPER_DIR = os.path.join(VOICE_MODELS_DIR, "whisper", "faster-whisper-small")

_TOTALS = {"tts": sum(sz for _, _, sz in _TTS_FILES), "stt": _STT_TAR[2]}
_TOTALS.update({lang: sum(sz for _, _, sz in files) for lang, (_, files) in _PACKS.items()})
_state = {a: {"status": "idle", "downloaded": 0, "total": _TOTALS[a]} for a in _ASSETS}
_state_lock = threading.Lock()
_inflight = {a: threading.Lock() for a in _ASSETS}


def _set_state(asset: str, status: str, downloaded: int = 0) -> None:
    with _state_lock:
        _state[asset].update(status=status, downloaded=downloaded, total=_TOTALS[asset])


def _stt_present() -> bool:
    return os.path.exists(os.path.join(WHISPER_DIR, "model.bin"))


def _present(asset: str) -> bool:
    if asset == "tts":
        return _tts_present()
    if asset == "stt":
        return _stt_present()
    return piper.present(_PACKS[asset][0])         # pack lang → both voice files on disk


def _download(name: str, sha256: str, dest_dir: str, on_progress) -> str:
    """Download BASE/name → a temp file in dest_dir, verify its sha256, return the temp path.
    Raises on any mismatch so a partial/tampered file is never promoted."""
    os.makedirs(dest_dir, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dest_dir, suffix=".part")
    h = hashlib.sha256()
    try:
        with os.fdopen(fd, "wb") as out:
            req = urllib.request.Request(f"{BASE}/{name}", headers={"User-Agent": "Vokter"})
            with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as r:
                while True:
                    chunk = r.read(_CHUNK)
                    if not chunk:
                        break
                    out.write(chunk)
                    h.update(chunk)
                    on_progress(len(chunk))
        if h.hexdigest() != sha256:
            raise ValueError(f"{name}: sha256 mismatch")
        return tmp
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def _ensure_files(files, dest_for, bump) -> None:
    """Download each (name, sha, size) whose destination isn't already present at the right size,
    verifying before an atomic move into place. Shared by TTS and the Piper packs."""
    for name, sha, size in files:
        dest = dest_for(name)
        if os.path.exists(dest) and os.path.getsize(dest) == size:
            bump(size)                            # already have this file
            continue
        tmp = _download(name, sha, os.path.dirname(dest), bump)
        os.replace(tmp, dest)                     # atomic


def _ensure_tts(bump) -> None:
    onnx_dest, voices_dest = _kokoro_paths()
    dests = {"kokoro-v1.0.onnx": onnx_dest, "voices-v1.0.bin": voices_dest}
    os.makedirs(_kokoro_dir(), exist_ok=True)
    _ensure_files(_TTS_FILES, lambda name: dests[name], bump)


def _ensure_pack(lang: str, bump) -> None:
    _, files = _PACKS[lang]
    d = piper.piper_dir()
    os.makedirs(d, exist_ok=True)
    _ensure_files(files, lambda name: os.path.join(d, name), bump)


def _ensure_stt(bump) -> None:
    name, sha, _ = _STT_TAR
    parent = os.path.dirname(WHISPER_DIR)
    tmp = _download(name, sha, parent, bump)
    try:
        os.makedirs(WHISPER_DIR, exist_ok=True)
        with tarfile.open(tmp) as tf:
            tf.extractall(WHISPER_DIR, filter="data")   # the 4 model files
    finally:
        os.remove(tmp)


def _run_ensure(asset: str, bump) -> None:
    if asset == "tts":
        _ensure_tts(bump)
    elif asset == "stt":
        _ensure_stt(bump)
    else:
        _ensure_pack(asset, bump)


def ensure(asset: str) -> dict:
    """Download `asset` if absent. Idempotent, in-flight-guarded. Returns state."""
    if _present(asset):
        _set_state(asset, "ready", _TOTALS[asset])
        return _state_for(asset)
    if not _inflight[asset].acquire(blocking=False):
        return _state_for(asset)              # another thread is already on it
    try:
        _set_state(asset, "downloading", 0)
        done = 0

        def bump(n):
            nonlocal done
            done += n
            _set_state(asset, "downloading", done)

        _run_ensure(asset, bump)
        _set_state(asset, "ready", _TOTALS[asset])
    except Exception:
        _set_state(asset, "error", 0)
    finally:
        _inflight[asset].release()
    return _state_for(asset)


def _state_for(asset: str) -> dict:
    with _state_lock:
        return dict(_state[asset])


def _safe_ensure(asset: str) -> None:
    try:
        ensure(asset)
    except Exception:
        pass                                  # state already "error"; never crash the daemon


def opportunistic_startup_fetch() -> None:
    """Trigger 3 — on boot, kick a best-effort background download of the base tts+stt assets, so
    both are ready by the time the user speaks/listens. Piper packs are NOT fetched here: they are
    per-language and opt-in (Settings / a 503 retry), never downloaded speculatively. Any failure
    leaves the asset absent and the backend keeps running (voice_not_ready). NEVER blocks or
    crashes startup (C′ invariant)."""
    for asset in ("tts", "stt"):
        try:
            if _present(asset):
                continue
        except Exception:
            continue
        _set_state(asset, "downloading", 0)
        threading.Thread(target=_safe_ensure, args=(asset,), daemon=True).start()


class EnsureRequest(BaseModel):
    asset: str = "tts"


@router.post("/api/voice/ensure")
def voice_ensure(req: EnsureRequest):
    """Kick off (or join) a voice-asset download. Non-blocking: returns state immediately; the UI
    polls /api/voice/state for progress."""
    asset = req.asset if req.asset in _ASSETS else "tts"
    if _present(asset):
        _set_state(asset, "ready", _TOTALS[asset])
    else:
        _set_state(asset, "downloading", 0)
        threading.Thread(target=_safe_ensure, args=(asset,), daemon=True).start()
    return JSONResponse(_state_for(asset))


@router.get("/api/voice/state")
def voice_state():
    for asset in _ASSETS:
        if _present(asset) and _state_for(asset)["status"] != "downloading":
            _set_state(asset, "ready", _TOTALS[asset])
    with _state_lock:
        base = {a: dict(_state[a]) for a in ("tts", "stt")}
        base["packs"] = {lang: dict(_state[lang]) for lang in _PACK_LANGS}
        return JSONResponse(base)
