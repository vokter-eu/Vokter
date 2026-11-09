"""Tier-3 Phase 1 — interactive-browser CONTAINMENT self-test (frozen into the backend).

This is the SHIPPED verification of the cage: it exercises the SHIPPED egress_argv/jail_argv
(interactive_browser) against the PACKAGED binaries (bundled browser + pasta, system bwrap + nft)
and the installed AppArmor profiles. A freshly-installed .deb proves itself with:

    vokter-backend --containment-selftest     →  all 3 tiers GREEN

tests/browser_containment_fire.py is a thin dev wrapper over run(). See
vokter-internal/TIER3_PHASE1_BROWSER_CONTAINMENT.md for the architecture + packaging.

Three tiers (every probe must FAIL to reach its target):
  1. filesystem/mount-ns cage (standalone bwrap fs cage): host files/secrets hidden.
  2. egress-only (the LOAD-BEARING control): public reachable YET host/loopback/LAN/RFC1918/
     metadata/127.0.0.1 all blocked — via pasta --no-map-gw + nft drop-private in pasta's userns.
  3. renderer sandbox + hostile page-JS over a CDP pipe: chrome sandboxed, page reaches public but
     NOTHING host/internal.
"""
import json
import os
import subprocess
import sys
import time

import interactive_browser as ib

GATEWAY = "10.0.2.2"                      # slirp/pasta gateway (host-loopback path)
DNS_REBIND = "192-168-1-1.nip.io"         # public name → a private (router) address
_JPY = "/usr/bin/python3" if os.path.exists("/usr/bin/python3") else "python3"

# The attack probe, as a standalone stdlib script run INSIDE the cage via the system python under
# the /usr bind (the frozen binary is not bound into the jail). Same netns/mount-ns the browser
# gets, so what it can reach == what a compromised renderer can reach.
_PROBE_SRC = r'''
import socket, json, sys, os
G="10.0.2.2"; RB="192-168-1-1.nip.io"
vport=int(sys.argv[1]); hostlan=sys.argv[2]
def tcp(h,p,t=3.0):
    try: socket.create_connection((h,p),timeout=t).close(); return "REACHED"
    except Exception: return "blocked"
net={
 "host-loopback via gateway %s:%d"%(G,vport): tcp(G,vport),
 "ollama via gateway %s:11434"%G: tcp(G,11434),
 "host LAN %s:%d"%(hostlan,vport): tcp(hostlan,vport),
 "router 192.168.1.1:80": tcp("192.168.1.1",80),
 "rfc1918 10.0.0.1:80": tcp("10.0.0.1",80),
 "rfc1918 172.16.0.1:80": tcp("172.16.0.1",80),
 "cloud-metadata 169.254.169.254:80": tcp("169.254.169.254",80),
 "sandbox-own-loopback 127.0.0.1:%d"%vport: tcp("127.0.0.1",vport),
 "dns-rebind %s:80"%RB: tcp(RB,80),
 "public egress 1.1.1.1:443": tcp("1.1.1.1",443),
}
fs={}
for p in (os.path.expanduser("~/.config"), os.path.expanduser("~/.bashrc"), "/etc/shadow",
          "/run/user/%d/bus"%os.getuid()):
    try:
        (os.listdir(p) if os.path.isdir(p) else open(p,"rb").read(1)); fs[p]="READABLE"
    except Exception: fs[p]="blocked"
print(json.dumps({"net":net,"fs":fs}))
'''

FAILS, SKIPS = [], []
def _ok(desc, cond):
    print(("PASS " if cond else "FAIL ") + desc)
    if not cond:
        FAILS.append(desc)
def _skip(desc, why):
    print(f"SKIP {desc}  — {why}"); SKIPS.append(desc)


def _host_lan_ip():
    try:
        import re
        out = subprocess.check_output(["ip", "route", "get", "1.1.1.1"], text=True)
        m = re.search(r"src (\d+\.\d+\.\d+\.\d+)", out)
        return m.group(1) if m else "127.0.0.1"
    except Exception:
        return "127.0.0.1"


def _free_port():
    import socket
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


def _write_probe():
    import tempfile
    f = tempfile.NamedTemporaryFile("w", suffix="_probe.py", delete=False)
    f.write(_PROBE_SRC); f.close()
    return f.name


def _run_probe(jailed, vport, hostlan, probe_path):
    if jailed:
        argv = ib.jail_argv([_JPY, "/probe.py", str(vport), str(hostlan)],
                            ro_binds=[(probe_path, "/probe.py")], unshare_net=True)
    else:
        argv = [_JPY, probe_path, str(vport), str(hostlan)]
    out = subprocess.check_output(argv, text=True, stderr=subprocess.DEVNULL, env=ib.clean_env())
    return json.loads(out.strip().splitlines()[-1])


def _egress_probe(vport, hostlan, probe_path):
    argv = ib.egress_argv([_JPY, "/probe.py", str(vport), str(hostlan)],
                          ro_binds=[(probe_path, "/probe.py")])
    out = subprocess.run(argv, capture_output=True, text=True, timeout=60, env=ib.clean_env())
    lines = [l for l in out.stdout.splitlines() if l.strip().startswith("{")]
    if not lines:
        raise RuntimeError("egress cage produced no output: " + (out.stderr.strip()[-200:] or "none"))
    return json.loads(lines[-1])


