"""
Stage 3 — the THREE TRIGGERS' failure behaviour (not the engine; that has its own
fault-tree proof). Here we show what Bilal asked to see: how each trigger behaves on a
network failure, and that NONE of them can block.

  Trigger 1 (onboarding-finish) and Trigger 2 (the ⚠ button) reach the engine through the
  HTTP endpoints /api/voice/ensure + /api/voice/state. Their invariant is: a dead network
  degrades to a clean "error" state, the endpoints NEVER 5xx, and the download is always
  re-triggerable (a failure never leaves the UI dead).

  Trigger 3 (opportunistic startup) is the backend lifespan hook opportunistic_startup_fetch().
  Its invariant is the sharp one: on a dead network it returns AT ONCE and never raises, so
  the backend comes up regardless — startup can never block or crash on a missing voice.

Run: VOKTER_DB=/tmp/x.db VOKTER_VOICE_MODELS_DIR=/tmp/models  python tests/voice_triggers_test.py
(the harness sets those itself). Talks only to a local fake HuggingFace on 127.0.0.1 — no net,
and a UNIQUE temp voice dir per run, so dev data/models are never touched.
"""
import hashlib
import http.server
import json
import os
import socket
import sys
import tempfile
import threading
import time

# --- isolate BEFORE importing app code: temp DB path + temp voice dir (dev data intact) ---
_TMP = tempfile.mkdtemp(prefix="vokter-voicetrig-")
os.environ["VOKTER_DB"] = os.path.join(_TMP, "test.db")          # never created (we patch config reads)
os.environ["VOKTER_VOICE_MODELS_DIR"] = os.path.join(_TMP, "models")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

VOICE = "xx_XX-test-medium"
_MODEL_BODY  = b"ONNX-FAKE-MODEL-" * 512      # stand-in .onnx bytes
_CONFIG_BODY = b'{"fake":"voice-config"}'     # stand-in .onnx.json
_REMOTE_ONNX = f"xx/xx_XX/test/medium/{VOICE}.onnx"
_REMOTE_JSON = _REMOTE_ONNX + ".json"


def _md5(b): return hashlib.md5(b).hexdigest()


def _voices_json():
    return json.dumps({VOICE: {"files": {
        _REMOTE_ONNX: {"size_bytes": len(_MODEL_BODY),  "md5_digest": _md5(_MODEL_BODY)},
        _REMOTE_JSON: {"size_bytes": len(_CONFIG_BODY), "md5_digest": _md5(_CONFIG_BODY)},
    }}}).encode()


class _Fake(http.server.BaseHTTPRequestHandler):
    """A tiny fake HuggingFace. mode is read from the server: 'good' serves real bytes."""
    def log_message(self, *a): pass

    def do_GET(self):
        body = None
        if self.path.endswith("/voices.json"):
            body = _voices_json()
        elif self.path.endswith(f"{VOICE}.onnx.json"):
            body = _CONFIG_BODY
        elif self.path.endswith(f"{VOICE}.onnx"):
            body = _MODEL_BODY
        if body is None:
            self.send_error(404); return
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _start_server():
    srv = http.server.HTTPServer(("127.0.0.1", 0), _Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def _dead_base():
    """A base URL pointing at a closed port → connection refused = 'no network', fast."""
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    return f"http://127.0.0.1:{port}"


# ---- import the module under test, then reset its module state between scenarios ----
import voice.fetch as fetch  # noqa: E402  (app/voice/fetch.py — via app/ on sys.path)


def _reset(base):
    """Point the engine at `base` and wipe all cached/promoted state + the on-disk voice dir,
    so each scenario starts from a true 'absent' with no leftover voices.json cache."""
    fetch.PIPER_BASE = base.rstrip("/")
    fetch._vjson_cache = None
    with fetch._state_lock:
        fetch._state.clear(); fetch._voice_locks.clear()
    d = fetch._piper_dir()
    for f in os.listdir(d):
        os.remove(os.path.join(d, f))
    fetch._current_voice = lambda: VOICE      # skip the DB; fix the current voice


def _wait_settle(timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        st = fetch._state_for(VOICE)["status"]
        if st in ("ready", "error", "absent"):
            return st
        time.sleep(0.02)
    return "TIMEOUT"


def _no_partial_left():
    """No promoted final AND no leftover temp for an errored voice = nothing corrupt on disk."""
    d = fetch._piper_dir()
    files = os.listdir(d)
    return not any(f.endswith((".onnx", ".onnx.json", ".part")) for f in files), files


results = []
def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""))


srv, good = _start_server()
dead = _dead_base()

print("\nT3 — opportunistic startup, NO NETWORK (the sharp invariant: must not block/raise):")
_reset(dead)
t0 = time.time()
raised = None
try:
    fetch.opportunistic_startup_fetch()          # this is exactly what main.py lifespan calls
except BaseException as e:                         # noqa: BLE001 — the whole point is it must NOT
    raised = repr(e)
elapsed = time.time() - t0
check("returns immediately (does not block boot)", elapsed < 1.0, f"{elapsed*1000:.0f} ms")
check("never raises into startup", raised is None, raised or "clean")
settled = _wait_settle()
check("background fetch settles to 'error' (offline)", settled == "error", f"state={settled}")
clean, files = _no_partial_left()
check("no corrupt/partial voice left on disk", clean, f"dir={files}")

print("\nT1/T2 — endpoints on NO NETWORK (must degrade to error, NEVER 5xx):")
_reset(dead)
r = fetch.voice_ensure()
check("POST /api/voice/ensure returns 200 (not 5xx)", r.status_code == 200, f"status={r.status_code}")
body = json.loads(bytes(r.body))
check("ensure body is a state dict", body.get("status") in ("downloading", "error", "absent"), str(body))
settled = _wait_settle()
check("state converges to 'error'", settled == "error", f"state={settled}")
rs = fetch.voice_state()
check("GET /api/voice/state returns 200 on failure", rs.status_code == 200, f"status={rs.status_code}")

print("\nT2 — re-triggerable after a failure (a dead download never stays dead):")
before = fetch._state_for(VOICE)["status"]
r2 = fetch.voice_ensure()                          # click ⚠ again → must start a NEW attempt
check("second ensure accepted (not stuck)", r2.status_code == 200, f"was={before}")
check("re-attempt reaches 'error' again cleanly", _wait_settle() == "error")

print("\nRecovery — dead → good: retry re-downloads a clean, verified voice:")
fetch.PIPER_BASE = good.rstrip("/")                # network 'comes back'; same absent state
fetch._vjson_cache = None
fetch.voice_ensure()
settled = _wait_settle()
check("state reaches 'ready' when network returns", settled == "ready", f"state={settled}")
check("both final files present + verified", fetch._present(VOICE))
mp, cp = fetch._paths(VOICE)
check("promoted bytes match md5 (not corrupt)",
      _md5(open(mp, "rb").read()) == _md5(_MODEL_BODY) and
      _md5(open(cp, "rb").read()) == _md5(_CONFIG_BODY))

print("\nHappy baseline — fresh good server, absent → ready:")
_reset(good)
st = fetch.ensure_voice(VOICE)
check("ensure_voice → ready", st["status"] == "ready", str(st))

srv.shutdown()
passed = sum(1 for _, ok, _ in results if ok)
print(f"\n==== {passed}/{len(results)} PASS ====")
sys.exit(0 if passed == len(results) else 1)
