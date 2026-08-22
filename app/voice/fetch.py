"""
Stage 3 — voice asset fetching, from Vokter's OWN mirror (sovereign: no third-party host is
contacted at runtime — not thewh1teagle, not HuggingFace). Two assets:

  * TTS  = Kokoro model + voices (2 files, ~337 MB) → voice/kokoro.py loads them.
  * STT  = faster-whisper-small, shipped as one tar of 4 files (~464 MB) → extracted into place,
           voice/whisper.py loads the directory (so faster-whisper never phones HuggingFace).

Each file is downloaded to a temp path, sha256-verified, and only then moved/extracted into
place, so a partial or tampered download is never seen as ready. Guarded per-asset by an
in-flight lock so the onboarding / Settings / retry / startup triggers coalesce instead of
racing. Best-effort at startup: any failure leaves the asset absent and the backend keeps
running (speak()/transcribe() answer voice_not_ready) — NEVER blocks or crashes boot.

State the UI polls: GET /api/voice/state → {tts:{status,downloaded,total}, stt:{...}}.
POST /api/voice/ensure {asset:"tts"|"stt"} kicks (or joins) that download.
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

# faster-whisper loads THIS directory (voice/whisper.py points at it), never a HuggingFace name.
WHISPER_DIR = os.path.join(VOICE_MODELS_DIR, "whisper", "faster-whisper-small")

_TOTALS = {"tts": sum(sz for _, _, sz in _TTS_FILES), "stt": _STT_TAR[2]}
_state = {a: {"status": "idle", "downloaded": 0, "total": _TOTALS[a]} for a in ("tts", "stt")}
_state_lock = threading.Lock()
_inflight = {"tts": threading.Lock(), "stt": threading.Lock()}


def _set_state(asset: str, status: str, downloaded: int = 0) -> None:
    with _state_lock:
        _state[asset].update(status=status, downloaded=downloaded, total=_TOTALS[asset])


def _stt_present() -> bool:
    return os.path.exists(os.path.join(WHISPER_DIR, "model.bin"))


def _present(asset: str) -> bool:
    return _tts_present() if asset == "tts" else _stt_present()


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


def _ensure_tts(bump) -> None:
    d = _kokoro_dir()
    onnx_dest, voices_dest = _kokoro_paths()
    dests = {"kokoro-v1.0.onnx": onnx_dest, "voices-v1.0.bin": voices_dest}
    for name, sha, size in _TTS_FILES:
        dest = dests[name]
        if os.path.exists(dest) and os.path.getsize(dest) == size:
            bump(size)                        # already have this file
            continue
        tmp = _download(name, sha, d, bump)
        os.replace(tmp, dest)                 # atomic


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


def ensure(asset: str) -> dict:
    """Download `asset` ("tts"|"stt") if absent. Idempotent, in-flight-guarded. Returns state."""
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

        (_ensure_tts if asset == "tts" else _ensure_stt)(bump)
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
    """Trigger 3 — on boot, kick a best-effort background download of any absent voice asset, so
    both are ready by the time the user speaks/listens. Any failure leaves the asset absent and
    the backend keeps running (voice_not_ready). NEVER blocks or crashes startup (C′ invariant)."""
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
    asset = req.asset if req.asset in ("tts", "stt") else "tts"
    if _present(asset):
        _set_state(asset, "ready", _TOTALS[asset])
    else:
        _set_state(asset, "downloading", 0)
        threading.Thread(target=_safe_ensure, args=(asset,), daemon=True).start()
    return JSONResponse(_state_for(asset))


@router.get("/api/voice/state")
def voice_state():
    for asset in ("tts", "stt"):
        if _present(asset) and _state_for(asset)["status"] != "downloading":
            _set_state(asset, "ready", _TOTALS[asset])
    with _state_lock:
        return JSONResponse({a: dict(_state[a]) for a in ("tts", "stt")})