def _hostile_page(vport, hostlan):
    import urllib.parse
    js = ("window.__p={};window.__done=false;"
          "function f(n,u){return fetch(u,{mode:'no-cors',signal:AbortSignal.timeout(8000)})"
          ".then(()=>{window.__p[n]='REACHED'}).catch(()=>{window.__p[n]='blocked'});}"
          "Promise.allSettled(["
          f"f('host-loopback via gateway','http://{GATEWAY}:{vport}/'),"
          f"f('host LAN','http://{hostlan}:{vport}/'),"
          "f('router','http://192.168.1.1/'),"
          "f('cloud-metadata','http://169.254.169.254/'),"
          f"f('ollama via gateway','http://{GATEWAY}:11434/'),"
          f"f('dns-rebind','http://{DNS_REBIND}:{vport}/'),"
          "f('public','http://1.1.1.1/')"
          "]).then(()=>{window.__done=true});")
    return "data:text/html," + urllib.parse.quote(f"<!doctype html><script>{js}</script>")


def _run_hostile_page(browser, vport, hostlan):
    bdir = os.path.dirname(browser)
    ro = [(bdir, bdir)] + [(p, p) for p in ib._BROWSER_RO_BINDS if os.path.exists(p)]
    chrome = [browser, "--remote-debugging-pipe", "--headless=new", "--disable-gpu",
              "--disable-dev-shm-usage", "--no-first-run", "--user-data-dir=/tmp/ud"]
    argv = ib.egress_argv(chrome, ro_binds=ro)
    b_read, p_write = os.pipe(); p_read, b_write = os.pipe(); os.set_blocking(p_read, False)
    import tempfile
    errf = tempfile.TemporaryFile()

    def _pre():
        os.dup2(b_read, 3); os.dup2(b_write, 4)
    proc = subprocess.Popen(argv, preexec_fn=_pre, env=ib.clean_env(),
                            pass_fds=(3, 4, b_read, b_write), stderr=errf)
    os.close(b_read); os.close(b_write)
    try:
        cdp = ib._CDP(p_read, p_write)
        tid = cdp.wait(cdp.send("Target.createTarget", {"url": _hostile_page(vport, hostlan)}))["result"]["targetId"]
        sid = cdp.wait(cdp.send("Target.attachToTarget", {"targetId": tid, "flatten": True}))["result"]["sessionId"]
        end = time.time() + 40
        while time.time() < end:
            if cdp.eval("window.__done===true", sid) is True:
                break
            time.sleep(0.5)
        return json.loads(cdp.eval("JSON.stringify(window.__p)", sid))
    except Exception as e:
        errf.seek(0); err = errf.read().decode(errors="replace")
        raise RuntimeError(f"chrome/CDP failed: {e}; stderr: {err.strip()[-200:]}")
    finally:
        proc.kill()


# ───────────────────────── TIER 4: secret-non-leakage (Phase-1 login) ─────────────────────────
# The ship gate for the Tier-3 login SECRET path (TIER3_PHASE1_LOGIN.md §6). Two halves:
#   * POSITIVE (delivery): a sentinel, typed by the REAL login keystroke path (interactive_browser.
#     type_secret) into a data: form inside the FROZEN cage, actually reaches the field (read back ==
#     sentinel). Without this, the negative half could false-green on a silent no-op.
#   * NEGATIVE (non-leakage): the FULL perform_password_login routine leaks a (different) sentinel into
#     NONE of — its return, a throwaway DB's memory/chunks/conversation tables, or captured stdout/
#     stderr — and the model-facing web.login request shape carries no secret field. Driving the whole
#     routine (not just type_secret) makes this a real regression guard: a future log/persist/return of
#     the secret fails the build.
# Distinct sentinels so the two halves cannot cross-contaminate. NEITHER value is ever printed.
_SENT_POS = "VKTR-POS-3f9c1a7e5b2d8046-reached-the-field"
_SENT_NEG = "VKTR-NEG-a1b2c3d4e5f60718-must-not-leak"


def _login_delivery_positive(browser) -> bool:
    """Real cage: type the sentinel into a data: form via type_secret, read the field back == sentinel.
    Proves the keystroke physically crosses the frozen cage. Never prints the sentinel."""
    import urllib.parse
    s = ib.launch()                                          # real contained browser (egress cage)
    try:
        s.navigate("data:text/html," + urllib.parse.quote("<input id=f>"))
        status = ib.type_secret(s, "#f", _SENT_POS)         # the login-only keystroke path
        got = s._t._cdp.eval("document.querySelector('#f').value", s._t._sid)  # read-back (white-box)
        return status == "succeeded" and got == _SENT_POS
    finally:
        try:
            s._t.close()
        except Exception:
            pass


