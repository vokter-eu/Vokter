"""
Stage 3 — voice fetching (docs/Vokter_Voz.md §9).

Downloads a Piper voice on demand and VERIFIES it (size + md5, from the pinned voices.json)
BEFORE promoting it into place, so a partial or corrupt file is never seen as ready. One
idempotent primitive, `ensure_voice`, behind an in-flight guard so the onboarding / Settings /
retry / startup triggers coalesce into a single download instead of racing.

Checksums come from voices.json at download time (decision (b)) — NOT baked into languages.py —
so swapping a voice stays a one-line edit (just the voice id).
"""
import hashlib
import json
import os
import tempfile
import threading
import urllib.request

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from agent_config import get_config
from languages import voice_for
from voice.piper import _paths, _piper_dir, _present

router = APIRouter()

# Pinned tag → every user downloads the same bytes (reproducible). Overridable for tests.
PIPER_BASE = os.getenv(
    "VOKTER_PIPER_VOICES_BASE",
    "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0",
).rstrip("/")
_HTTP_TIMEOUT = 60
_CHUNK = 1 << 16

# voices.json (the checksums) — fetched once, cached on disk (pinned tag → stable).
_vjson_cache: dict | None = None
_vjson_lock = threading.Lock()

# Per-voice download state + in-flight guard, so concurrent ensure() calls dedupe.
_state: dict[str, dict] = {}
_state_lock = threading.Lock()
_voice_locks: dict[str, threading.Lock] = {}


def _lock_for(voice_id: str) -> threading.Lock:
    with _state_lock:
        lk = _voice_locks.get(voice_id)
        if lk is None:
            lk = _voice_locks[voice_id] = threading.Lock()
        return lk


def _set_state(voice_id: str, status: str, downloaded: int = 0, total: int = 0) -> None:
    with _state_lock:
        _state[voice_id] = {"status": status, "downloaded": downloaded, "total": total}


def _state_for(voice_id: str) -> dict:
    with _state_lock:
        st = _state.get(voice_id)
        if st is None:
            st = {"status": "ready" if _present(voice_id) else "absent", "downloaded": 0, "total": 0}
    return {**st, "voice": voice_id}


def _voices_json() -> dict:
    global _vjson_cache
    if _vjson_cache is not None:
        return _vjson_cache
    with _vjson_lock:
        if _vjson_cache is not None:
            return _vjson_cache
        cache_path = os.path.join(_piper_dir(), "voices.json")
        if os.path.exists(cache_path):
            with open(cache_path, "rb") as f:
                _vjson_cache = json.load(f)
                return _vjson_cache
        with urllib.request.urlopen(f"{PIPER_BASE}/voices.json", timeout=_HTTP_TIMEOUT) as r:
            data = r.read()
        parsed = json.loads(data)
        tmp = cache_path + ".part"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, cache_path)          # cache atomically too
        _vjson_cache = parsed
        return _vjson_cache


def _expected_files(voice_id: str) -> list[tuple[str, str, int, str]]:
    """[(remote_path, local_final_path, size_bytes, md5)] for the voice's .onnx + .onnx.json.
    Checksums read from voices.json — this is what keeps the table md5-free (swap = one line)."""
    entry = _voices_json().get(voice_id)
    if entry is None:
        raise ValueError(f"unknown voice '{voice_id}' (not in voices.json)")
    model_path, config_path = _paths(voice_id)
    out = []
    for remote, meta in entry["files"].items():
        if remote.endswith(".onnx.json"):
            local = config_path
        elif remote.endswith(".onnx"):
            local = model_path
        else:
            continue                          # skip MODEL_CARD etc.
        out.append((remote, local, meta["size_bytes"], meta["md5_digest"]))
    return out


def _download_verify(remote_path: str, size: int, md5: str, dest_dir: str, on_progress) -> str:
    """Download {PIPER_BASE}/{remote_path} to a UNIQUE temp in dest_dir, verify size+md5, and
    return the temp path. On ANY failure remove the temp and re-raise. Never touches finals —
    a corrupt/partial download therefore cannot be seen as ready (_present checks finals)."""
    url = f"{PIPER_BASE}/{remote_path}"
    fd, tmp = tempfile.mkstemp(dir=dest_dir, suffix=".part")   # unique → no shared .part
    h = hashlib.md5()
    got = 0
    try:
        with os.fdopen(fd, "wb") as out, urllib.request.urlopen(url, timeout=_HTTP_TIMEOUT) as r:
            while True:
                chunk = r.read(_CHUNK)
                if not chunk:
                    break
                out.write(chunk)
                h.update(chunk)
                got += len(chunk)
                on_progress(got)
        if got != size:
            raise ValueError(f"size mismatch for {remote_path}: got {got}, expected {size}")
        if h.hexdigest() != md5:
            raise ValueError(f"md5 mismatch for {remote_path}: got {h.hexdigest()}, expected {md5}")
        return tmp
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def ensure_voice(voice_id: str) -> dict:
    """Ensure voice_id is present and verified on disk. Idempotent; in-flight-guarded so
    concurrent callers coalesce into ONE download. Returns the state dict. Raises on failure
    (after cleaning up) — callers/endpoint turn that into a not-ready state, never a crash."""
    if _present(voice_id):
        _set_state(voice_id, "ready")
        return _state_for(voice_id)

    lock = _lock_for(voice_id)
    if not lock.acquire(blocking=False):
        # Another caller is already downloading this exact voice — wait for it, don't start a
        # second download. When it finishes we simply report the result.
        with lock:
            pass
        _set_state(voice_id, "ready" if _present(voice_id) else "error")
        return _state_for(voice_id)

    temps: list[tuple[str, str]] = []
    try:
        if _present(voice_id):                # won the race after acquiring
            _set_state(voice_id, "ready")
            return _state_for(voice_id)
        _set_state(voice_id, "downloading")   # attempt started — any failure below → error
        files = _expected_files(voice_id)     # may raise (voices.json fetch / unknown voice)
        total = sum(s for _, _, s, _ in files)
        _set_state(voice_id, "downloading", 0, total)
        dest = _piper_dir()
        base = 0
        for remote, local, size, md5 in files:
            start = base
            tmp = _download_verify(
                remote, size, md5, dest,
                lambda g, s=start: _set_state(voice_id, "downloading", s + g, total),
            )
            temps.append((tmp, local))
            base += size
        for tmp, local in temps:              # all verified → promote atomically
            os.replace(tmp, local)
        _set_state(voice_id, "ready")
        return _state_for(voice_id)
    except BaseException:
        for tmp, _ in temps:                  # clean any verified-but-not-yet-promoted temps
            try:
                os.remove(tmp)
            except OSError:
                pass
        _set_state(voice_id, "error")
        raise
    finally:
        lock.release()


def _current_voice() -> str:
    return voice_for(get_config().get("language", "auto"))


def _safe_ensure(voice_id: str) -> None:
    try:
        ensure_voice(voice_id)
    except Exception:
        pass                                  # state is already set to "error"; nothing to crash


@router.post("/api/voice/ensure")
def voice_ensure():
    """Kick off (or join) the download of the current language's voice. Non-blocking: returns
    the current state immediately; the UI polls /api/voice/state for progress."""
    voice_id = _current_voice()
    if _present(voice_id):
        _set_state(voice_id, "ready")
    else:
        _set_state(voice_id, "downloading")
        threading.Thread(target=_safe_ensure, args=(voice_id,), daemon=True).start()
    return JSONResponse(_state_for(voice_id))


@router.get("/api/voice/state")
def voice_state():
    return JSONResponse(_state_for(_current_voice()))
