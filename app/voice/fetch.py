"""
Stage 3 — voice fetching (docs/Vokter_Voz.md §9), now for the Kokoro TTS model.

Kokoro is a SINGLE local asset (one ONNX model + one voices file) that covers every language
it speaks, so this is simpler than the old per-Piper-voice fetcher: one idempotent primitive,
`ensure_kokoro`, behind an in-flight guard so the onboarding / Settings / retry / startup
triggers coalesce into one download instead of racing. Each file is downloaded to a temp path
and only renamed into place once its byte size matches the pinned expectation, so a partial or
interrupted download is never seen as ready.

Best-effort BY DESIGN: any failure (offline boot, server down) leaves the model absent and the
backend keeps running — speak() answers voice_not_ready. It can NEVER block or crash startup.
"""
import os
import tempfile
import threading
import urllib.request

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from voice.kokoro import _kokoro_dir, _model_paths, _present

router = APIRouter()

# Pinned release → every user downloads the same bytes. Overridable for tests/mirrors.
KOKORO_BASE = os.getenv(
    "VOKTER_KOKORO_BASE",
    "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0",
).rstrip("/")
# (filename, exact byte size) — the size is the integrity check (kokoro-onnx also fails loudly
# on a corrupt model at load, so we don't carry a second md5 table here).
_FILES = [
    ("kokoro-v1.0.onnx", 325532387),
    ("voices-v1.0.bin",   28214398),
]
_HTTP_TIMEOUT = 60
_CHUNK = 1 << 16
_TOTAL = sum(sz for _, sz in _FILES)

_state = {"status": "idle", "downloaded": 0, "total": _TOTAL}
_state_lock = threading.Lock()
_inflight = threading.Lock()      # only one download at a time; concurrent callers dedupe


def _set_state(status: str, downloaded: int = 0) -> None:
    with _state_lock:
        _state.update(status=status, downloaded=downloaded, total=_TOTAL)


def _state_for() -> dict:
    with _state_lock:
        return dict(_state)


def _download_verify(name: str, size: int, dest: str, on_progress) -> None:
    """Download one file to a temp path, verify its size, then atomically rename into `dest`.
    Raises on any mismatch so a partial file is never promoted."""
    url = f"{KOKORO_BASE}/{name}"
    d = os.path.dirname(dest)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".part")
    got = 0
    try:
        with os.fdopen(fd, "wb") as out:
            req = urllib.request.Request(url, headers={"User-Agent": "Vokter"})
            with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as r:
                while True:
                    chunk = r.read(_CHUNK)
                    if not chunk:
                        break
                    out.write(chunk)
                    got += len(chunk)
                    on_progress(len(chunk))
        if got != size:
            raise ValueError(f"{name}: size {got} != expected {size}")
        os.replace(tmp, dest)             # atomic: readers see all-or-nothing
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def ensure_kokoro() -> dict:
    """Download the Kokoro model + voices if absent. Idempotent and in-flight-guarded.
    Returns the final state dict."""
    if _present():
        _set_state("ready", _TOTAL)
        return _state_for()
    if not _inflight.acquire(blocking=False):
        return _state_for()               # another thread is already downloading
    try:
        _set_state("downloading", 0)
        done = 0

        def bump(n):
            nonlocal done
            done += n
            _set_state("downloading", done)

        model, voices = _model_paths()
        dests = {"kokoro-v1.0.onnx": model, "voices-v1.0.bin": voices}
        for name, size in _FILES:
            dest = dests[name]
            if os.path.exists(dest) and os.path.getsize(dest) == size:
                bump(size)                # already have this file
                continue
            _download_verify(name, size, dest, bump)
        _set_state("ready", _TOTAL)
    except Exception:
        _set_state("error", 0)
    finally:
        _inflight.release()
    return _state_for()


def _safe_ensure() -> None:
    try:
        ensure_kokoro()
    except Exception:
        pass                              # state is already "error"; nothing to crash


def opportunistic_startup_fetch() -> None:
    """Trigger 3 — opportunistic startup. On boot, if the Kokoro model is absent, kick ONE
    best-effort download in a daemon thread. Any failure leaves it absent and the backend keeps
    running (speak() → voice_not_ready). NEVER blocks or crashes startup (C′ invariant)."""
    try:
        if _present():
            return
    except Exception:
        return
    _set_state("downloading", 0)
    threading.Thread(target=_safe_ensure, daemon=True).start()


@router.post("/api/voice/ensure")
def voice_ensure():
    """Kick off (or join) the Kokoro download. Non-blocking: returns state immediately; the UI
    polls /api/voice/state for progress."""
    if _present():
        _set_state("ready", _TOTAL)
    else:
        _set_state("downloading", 0)
        threading.Thread(target=_safe_ensure, daemon=True).start()
    return JSONResponse(_state_for())


@router.get("/api/voice/state")
def voice_state():
    if _present() and _state_for()["status"] != "downloading":
        _set_state("ready", _TOTAL)       # already on disk (seeded or fetched) → report ready
    return JSONResponse(_state_for())