def _login_leakage_negative() -> tuple[bool, dict]:
    """Full routine (fake transport reporting the registered https origin + a sentinel in an in-memory
    store) leaks the sentinel into none of: its return, the throwaway DB tables, captured stdout/stderr;
    and the web.login request shape carries no secret field. Returns (ok, detail-without-secret)."""
    import contextlib
    import io
    import sys as _sys
    import tempfile

    reg = "https://sentinel-bank.example"
    os.environ["VOKTER_DB"] = os.path.join(tempfile.mkdtemp(prefix="vkt_neg_"), "probe.db")
    if getattr(_sys, "frozen", False) and not os.environ.get("VOKTER_DB_KEY"):
        import secrets
        os.environ["VOKTER_DB_KEY"] = "tier4-throwaway-" + secrets.token_hex(8)  # sqlcipher bundled in .deb
    os.environ["VOKTER_TIER3_SECRET_GATE"] = "passed"       # open the gate for the probe (like the broker test)

    import db                                                # imports config → reads the env set above
    import web_login_injector as wli
    from web_login import WebLoginRequest

    class _FakeT:                                           # reports the resolved origin; records nothing
        def top_origin(self): return reg
        def type_into(self, sel, val): return True
        def click(self, sel): return True
        def navigate(self, u): return True
        def read_text(self): return ""

    conn = db.get_db()                                      # create schema so the table scan is REAL, not theater
    store = wli.InMemoryCredentialStore(); store.put(reg, "me", _SENT_NEG)
    sels = wli.LoginSelectors(username="#u", password="#p", submit="#s")

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        ret = wli.perform_password_login(ib.Session(_FakeT()), store, registered_origin=reg,
                                         identity="me", selectors=sels, username="alice")
    captured = buf.getvalue()

    db_hit = False
    for tbl in ("memory", "chunks", "conversations"):
        try:
            if any(_SENT_NEG in str(cell)
                   for row in conn.execute(f"SELECT * FROM {tbl}").fetchall() for cell in row):
                db_hit = True
        except Exception:
            pass
    conn.close()

    secret_fields = {"value", "password", "secret", "token", "otp", "pan", "cvv"}
    shape_clean = not (set(WebLoginRequest.model_fields) & secret_fields)

    ok = (ret in ("succeeded", "failed") and _SENT_NEG not in ret
          and _SENT_NEG not in captured and not db_hit and shape_clean)
    return ok, {"routine_status": ret, "db_leak": db_hit,
                "stdout_leak": (_SENT_NEG in captured), "request_shape_clean": shape_clean}


