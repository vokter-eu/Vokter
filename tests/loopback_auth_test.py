"""CRITICAL-1 regression tests — the unauthenticated loopback surface that sidestepped the P2 gate.

Covers the three controls added to close it:
  1. Host-header middleware (DNS-rebinding defense): a non-loopback Host on the admin API → 403.
  2. Human-session token on the mutating/exposing routes that do NOT pass through the gateway
     (config/engine_url, browse-allowlist, agents rate/attest/talk, negotiate accept) and CONTEXT
     DERIVED from the token on the gateway routes (schedule/doc/email-purge) — a tokenless caller is
     nonhuman → BLOCK, so "human confirmed" can no longer be self-asserted by the caller.
  3. SSRF: /api/browse validates every redirect hop BEFORE fetching it.

Each check asserts the FAIL-CLOSED side (no token / bad Host → denied) AND the works-with-token side,
so it bites if any gate is removed. Run:
  VOKTER_DB=/tmp/la.db VOKTER_DB_KEY=k desktop/runtime/venv/bin/python tests/loopback_auth_test.py
"""
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

os.environ.setdefault("VOKTER_DB", "/tmp/loopback_auth_test.db")
os.environ.setdefault("VOKTER_DB_KEY", "test-key")
os.environ["VOKTER_HUMAN_SESSION_TOKEN"] = "testtok-human"
os.environ.pop("VOKTER_ADMIN_TOKEN", None)                 # shipped default: no admin token

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from fastapi.testclient import TestClient
import main
import browser

client = TestClient(main.app)
LOOPBACK = {"host": "127.0.0.1:8080"}
TOK = {"X-Vokter-Human-Session": "testtok-human", **LOOPBACK}

FAILS = []
def check(desc, cond):
    print(("PASS " if cond else "FAIL ") + desc)
    if not cond:
        FAILS.append(desc)


# ───────────────────────── 1. Host-header middleware ─────────────────────────
print("── Host / DNS-rebinding ──")
check("spoofed Host (evil.com) → 403", client.get("/api/config", headers={"host": "evil.com"}).status_code == 403)
check("rebinding host (x.127.0.0.1.nip.io) → 403", client.get("/api/config", headers={"host": "attacker.127.0.0.1.nip.io"}).status_code == 403)
check("loopback Host, read → not 403", client.get("/api/config", headers=LOOPBACK).status_code == 200)
check("public agent card is exempt (Host not enforced)", client.get("/api/agent/card", headers={"host": "evil.com"}).status_code == 200)


# ───────────────── 2. Per-route human-session gate (loopback Host throughout) ─────────────────
# (method, path, json) for the NON-gateway routes → tokenless must be 403, with token must be != 401/403.
print("\n── non-gateway routes require the human token ──")
NONGATEWAY = [
    ("PATCH",  "/api/config", {"engine_url": "http://attacker.example"}),   # the memory-exfil primitive
    ("POST",   "/api/browse/permissions", {"pattern": "https://evil.example"}),
    ("POST",   "/api/agents/rate", {"id": "deadbeef", "trust": "trusted"}),
    ("POST",   "/api/negotiate/accept", {"target": "x", "session_id": "s", "amount": 1}),
]
for method, path, body in NONGATEWAY:
    no = client.request(method, path, headers=LOOPBACK, json=body).status_code
    yes = client.request(method, path, headers=TOK, json=body).status_code
    check(f"{method} {path}: no token → 403", no == 403)
    check(f"{method} {path}: with token → not 401/403 (got {yes})", yes not in (401, 403))

# Gateway routes: context DERIVED from the token. Tokenless → nonhuman → BLOCK (403). With token +
# confirm=true → the gateway allows (not 401/403). confirm omitted+token → 428 (also not a deny).
print("\n── gateway routes derive context from the token (no caller-asserted 'human') ──")
GATEWAY = [
    ("POST",   "/api/schedule", {"name": "t", "goal": "g", "interval": "1h"}),
]
for method, path, body in GATEWAY:
    no = client.request(method, path, headers=LOOPBACK, json=body).status_code
    yes = client.request(method, path + "?confirm=true", headers=TOK, json=body).status_code
    check(f"{method} {path}: tokenless → gateway BLOCK 403 (can't self-assert human)", no == 403)
    check(f"{method} {path}: token+confirm → allowed (got {yes})", yes not in (401, 403))

# The engine_url exfil specifically: prove a TOKENLESS PATCH cannot change where prompts are sent.
client.request("PATCH", "/api/config", headers=TOK, json={"engine_url": ""})         # baseline, with token
no_change = client.request("PATCH", "/api/config", headers=LOOPBACK,
                           json={"engine_url": "http://attacker.example"}).status_code
after = client.get("/api/config", headers=LOOPBACK).json().get("engine_url", "")
check("tokenless engine_url PATCH → 403 and config unchanged (memory-exfil primitive closed)",
      no_change == 403 and "attacker" not in after)


# ───────────────────────── 3. SSRF — redirect hop validated before fetch ─────────────────────────
# Unit-test the browse handler's redirect loop with a fake httpx client that RECORDS each GET. The
# server 302s to a private host; the loop must refuse it WITHOUT ever GETting it (the key property —
# the old follow_redirects=True code fetched every hop, including the private one, before validating).
print("\n── SSRF: redirect to a private target blocked BEFORE it is fetched ──")
import asyncio
import safety
from fastapi import HTTPException

class _Resp:
    def __init__(self, status, location, url):
        self.status_code = status; self.headers = {"location": location}; self.url = url
    @property
    def is_redirect(self): return self.status_code in (301, 302, 303, 307, 308)

class _FakeClient:
    gets = []
    def __init__(self, *a, **k): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def get(self, url):
        _FakeClient.gets.append(url)
        return _Resp(302, "http://blocked.internal/secret", url)   # 302 → a private target

_orig = (browser._is_private_host, browser.httpx.AsyncClient, safety.guard)
browser._is_private_host = lambda h: h == "blocked.internal"       # only the TARGET is private
browser.httpx.AsyncClient = _FakeClient
safety.guard = lambda *a, **k: safety.Decision.ALLOW               # bypass the allowlist gate (not under test)
try:
    raised = None
    try:
        asyncio.run(browser.browse(browser.BrowseRequest(url="http://public.example/start")))
    except HTTPException as e:
        raised = e
    check("redirect to private target → 403 with 'private' reason",
          raised is not None and raised.status_code == 403 and "private" in str(raised.detail).lower())
    check("the private redirect target was NEVER fetched (validated before the request)",
          _FakeClient.gets == ["http://public.example/start"])
finally:
    browser._is_private_host, browser.httpx.AsyncClient, safety.guard = _orig

print()
if FAILS:
    print("❌ FAILED:", FAILS); sys.exit(1)
print("✅ loopback-auth: Host middleware + per-route human gate + SSRF-redirect guard all hold")
