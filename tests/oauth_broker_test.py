"""OAuth/PKCE delegation broker — exercised against a MOCK IdP (sentinel, never a real credential).

    VOKTER_DB=/tmp/x VOKTER_DB_KEY=k desktop/runtime/venv/bin/python tests/oauth_broker_test.py

Proves: full PKCE delegation (system-browser-sim → loopback code → server-side exchange → scoped
token stored), the IdP enforces PKCE (wrong verifier rejected), CSRF state mismatch rejected, the
ship gate refuses a real flow until green, one-action revocation, the API-token sub-shape, and that
the sentinel token is NEVER returned to the caller (status+metadata only)."""
import base64
import hashlib
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import oauth_broker as ob

FAILS = []
SENTINEL_TOKEN = "SENTINEL-ACCESS-TOKEN-6b2f9a-never-to-model"
SENTINEL_CODE = "sentinel-auth-code-xyz"


def check(desc, got, want=True):
    ok = got == want
    print(("PASS " if ok else "FAIL ") + desc + f"  → {got}")
    if not ok:
        FAILS.append(desc)


# ───────────────────────── mock IdP ─────────────────────────
class MockIdP:
    """/authorize → 302 back to the loopback redirect with code+state; /token → verifies PKCE
    (sha256(verifier)==challenge) then issues the sentinel token; /revoke → 200."""
    def __init__(self):
        self._challenge_for = {}   # code → code_challenge (to verify PKCE at /token)
        idp = self

        class _H(BaseHTTPRequestHandler):
            def log_message(self, *a): pass

            def do_GET(self):
                u = urlparse(self.path); q = parse_qs(u.query)
                if u.path == "/authorize":
                    idp._challenge_for[SENTINEL_CODE] = q.get("code_challenge", [""])[0]
                    redirect = q.get("redirect_uri", [""])[0]
                    state = q.get("state", [""])[0]
                    loc = f"{redirect}?code={SENTINEL_CODE}&state={state}"
                    self.send_response(302); self.send_header("Location", loc); self.end_headers()
                else:
                    self.send_response(404); self.end_headers()

            def do_POST(self):
                u = urlparse(self.path)
                n = int(self.headers.get("Content-Length", 0))
                body = parse_qs(self.rfile.read(n).decode())
                if u.path == "/token":
                    verifier = body.get("code_verifier", [""])[0]
                    code = body.get("code", [""])[0]
                    expect = idp._challenge_for.get(code)
                    chal = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
                    if expect and chal == expect:
                        out = b'{"access_token":"%s","token_type":"bearer","scope":"read"}' % SENTINEL_TOKEN.encode()
                        self.send_response(200)
                    else:
                        out = b'{"error":"invalid_grant"}'; self.send_response(400)
                    self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(out)
                elif u.path == "/revoke":
                    self.send_response(200); self.end_headers()
                else:
                    self.send_response(404); self.end_headers()

        self._srv = HTTPServer(("127.0.0.1", 0), _H)
        self.port = self._srv.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"

    def __enter__(self):
        threading.Thread(target=self._srv.serve_forever, daemon=True).start(); return self

    def __exit__(self, *e):
        self._srv.shutdown(); self._srv.server_close()


def browser_sim(url):
    """Stand-in for open_in_system_browser: simulate the user's browser consenting. Returns
    immediately (like the real opener) and does the authorize→redirect→loopback dance in a thread."""
    def go():
        import httpx
        r = httpx.get(url, follow_redirects=False)       # hit /authorize, DON'T follow
        loc = r.headers.get("location")
        httpx.get(loc)                                   # deliver code+state to the loopback receiver
    threading.Thread(target=go, daemon=True).start()


def main():
    # 1) ship gate refuses a real flow until green
    os.environ.pop(ob._GATE_ENV, None)
    try:
        ob.begin_delegation(ob.IdP(authorize="x", token="y", client_id="c", scopes=["read"]),
                            origin="https://site.example", identity="me", store=ob.InMemoryTokenStore())
        check("gate blocks pre-green begin_delegation (raises)", False)
    except ob.GateNotPassed:
        check("gate blocks pre-green begin_delegation (raises)", True)

    # open the gate for the mechanism tests (as the packaged selftest would)
    os.environ[ob._GATE_ENV] = "passed"

    with MockIdP() as idp_srv:
        idp = ob.IdP(authorize=idp_srv.base + "/authorize", token=idp_srv.base + "/token",
                     client_id="vokter-test", scopes=["read"], revoke=idp_srv.base + "/revoke")
        store = ob.InMemoryTokenStore()

        # 2) full happy-path delegation
        res = ob.begin_delegation(idp, origin="https://site.example", identity="me",
                                  store=store, open_url=browser_sim, wait_timeout=15)
        check("delegation succeeded", res.get("status"), "succeeded")
        check("result exposes scopes (for CONFIRM)", res.get("scopes"), ["read"])

        # 3) token stored (scoped) but NEVER returned to the caller/model
        check("token stored under oauth/<origin>/<identity>",
              store.get(ob.token_name("https://site.example", "me")), SENTINEL_TOKEN)
        check("begin_delegation NEVER returns the token", SENTINEL_TOKEN not in str(res))

        # 4) API-token sub-shape sees it; a different identity does not
        check("use_api_token True for provisioned identity",
              ob.use_api_token(store, "https://site.example", "me"), True)
        check("use_api_token False for other identity",
              ob.use_api_token(store, "https://site.example", "other"), False)

        # 5) one-action revocation removes the local token
        check("revoke_delegation removes token",
              ob.revoke_delegation(store, origin="https://site.example", identity="me", idp=idp), True)
        check("token gone after revoke",
              store.get(ob.token_name("https://site.example", "me")), None)

        # 6) PKCE is real: a tampered verifier is rejected by the IdP
        v, c = ob.gen_pkce()
        idp_srv.__class__  # keep ref
        import httpx
        # register a challenge for a code, then exchange with the WRONG verifier
        httpx.get(idp.authorize + f"?code_challenge={c}&redirect_uri=http://127.0.0.1:1/&state=s",
                  follow_redirects=False)
        try:
            ob.exchange_code(idp.token, client_id="vokter-test", redirect_uri="http://127.0.0.1:1/",
                             code=SENTINEL_CODE, verifier="WRONG-" + v)
            check("PKCE rejects a tampered verifier (raises)", False)
        except ob.BrokerError:
            check("PKCE rejects a tampered verifier (raises)", True)

    # 7) CSRF: the loopback receiver rejects a state mismatch
    rx = ob.LoopbackReceiver("expected-state")
    with rx:
        import httpx
        httpx.get(rx.redirect_uri + "?code=abc&state=FORGED")
        try:
            rx.wait(timeout=5)
            check("loopback rejects forged state (raises)", False)
        except ob.BrokerError as e:
            check("loopback rejects forged state (raises)", "state_mismatch" in str(e))

    print("\n" + ("❌ FAILS: " + ", ".join(FAILS) if FAILS else "✅ oauth broker: all guarantees hold"))
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