# ───────── TIER 5: live two-phase web.login handler — e2e secret-non-leakage (hermetic) ─────────
# Drives the REAL web_login() HTTP handler through BOTH phases — "not just the isolated routine" — with
# a hermetic transport (no cage): the real normalize_origin/origin_matches run against a transport-
# reported https origin. The physical keystroke-through-the-cage is already proven by the TIER-4 positive
# probe; this proves the WIRING leaks nothing and actually works end to end.
def _login_handler_e2e():
    """Returns (ok, checks). Exercises, under captured stdout/stderr + a throwaway DB:
      * phase 1 (human, no confirm) → enriched 428 (resolved_origin + identity + mode; NO secret),
      * phase 2 (human, confirm)    → 'succeeded' AND the sentinel WAS typed into the password field
                                       (positive functional — catches a silent no-op / 501 regression),
      * wrong resolved origin       → refused ('failed'), nothing typed (origin gate in the live path),
      * non-human phase 1           → 403 BEFORE any navigation (no cage opened),
      * the sentinel leaks into NONE of: the 428 body, the return, stdout/stderr, the DB tables, or the
        gateway-recorded target. (SSE is structurally N/A — web.login is a POST, not streamed.)"""
    import contextlib, io, sys as _sys, tempfile, types
    SENT = "VKTR-E2E-5d1c8b3f9a27-must-not-leak"; REG = "https://e2e-bank.example"; TOK = "e2e-human-session-tok"

    os.environ.setdefault("VOKTER_DB", os.path.join(tempfile.mkdtemp(prefix="vkt_e2e_"), "probe.db"))
    if getattr(_sys, "frozen", False) and not os.environ.get("VOKTER_DB_KEY"):
        import secrets; os.environ["VOKTER_DB_KEY"] = "tier5-throwaway-" + secrets.token_hex(8)
    os.environ["VOKTER_TIER3_SECRET_GATE"] = "passed"

    import db
    from fastapi import HTTPException
    from interactive_browser import Session
    import interactive_browser as ibmod
    import web_login as wl
    from web_login_injector import InMemoryCredentialStore

    class _FakeT:   # hermetic transport: reports a chosen top origin; records keystrokes
        def __init__(self, origin): self._o = origin; self.typed = []; self.clicked = []
        def navigate(self, u): return True
        def top_origin(self): return self._o
        def type_into(self, sel, val): self.typed.append((sel, val)); return True
        def click(self, sel): self.clicked.append(sel); return True
        def read_text(self): return ""

    opened = []
    def _factory(origin): t = _FakeT(origin); opened.append(t); return Session(t)
    store = InMemoryCredentialStore(); store.put(REG, "e2e-user", SENT)
    wl._open_login_session = lambda: _factory(REG)
    wl._credential_store = lambda: store
    ibmod.browser_supported = lambda: True
    wl._SITE_REGISTRY["__e2e__"] = {"login_url": "https://e2e-bank.example/login", "registered_origin": REG,
                                    "mode": "password", "selectors": {"username": "#u", "password": "#p", "submit": "#s"}}
    _sys.modules["chat"] = types.ModuleType("chat"); _sys.modules["chat"].is_local_human_session = lambda m: m == TOK
    try:
        db.get_db().close()   # create schema so the DB scan is real

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            p1_status = None; p1_detail = {}
            try:
                wl.web_login(wl.WebLoginRequest(site="__e2e__", identity_ref="e2e-user"), x_vokter_human_session=TOK)
            except HTTPException as e:
                p1_status = e.status_code; p1_detail = e.detail if isinstance(e.detail, dict) else {"d": str(e.detail)}
            opened.clear()
            p2 = wl.web_login(wl.WebLoginRequest(site="__e2e__", identity_ref="e2e-user", confirm=True), x_vokter_human_session=TOK)
            typed_ph2 = opened[-1].typed if opened else []
            wl._open_login_session = lambda: _factory("https://evil.example")   # wrong origin
            opened.clear()
            p3 = wl.web_login(wl.WebLoginRequest(site="__e2e__", identity_ref="e2e-user", confirm=True), x_vokter_human_session=TOK)
            typed_wrong = opened[-1].typed if opened else []
            wl._open_login_session = lambda: _factory(REG)
            opened.clear()
            nh_status = None
            try:
                wl.web_login(wl.WebLoginRequest(site="__e2e__", identity_ref="e2e-user"), x_vokter_human_session="not-the-token")
            except HTTPException as e:
                nh_status = e.status_code
            nh_opened = len(opened)
        captured = buf.getvalue()

        db_hit = False
        try:
            conn = db.get_db()
            for tbl in ("memory", "chunks", "conversations"):
                try:
                    if any(SENT in str(c) for row in conn.execute(f"SELECT * FROM {tbl}").fetchall() for c in row): db_hit = True
                except Exception: pass
            conn.close()
        except Exception: pass
    finally:
        wl._SITE_REGISTRY.pop("__e2e__", None); _sys.modules.pop("chat", None)

    checks = {
        "phase1_428": p1_status == 428,
        "phase1_resolved_origin": p1_detail.get("resolved_origin") == REG,
        "phase1_identity+mode": p1_detail.get("identity") == "e2e-user" and p1_detail.get("mode") == "password",
        "phase1_428_no_secret": SENT not in str(p1_detail),
        "phase2_succeeded": isinstance(p2, dict) and p2.get("status") == "succeeded",
        "phase2_secret_actually_typed": ("#p", SENT) in typed_ph2,
        "phase2_return_no_secret": SENT not in str(p2),
        "wrong_origin_refused": isinstance(p3, dict) and p3.get("status") == "failed",
        "wrong_origin_not_typed": typed_wrong == [],
        "nonhuman_403": nh_status == 403,
        "nonhuman_no_navigation": nh_opened == 0,
        "no_secret_in_stdout": SENT not in captured,
        "no_secret_in_db": not db_hit,
        "no_secret_in_gateway_target": SENT not in "__e2e__ as e2e-user",
    }
    # the /login menu exposes ONLY site/mode/provider — never endpoints/client_id/selectors/origins.
    try:
        menu = wl.web_login_sites()
        checks["sites_menu_shape_only"] = all(set(e.keys()) <= {"site", "mode", "provider"} for e in menu)
        checks["sites_menu_no_config"] = not any(
            any(bad in str(e) for bad in ("client_id", "authorize", "/token", "selectors", "login_url", "#login_field", "registered_origin"))
            for e in menu)
    except Exception:
        checks["sites_menu_shape_only"] = False
    return all(checks.values()), checks


