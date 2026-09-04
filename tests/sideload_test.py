"""Sovereign sideload sha256 test — the trust boundary on OUR self-hosted GGUFs.

orchestrator._sideload_streaming() is the fresh-install path that provisions a MIRROR model
(the bge-m3 embedder, the Salamandra Catalan model) by downloading its GGUF from Vokter's own
host, verifying a pinned sha256, and creating it in Ollama. The pin is the ONLY thing standing
between "our model" and "whatever a tampered/MITM'd host served," so it must:

  1. CORRECT hash  → proceeds past verify and creates the model in Ollama.
  2. WRONG hash    → die()s loudly and NEVER contacts Ollama (never installs a wrong model).
  3. TRUNCATED     → a short/interrupted download fails the pin → same loud rejection, no install.
  4. leaves no half-written .part behind on rejection.

Runs offline: the GGUF is served from a local `file://` mirror (explicitly supported by the
downloader for exactly this), and Ollama's HTTP API is stubbed so nothing real is contacted.
Run:  desktop/runtime/venv/bin/python tests/sideload_test.py
"""
import hashlib
import os
import sys
import tempfile
import urllib.request

_TMP = tempfile.mkdtemp(prefix="vokter-sideload-")
_MIRROR = os.path.join(_TMP, "mirror")
os.makedirs(_MIRROR, exist_ok=True)
os.environ["VOKTER_MODEL_ASSETS_BASE"] = "file://" + _MIRROR
os.environ["VOKTER_DESKTOP_DATA"] = os.path.join(_TMP, "data")   # isolate DATA_DIR/gguf-cache
os.environ["VOKTER_KEY_SOURCE"] = "file"                         # never touch the keychain

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "desktop"))
import orchestrator  # noqa: E402


def _fail(msg):
    print("FAIL:", msg)
    sys.exit(1)


# --- fixture GGUF on the local mirror ---------------------------------------
GGUF = "bge-m3-Q4_K_M.gguf"
CONTENT = b"GGUF\x00fake-model-weights-" + os.urandom(4096)
with open(os.path.join(_MIRROR, GGUF), "wb") as fh:
    fh.write(CONTENT)
GOOD_SHA = hashlib.sha256(CONTENT).hexdigest()

META_GOOD = {"gguf": GGUF, "sha256": GOOD_SHA, "size": len(CONTENT), "template": "{{ .Prompt }}"}
META_WRONG = {**META_GOOD, "sha256": "0" * 64}   # host served something else

# --- stub die() (loud exit) and emit_progress (noise); intercept Ollama HTTP -------------
class DieCalled(Exception):
    pass

orchestrator.die = lambda msg: (_ for _ in ()).throw(DieCalled(msg))
orchestrator.emit_progress = lambda *a, **k: None

_real_urlopen = urllib.request.urlopen
_ollama_calls = []          # (METHOD, path) actually sent to the Ollama API

class _FakeResp:
    def __init__(self, status=200, lines=()):
        self.status = status
        self._lines = list(lines)
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def read(self, n=-1): return b""
    def __iter__(self): return iter(self._lines)

def _fake_urlopen(req, *a, **k):
    url = req.full_url if isinstance(req, urllib.request.Request) else req
    method = req.get_method() if isinstance(req, urllib.request.Request) else "GET"
    if url.startswith("file://"):
        return _real_urlopen(req, *a, **k)          # REAL download + REAL hashing
    # everything else is the Ollama API → record + fake
    path = url.split("://", 1)[-1].split("/", 1)[-1]
    _ollama_calls.append((method, "/" + path))
    if "/api/blobs/" in url and method == "HEAD":
        return _FakeResp(status=404)                # not present → triggers push
    if "/api/blobs/" in url and method == "POST":
        return _FakeResp(status=201)                # blob accepted
    if url.endswith("/api/create"):
        return _FakeResp(status=200, lines=[b'{"status":"success"}'])
    return _FakeResp(status=200)

urllib.request.urlopen = _fake_urlopen

CACHE = orchestrator.DATA_DIR / "gguf-cache"
def _part_left():
    return (CACHE / (GGUF + ".part")).exists()


# ============================================================================
# 1. CORRECT hash → creates the model in Ollama
# ============================================================================
_ollama_calls.clear()
try:
    orchestrator._sideload_streaming("bge-m3", META_GOOD, index=1, count=1)
except DieCalled as e:
    _fail(f"correct hash was rejected: {e}")
created = [c for c in _ollama_calls if c == ("POST", "/api/create")]
if not created:
    _fail(f"correct hash did not create the model in Ollama (calls: {_ollama_calls})")
if _part_left():
    _fail("temp .part not cleaned up after a successful install")
print(f"1. correct hash OK — verified + created in Ollama (calls: {[c[1] for c in _ollama_calls]})")

# ============================================================================
# 2. WRONG hash → die() and NEVER touches Ollama
# ============================================================================
_ollama_calls.clear()
raised = None
try:
    orchestrator._sideload_streaming("bge-m3", META_WRONG, index=1, count=1)
except DieCalled as e:
    raised = str(e)
if raised is None:
    _fail("wrong hash was NOT rejected — a tampered model would install")
if "checksum" not in raised.lower() and "mismatch" not in raised.lower():
    _fail(f"wrong hash rejected, but not for the checksum reason: {raised!r}")
if _ollama_calls:
    _fail(f"wrong hash still contacted Ollama — could install a wrong model: {_ollama_calls}")
if _part_left():
    _fail("temp .part left behind after checksum rejection")
print(f"2. wrong hash OK — died loudly ({raised!r}), zero Ollama calls, .part removed")

# ============================================================================
# 3. TRUNCATED download → pin fails → same loud rejection, no install
# ============================================================================
with open(os.path.join(_MIRROR, GGUF), "wb") as fh:
    fh.write(CONTENT[: len(CONTENT) // 2])          # only half the bytes arrive
_ollama_calls.clear()
raised = None
try:
    orchestrator._sideload_streaming("bge-m3", META_GOOD, index=1, count=1)  # still expects full sha
except DieCalled as e:
    raised = str(e)
if raised is None:
    _fail("truncated download was NOT rejected")
if _ollama_calls:
    _fail(f"truncated download still contacted Ollama: {_ollama_calls}")
if _part_left():
    _fail("temp .part left behind after truncated-download rejection")
print(f"3. truncated OK — died loudly ({raised!r}), zero Ollama calls, .part removed")

print("\nOK — sideload: a pinned sha256 gates the sovereign GGUF — correct hash installs, "
      "wrong or truncated content is rejected loudly and never reaches Ollama.")