def _oauth_handler_e2e():
    """Drive the REAL web_login() handler through BOTH phases for OAuth (Path 1), hermetically: a MockIdP
    + a browser_sim stand in for the user's system browser + the IdP (loopback is in the TRUSTED backend,
    never the cage). Asserts phase1→428 {mode:oauth, provider, scopes, NO token}; phase2→'succeeded' with
    the scoped token STORED but the token sentinel NEVER in the 428 / return / stdout; an unconfigured
    entry (no client_id)→503; non-human→403."""
    import base64, contextlib, hashlib, io, sys as _sys, threading, types
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from urllib.parse import urlparse, parse_qs
    SENT_TOK = "VKTR-OAUTH-e4a91f7c2b8d-token-never-to-model"; SCODE = "oauth-e2e-code-xyz"
    REG = "https://e2e-idp.example"; TOK = "e2e-human-session-tok"
    os.environ["VOKTER_TIER3_SECRET_GATE"] = "passed"

    from fastapi import HTTPException
    import web_login as wl
    from oauth_broker import InMemoryTokenStore, token_name

    class _IdP:
        def __init__(self):
            self._chal = {}; idp = self
            class _H(BaseHTTPRequestHandler):
                def log_message(self, *a): pass
                def do_GET(self):
                    u = urlparse(self.path); q = parse_qs(u.query)
                    if u.path == "/authorize":
                        idp._chal[SCODE] = q.get("code_challenge", [""])[0]
                        loc = f'{q.get("redirect_uri", [""])[0]}?code={SCODE}&state={q.get("state", [""])[0]}'
                        self.send_response(302); self.send_header("Location", loc); self.end_headers()
                    else:
                        self.send_response(404); self.end_headers()
                def do_POST(self):
                    n = int(self.headers.get("Content-Length", 0)); b = parse_qs(self.rfile.read(n).decode())
                    if urlparse(self.path).path == "/token":
                        v = b.get("code_verifier", [""])[0]; code = b.get("code", [""])[0]
                        chal = base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).decode().rstrip("=")
                        if idp._chal.get(code) == chal:
                            out = b'{"access_token":"%s","token_type":"bearer","scope":"read:user"}' % SENT_TOK.encode(); self.send_response(200)
                        else:
                            out = b'{"error":"invalid_grant"}'; self.send_response(400)
                        self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(out)
                    else:
                        self.send_response(404); self.end_headers()
            self._srv = HTTPServer(("127.0.0.1", 0), _H); self.base = f"http://127.0.0.1:{self._srv.server_address[1]}"
        def __enter__(self): threading.Thread(target=self._srv.serve_forever, daemon=True).start(); return self
        def __exit__(self, *e): self._srv.shutdown(); self._srv.server_close()

    def browser_sim(url):
        def go():
            import httpx
            loc = httpx.get(url, follow_redirects=False).headers.get("location"); httpx.get(loc)
        threading.Thread(target=go, daemon=True).start()

    store = InMemoryTokenStore()
    _sys.modules["chat"] = types.ModuleType("chat"); _sys.modules["chat"].is_local_human_session = lambda m: m == TOK
    wl._token_store = lambda: store
    wl._open_system_browser = browser_sim
    checks = {}
    try:
        with _IdP() as idp:
            wl._SITE_REGISTRY["__e2e_oauth__"] = {"mode": "oauth", "provider": "E2E-IdP", "registered_origin": REG,
                "authorize": idp.base + "/authorize", "token": idp.base + "/token", "revoke": None,
                "client_id": "e2e-client", "scopes": ["read:user"]}
            wl._SITE_REGISTRY["__e2e_oauth_unconf__"] = {"mode": "oauth", "provider": "E2E", "registered_origin": REG,
                "authorize": idp.base + "/authorize", "token": idp.base + "/token", "revoke": None,
                "client_id": "", "scopes": ["read:user"]}
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                p1s = None; p1d = {}
                try:
                    wl.web_login(wl.WebLoginRequest(site="__e2e_oauth__", identity_ref="e2e-user"), x_vokter_human_session=TOK)
                except HTTPException as e:
                    p1s = e.status_code; p1d = e.detail if isinstance(e.detail, dict) else {}
                p2 = wl.web_login(wl.WebLoginRequest(site="__e2e_oauth__", identity_ref="e2e-user", confirm=True), x_vokter_human_session=TOK)
                uns = None
                try:
                    wl.web_login(wl.WebLoginRequest(site="__e2e_oauth_unconf__", identity_ref="e2e-user", confirm=True), x_vokter_human_session=TOK)
                except HTTPException as e:
                    uns = e.status_code
                nhs = None
                try:
                    wl.web_login(wl.WebLoginRequest(site="__e2e_oauth__", identity_ref="e2e-user"), x_vokter_human_session="nope")
                except HTTPException as e:
                    nhs = e.status_code
            captured = buf.getvalue()
            stored_tok = store.get(token_name(REG, "e2e-user"))
        checks = {
            "oauth_phase1_428": p1s == 428,
            "oauth_phase1_mode+provider+scopes": p1d.get("mode") == "oauth" and p1d.get("provider") == "E2E-IdP" and p1d.get("scopes") == ["read:user"],
            "oauth_phase1_no_token": SENT_TOK not in str(p1d),
            "oauth_phase2_succeeded": isinstance(p2, dict) and p2.get("status") == "succeeded",
            "oauth_token_stored_scoped": stored_tok == SENT_TOK,
            "oauth_phase2_return_no_token": SENT_TOK not in str(p2),
            "oauth_unconfigured_503": uns == 503,
            "oauth_nonhuman_403": nhs == 403,
            "oauth_no_token_in_stdout": SENT_TOK not in captured,
        }
    finally:
        wl._SITE_REGISTRY.pop("__e2e_oauth__", None); wl._SITE_REGISTRY.pop("__e2e_oauth_unconf__", None); _sys.modules.pop("chat", None)
    return all(checks.values()), checks


# ───────── TIER 6: live web.pay (LNURL) — e2e, BOTH halves: non-leakage + confirm-integrity ─────────
# Drives the REAL web_pay() handler both phases with a hermetic mock LNURL (no network, no funds). The
# crypto-critical half is CONFIRM-INTEGRITY: a substituted/inflated invoice is REJECTED and never handed
# off. Non-leakage here = the handler passes through only the public invoice, never an extra secret the
# callback returns, and Vokter holds no key/funds.
def _pay_handler_e2e():
    import contextlib, hashlib, io, sys as _sys, types
    SENT_SECRET = "VKTR-PAY-preimage-9f3c1a-never-to-model"; TOK = "e2e-human-session-tok"
    DOMAIN = "e2e-pay.example"; PAYEE = f"alice@{DOMAIN}"; META = '[["text/plain","e2e"]]'
    DH = hashlib.sha256(META.encode()).digest()
    os.environ["VOKTER_TIER3_SECRET_GATE"] = "passed"

    from fastapi import HTTPException
    import web_pay as wp
    _B32 = wp._B32

    def _poly(vs):
        G = [0x3b6a57b2, 0x26508e6d, 0x1ea119fa, 0x3d4233dd, 0x2a1462b3]; c = 1
        for v in vs:
            b = c >> 25; c = ((c & 0x1ffffff) << 5) ^ v
            for i in range(5): c ^= G[i] if ((b >> i) & 1) else 0
        return c
    def _enc(hrp, data):
        pm = _poly([ord(x) >> 5 for x in hrp] + [0] + [ord(x) & 31 for x in hrp] + data + [0] * 6) ^ 1
        return hrp + "1" + "".join(_B32[d] for d in data + [(pm >> 5 * (5 - i)) & 31 for i in range(6)])
    def _b5(b):
        acc = bits = 0; o = []
        for x in b:
            acc = (acc << 8) | x; bits += 8
            while bits >= 5: bits -= 5; o.append((acc >> bits) & 31)
        if bits: o.append((acc << (5 - bits)) & 31)
        return o
    def _fld(t, v): return [t, len(v) // 32, len(v) % 32] + v
    def mk_inv(hrp, dh): return _enc(hrp, [0] * 7 + _fld(1, _b5(b"\x11" * 32)) + _fld(23, _b5(dh)))

    def mk_http(hrp="lnbc10u", dh=DH):   # 1000 sats = 1_000_000 msat → lnbc10u
        def _get(url):
            if url.endswith("/.well-known/lnurlp/alice"):
                return {"tag": "payRequest", "callback": f"https://{DOMAIN}/cb", "metadata": META,
                        "minSendable": 1000, "maxSendable": 100_000_000}
            if url.startswith(f"https://{DOMAIN}/cb"):
                return {"pr": mk_inv(hrp, dh), "preimage": SENT_SECRET}   # callback also returns an extra secret
            raise AssertionError(url)
        return _get
    def _hrp(sats):
        m = sats * 1000
        return f"lnbc{m // 100000}u" if m % 100000 == 0 else f"lnbc{m // 100}n"
    def mk_http_amt(sats):   # a correct (integrity-passing) invoice for exactly `sats`
        def _get(url):
            if url.endswith("/.well-known/lnurlp/alice"):
                return {"tag": "payRequest", "callback": f"https://{DOMAIN}/cb", "metadata": META,
                        "minSendable": 1000, "maxSendable": 1_000_000_000}
            if url.startswith(f"https://{DOMAIN}/cb"):
                return {"pr": mk_inv(_hrp(sats), DH)}
            raise AssertionError(url)
        return _get

    # NWC capped-session rail: a sentinel connection + a mock wallet enforcing a 5000-sat budget.
    NWC_SECRET = "VKTR-NWC-secret-never-to-model-7a2f"
    NWC_URI = f"nostr+walletconnect://deadbeef?relay=wss://r&secret={NWC_SECRET}"
    import web_pay_nwc as wpn
    from web_pay_nwc import InMemoryNWCStore
    nstore = InMemoryNWCStore(); nstore.put("default", NWC_URI, 0)
    wp._nwc_store = lambda: nstore
    def mock_nwc_transport(uri, bolt11):   # the WALLET enforces its budget; we get status only
        amt = wp.decode_bolt11(bolt11)["amount_msat"] or 0
        return (True, "paid") if amt <= 5_000_000 else (False, "failed")
    wpn._nwc_transport = mock_nwc_transport

    _sys.modules["chat"] = types.ModuleType("chat"); _sys.modules["chat"].is_local_human_session = lambda m: m == TOK
    checks = {}
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            wp._http_get_json = mk_http(); wp._LEDGER.clear()
            p1s = None; p1d = {}
            try:
                wp.web_pay(wp.WebPayRequest(payee=PAYEE, amount_sats=1000), x_vokter_human_session=TOK)
            except HTTPException as e:
                p1s = e.status_code; p1d = e.detail if isinstance(e.detail, dict) else {}
            wp._LEDGER.clear()
            p2 = wp.web_pay(wp.WebPayRequest(payee=PAYEE, amount_sats=1000, confirm=True), x_vokter_human_session=TOK)
            handoff = (p2 or {}).get("handoff", ""); integrity_ok = False
            if handoff.startswith("lightning:"):
                d = wp.decode_bolt11(handoff[len("lightning:"):])
                integrity_ok = d["amount_msat"] == 1_000_000 and d["description_hash"] == DH.hex()
            wp._http_get_json = mk_http(hrp="lnbc2500u"); wp._LEDGER.clear()   # inflated: 250_000_000 msat
            inf = None
            try:
                wp.web_pay(wp.WebPayRequest(payee=PAYEE, amount_sats=1000, confirm=True), x_vokter_human_session=TOK)
            except HTTPException as e:
                inf = e.status_code
            wp._http_get_json = mk_http(dh=hashlib.sha256(b"attacker").digest()); wp._LEDGER.clear()  # substituted
            sub = None
            try:
                wp.web_pay(wp.WebPayRequest(payee=PAYEE, amount_sats=1000, confirm=True), x_vokter_human_session=TOK)
            except HTTPException as e:
                sub = e.status_code
            wp._http_get_json = mk_http()
            nh = None
            try:
                wp.web_pay(wp.WebPayRequest(payee=PAYEE, amount_sats=1000), x_vokter_human_session="nope")
            except HTTPException as e:
                nh = e.status_code

            # ── NWC capped-session rail ──
            wp._http_get_json = mk_http_amt(1000); wp._LEDGER.clear()
            nnp1s = None; nnp1d = {}
            try:
                wp.web_pay(wp.WebPayRequest(payee=PAYEE, amount_sats=1000, via="nwc"), x_vokter_human_session=TOK)
            except HTTPException as e:
                nnp1s = e.status_code; nnp1d = e.detail if isinstance(e.detail, dict) else {}
            wp._LEDGER.clear()
            nnp2 = wp.web_pay(wp.WebPayRequest(payee=PAYEE, amount_sats=1000, via="nwc", confirm=True), x_vokter_human_session=TOK)
            wp._http_get_json = mk_http_amt(6000); wp._LEDGER.clear()   # 6000 sats > 5000 wallet budget
            nnover = wp.web_pay(wp.WebPayRequest(payee=PAYEE, amount_sats=6000, via="nwc", confirm=True), x_vokter_human_session=TOK)
            wp._http_get_json = mk_http(dh=hashlib.sha256(b"attacker2").digest()); wp._LEDGER.clear()  # substituted
            nnsub = None
            try:
                wp.web_pay(wp.WebPayRequest(payee=PAYEE, amount_sats=1000, via="nwc", confirm=True), x_vokter_human_session=TOK)
            except HTTPException as e:
                nnsub = e.status_code
            nstore.delete("default"); wp._http_get_json = mk_http_amt(1000)   # revoke → no connection
            nn404 = None
            try:
                wp.web_pay(wp.WebPayRequest(payee=PAYEE, amount_sats=1000, via="nwc"), x_vokter_human_session=TOK)
            except HTTPException as e:
                nn404 = e.status_code
        captured = buf.getvalue()
        _nwc_responses = str(nnp1d) + str(nnp2) + str(nnover)
        checks = {
            "pay_phase1_428": p1s == 428,
            "pay_phase1_payee+amount+verifiable": p1d.get("payee_domain") == DOMAIN and p1d.get("amount_sats") == 1000 and p1d.get("verifiable") is True,
            "pay_phase2_ready_handoff": isinstance(p2, dict) and p2.get("status") == "ready" and str(p2.get("handoff", "")).startswith("lightning:"),
            "integrity_handoff_matches_confirmed": integrity_ok,
            "integrity_inflated_amount_rejected": inf == 422,
            "integrity_substituted_invoice_rejected": sub == 422,
            "pay_nonhuman_403": nh == 403,
            "no_callback_secret_in_response": SENT_SECRET not in str(p2) and SENT_SECRET not in str(p1d),
            "no_callback_secret_in_stdout": SENT_SECRET not in captured,
            "nwc_phase1_428_via": nnp1s == 428 and nnp1d.get("via") == "nwc",
            "nwc_phase2_paid": isinstance(nnp2, dict) and nnp2.get("status") == "paid" and nnp2.get("via") == "nwc",
            "nwc_over_budget_failed": isinstance(nnover, dict) and nnover.get("status") == "failed",
            "nwc_substituted_rejected": nnsub == 422,
            "nwc_no_connection_404": nn404 == 404,
            "nwc_connection_secret_absent": NWC_SECRET not in _nwc_responses and NWC_SECRET not in captured,
        }
    finally:
        _sys.modules.pop("chat", None)
    return all(checks.values()), checks


def run() -> int:
    """Run all tiers. Returns 0 only if every tier is green."""
    FAILS.clear(); SKIPS.clear()
    if not (ib.bwrap_bin() and ib.pasta_bin() and ib.nft_bin()):
        print("FAIL: containment tooling missing (need bwrap + pasta + nft)"); return 1
    hostlan = _host_lan_ip(); vport = _free_port()
    srv = subprocess.Popen([_JPY, "-m", "http.server", str(vport), "--bind", "0.0.0.0"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(0.8)
    probe_path = _write_probe()
    try:
        print("\n── TIER 1: filesystem/mount-ns cage ──")
        red = _run_probe(False, vport, hostlan, probe_path)
        green = _run_probe(True, vport, hostlan, probe_path)
        _ok("RED reaches host LAN (danger real)", red["net"][f"host LAN {hostlan}:{vport}"] == "REACHED")
        # ~/.bashrc exists on any normal user (from /etc/skel); ~/.config may not on a fresh user.
        _ok("RED reads home dotfiles (danger real)", red["fs"][os.path.expanduser("~/.bashrc")] == "READABLE")
        for k, v in green["net"].items():
            _ok(f"GREEN net blocked: {k}", v == "blocked")
        for k, v in green["fs"].items():
            _ok(f"GREEN fs blocked: {k}", v == "blocked")

        print("\n── TIER 2: egress-only (LOAD-BEARING) ──")
        try:
            g = _egress_probe(vport, hostlan, probe_path)
            print("  egress-cage net:", json.dumps(g["net"]))
            _ok("public egress REACHABLE", g["net"]["public egress 1.1.1.1:443"] == "REACHED")
            for k, v in g["net"].items():
                if not k.startswith("public"):
                    _ok(f"egress blocked: {k}", v == "blocked")
        except Exception as e:
            _ok(f"TIER 2 egress cage ran ({e})", False)

        print("\n── TIER 3: renderer sandbox + hostile page-JS ──")
        _ok("--no-sandbox absent from the cage", "--no-sandbox" not in ib.jail_argv(["x"]))
        browser = ib.browser_binary()
        if not browser:
            _ok("browser present", False)
        else:
            try:
                page = _run_hostile_page(browser, vport, hostlan)
                print("  page-JS reached:", json.dumps(page))
                _ok("page-JS public REACHABLE", page.get("public") == "REACHED")
                for name in ("host-loopback via gateway", "host LAN", "router", "cloud-metadata",
                             "ollama via gateway", "dns-rebind"):
                    _ok(f"page-JS blocked: {name}", page.get(name) == "blocked")
            except Exception as e:
                _ok(f"TIER 3 chrome page-JS ran ({e})", False)

        print("\n── TIER 4: secret-non-leakage (Phase-1 login) ──")
        # NEGATIVE first (no browser needed): the full routine must not leak the sentinel anywhere.
        try:
            neg_ok, detail = _login_leakage_negative()
            print("  negative probe:", json.dumps(detail))   # detail carries NO sentinel
            _ok("negative: routine returns only a status (no sentinel in the return)",
                detail["routine_status"] in ("succeeded", "failed"))
            _ok("negative: no sentinel in memory/chunks/conversation tables", not detail["db_leak"])
            _ok("negative: no sentinel in backend stdout/stderr", not detail["stdout_leak"])
            _ok("negative: web.login request shape carries no secret field", detail["request_shape_clean"])
            _ok("negative: secret-non-leakage holds overall", neg_ok)
        except Exception as e:
            _ok(f"TIER 4 negative probe ran ({e})", False)
        # POSITIVE (real cage): the keystroke actually delivers the sentinel to the field.
        if not browser:
            _ok("positive: browser present", False)
        else:
            try:
                _ok("positive: sentinel reached the field through the frozen cage (read-back matches)",
                    _login_delivery_positive(browser))
            except Exception as e:
                _ok(f"TIER 4 positive probe ran ({e})", False)

        print("\n── TIER 5: live two-phase web.login handler — e2e secret-non-leakage (hermetic) ──")
        try:
            e2e_ok, checks = _login_handler_e2e()
            for k, v in checks.items():
                _ok(f"e2e[password]: {k}", v)
            _ok("e2e[password]: live two-phase web.login leaks nothing + works end to end", e2e_ok)
        except Exception as e:
            _ok(f"TIER 5 password e2e probe ran ({e})", False)
        try:
            oa_ok, ochecks = _oauth_handler_e2e()
            for k, v in ochecks.items():
                _ok(f"e2e[oauth]: {k}", v)
            _ok("e2e[oauth]: live OAuth two-phase works + token never reaches the model", oa_ok)
        except Exception as e:
            _ok(f"TIER 5 oauth e2e probe ran ({e})", False)

        print("\n── TIER 6: live web.pay (LNURL) — e2e, non-leakage + CONFIRM-INTEGRITY ──")
        try:
            pay_ok, pchecks = _pay_handler_e2e()
            for k, v in pchecks.items():
                _ok(f"e2e[pay]: {k}", v)
            _ok("e2e[pay]: live web.pay hands off a confirmed invoice, rejects substitution, no key/funds", pay_ok)
        except Exception as e:
            _ok(f"TIER 6 pay e2e probe ran ({e})", False)
    finally:
        srv.terminate()
        try:
            os.unlink(probe_path)
        except OSError:
            pass
    print()
    if FAILS:
        print(f"❌ CONTAINMENT FAILED: {FAILS}"); return 1
    print("✅ CONTAINMENT FULLY VERIFIED — all tiers green."); return 0


if __name__ == "__main__":
    sys.exit(run())
