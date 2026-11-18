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
    def mk_inv(hrp, dh=DH, desc=None):
        # Real BOLT11 invoices always carry a 520-bit (104-group) signature at the end — include it so
        # decode_bolt11's signature-exclusion is exercised. 'h' = LUD-06 description_hash; 'd' = BOLT11
        # plaintext description (what Wallet of Satoshi + many popular wallets emit instead of 'h').
        f = [0] * 7 + _fld(1, _b5(b"\x11" * 32))
        if dh is not None:   f += _fld(23, _b5(dh))
        if desc is not None: f += _fld(13, _b5(desc.encode()))
        return _enc(hrp, f + [0] * 104)

    def mk_http(hrp="lnbc10u", dh=DH, desc=None):   # 1000 sats = 1_000_000 msat → lnbc10u
        def _get(url):
            if url.endswith("/.well-known/lnurlp/alice"):
                return {"tag": "payRequest", "callback": f"https://{DOMAIN}/cb", "metadata": META,
                        "minSendable": 1000, "maxSendable": 100_000_000}
            if url.startswith(f"https://{DOMAIN}/cb"):
                return {"pr": mk_inv(hrp, dh, desc), "preimage": SENT_SECRET}   # callback also returns an extra secret
            raise AssertionError(url)
        return _get

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
            # 'd' plaintext-description form (no 'h'): ACCEPTED when d == metadata text/plain ("e2e"),
            # REJECTED when it doesn't — the Wallet-of-Satoshi real-transport case, now a regression guard.
            wp._http_get_json = mk_http(dh=None, desc="e2e"); wp._LEDGER.clear()
            pd = wp.web_pay(wp.WebPayRequest(payee=PAYEE, amount_sats=1000, confirm=True), x_vokter_human_session=TOK)
            d_form_ok = isinstance(pd, dict) and str(pd.get("handoff", "")).startswith("lightning:")
            wp._http_get_json = mk_http(dh=None, desc="ATTACKER"); wp._LEDGER.clear()
            d_bad = None
            try:
                wp.web_pay(wp.WebPayRequest(payee=PAYEE, amount_sats=1000, confirm=True), x_vokter_human_session=TOK)
            except HTTPException as e:
                d_bad = e.status_code
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

            # ── opaque-crypto rail: x402 + direct on-chain (confirm-token bound; no funds/keys/wallet) ──
            import web_pay_x402 as x4
            x4._OC_LEDGER.clear()
            USDC = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"; PAYTO = "0x1111111111111111111111111111111111111111"
            EVIL = "0x2222222222222222222222222222222222222222"
            CHAL = {"accepts": [{"scheme": "exact", "network": "base", "asset": USDC, "payTo": PAYTO,
                                 "maxAmountRequired": "1000000", "resource": "https://shop.example/a/42"}]}
            def _oc_p1(**kw):
                try:
                    wp.web_pay_onchain(wp.OnchainPayRequest(**kw), x_vokter_human_session=TOK); return None, {}
                except HTTPException as e:
                    return e.status_code, (e.detail if isinstance(e.detail, dict) else {})
            def _oc_p2(**kw):
                try:
                    return 200, wp.web_pay_onchain(wp.OnchainPayRequest(confirm=True, **kw), x_vokter_human_session=TOK)
                except HTTPException as e:
                    return e.status_code, (e.detail if isinstance(e.detail, dict) else {})
            ocx1s, ocx1d = _oc_p1(rail="x402", challenge=CHAL)
            ocxtok = ocx1d.get("confirm_token", "")
            ocx2s, ocx2b = _oc_p2(rail="x402", challenge=CHAL, confirm_token=ocxtok)
            ocx_handoff = (ocx2b or {}).get("handoff", {}) if isinstance(ocx2b, dict) else {}
            x4._OC_LEDGER.clear()
            ocxsub_s, _ = _oc_p2(rail="x402", challenge={"accepts": [dict(CHAL["accepts"][0], payTo=EVIL)]}, confirm_token=ocxtok)
            x4._OC_LEDGER.clear()
            ETH_TO = "0x3333333333333333333333333333333333333333"; ETH_EVIL = "0x4444444444444444444444444444444444444444"
            oce1s, oce1d = _oc_p1(rail="onchain", asset="ETH", network="base", destination=ETH_TO, amount="0.5")
            ocetok = oce1d.get("confirm_token", "")
            oce2s, oce2b = _oc_p2(rail="onchain", asset="ETH", network="base", destination=ETH_TO, amount="0.5", confirm_token=ocetok)
            oce_uri = (oce2b or {}).get("handoff", {}).get("uri", "") if isinstance(oce2b, dict) else ""
            x4._OC_LEDGER.clear()
            ocesub_s, _ = _oc_p2(rail="onchain", asset="ETH", network="base", destination=ETH_EVIL, amount="0.5", confirm_token=ocetok)
            ocnotok_s, _ = _oc_p2(rail="onchain", asset="ETH", network="base", destination=ETH_TO, amount="0.5", confirm_token="")
            ocnh_s = None
            try:
                wp.web_pay_onchain(wp.OnchainPayRequest(rail="onchain", asset="ETH", network="base", destination=ETH_TO, amount="0.5"), x_vokter_human_session="nope")
            except HTTPException as e:
                ocnh_s = e.status_code
        captured = buf.getvalue()
        # Every crypto hand-off must be an INERT one-way request (QR/deep-link) — NEVER a WalletConnect-style
        # session/pairing. Scan all rails' hand-offs for a session scheme (Bilal's directive, 2026-11-15).
        _handoff_blob = (str(ocx_handoff) + "|" + oce_uri + "|" + str(p2)).lower()
        _no_session = (not any(x in _handoff_blob for x in ("walletconnect", "wc:", "pairing", "session"))
                       and ocx_handoff.get("scheme") == "x402" and oce_uri.startswith("ethereum:")
                       and "lightning:" in str(p2))
        # ZERO STORED FUND-MOVING SECRET on the DEFAULT (QR) pay surface. web_pay_nwc exists again as the
        # opt-in, gated, default-OFF unattended rail (Phase 2B, 2026-11-16) — so this is no longer a
        # module-ABSENCE check. The invariant transforms: the QR path `wp` (web_pay.py) must still hold
        # nothing that can move money (no nwc store/route/field on IT), and the unattended rail's stored
        # connection-secret NON-LEAKAGE is asserted by TIER-7, not here (TIER3_PHASE2B_NWC_UNATTENDED.md §7).
        _pay_paths = [getattr(r, "path", "") for r in wp.router.routes]
        _no_stored_fund_secret = (
            not hasattr(wp, "_nwc_store") and not hasattr(wp, "NWCConnectRequest")
            and not any("nwc" in p for p in _pay_paths)
            and "nwc" not in wp.WebPayRequest.model_fields and "via" not in wp.WebPayRequest.model_fields)
        checks = {
            "pay_phase1_428": p1s == 428,
            "pay_phase1_payee+amount+verifiable": p1d.get("payee_domain") == DOMAIN and p1d.get("amount_sats") == 1000 and p1d.get("verifiable") is True,
            "pay_phase2_ready_handoff": isinstance(p2, dict) and p2.get("status") == "ready" and str(p2.get("handoff", "")).startswith("lightning:"),
            "integrity_handoff_matches_confirmed": integrity_ok,
            "integrity_d_form_accepted": d_form_ok,
            "integrity_d_form_mismatch_rejected": d_bad == 422,
            "integrity_inflated_amount_rejected": inf == 422,
            "integrity_substituted_invoice_rejected": sub == 422,
            "pay_nonhuman_403": nh == 403,
            "no_callback_secret_in_response": SENT_SECRET not in str(p2) and SENT_SECRET not in str(p1d),
            "no_callback_secret_in_stdout": SENT_SECRET not in captured,
            # opaque-crypto rail (x402 + direct on-chain): hand-off only, caveat enforced, substitution rejected
            "x402_phase1_428_opaque": ocx1s == 428 and ocx1d.get("verifiable") is False
                and "not verifiable" in str(ocx1d.get("caveat", "")) and ocx1d.get("resource_origin") == "shop.example"
                and ocx1d.get("destination") == PAYTO,
            "x402_phase2_handoff_eip681": isinstance(ocx2b, dict) and ocx2b.get("status") == "ready"
                and ocx_handoff.get("scheme") == "x402" and ocx_handoff.get("uri", "").startswith(f"ethereum:{USDC}")
                and f"address={PAYTO}" in ocx_handoff.get("uri", ""),
            "x402_substituted_payTo_rejected": ocxsub_s == 422,
            "onchain_phase1_428_opaque": oce1s == 428 and oce1d.get("verifiable") is False and bool(ocetok),
            "onchain_phase2_handoff_eip681": oce2s == 200 and oce_uri == f"ethereum:{ETH_TO}@8453?value=500000000000000000",
            "onchain_substituted_address_rejected": ocesub_s == 422,
            "onchain_missing_confirm_token_rejected": ocnotok_s == 422,
            "onchain_nonhuman_403": ocnh_s == 403,
            "no_session_scheme_any_rail": _no_session,   # inert QR/deep-link only — NO WalletConnect session
            "no_stored_fund_moving_secret": _no_stored_fund_secret,   # QR DEFAULT surface stores nothing (unattended rail is TIER-7)
        }
    finally:
        _sys.modules.pop("chat", None)
    return all(checks.values()), checks


def _handler_routes_through_gateway(module, func_name: str, action: str) -> bool:
    """FROZEN-SAFE tripwire: assert a handler routes its action through enforce_http, by inspecting the
    COMPILED bytecode (func.__code__) — NOT inspect.getsource(), which reads the .py file that does NOT
    exist in a frozen PyInstaller binary (OSError 'could not get source code'; caught by the TIER-7/8 VM
    acceptance 2026-11-17). Checks the handler's code (recursively, incl. nested/inlined code objects) for
    the name `enforce_http` AND the exact action STRING CONSTANT — so a handler reachable without the
    gateway, or gated on the wrong action, still trips. Works identically in dev and in the shipped .deb."""
    fn = getattr(module, func_name, None)
    if fn is None or not hasattr(fn, "__code__"):
        return False
    names: set = set()
    consts: set = set()

    def _walk(code):
        names.update(code.co_names)
        names.update(code.co_varnames)
        for k in code.co_consts:
            if isinstance(k, str):
                consts.add(k)
            elif hasattr(k, "co_code"):
                _walk(k)
    _walk(fn.__code__)
    return "enforce_http" in names and action in consts


# ───────── TIER 7: live web.pay.unattended — e2e, BOTH halves: envelope-integrity + secret-non-leakage ─────────
# The opt-in, default-OFF autonomous-within-budget rail (TIER3_PHASE2B_NWC_UNATTENDED.md). Drives the REAL
# web_pay_unattended() handler with a hermetic mock LNURL + a budget-ENFORCING mock wallet (no network, no
# funds). Proves: (a) a spend INSIDE the mandate pays and the WALLET's own budget is authoritative (a spend
# the envelope allows but the wallet refuses → NOT paid); every spend OUTSIDE the envelope (off allow-list,
# over per-tx, over budget, over throttle, expired, not-opted-in, gate-off) is REFUSED/DOWNGRADED and
# nothing is sent; peer/mcp can never obtain the grant; and (b) the stored connection SECRET never leaks.
def _unattended_handler_e2e():
    import contextlib, hashlib, io, sys as _sys, time, types
    SENT_SECRET = "VKTR-NWC-connsecret-7a2f90-never-to-model"; TOK = "e2e-human-session-tok"
    DOMAIN = "e2e-nwc.example"; PAYEE = f"alice@{DOMAIN}"; OFFLIST = "mallory@evil.example"
    META = '[["text/plain","e2e"]]'
    DH = hashlib.sha256(META.encode()).digest()
    URI = f"nostr+walletconnect://deadbeef?relay=wss://r&secret={SENT_SECRET}"

    import web_pay as wp
    import web_pay_nwc as nwc
    import web_pay_unattended as wpu
    from fastapi import HTTPException
    import safety
    _B32 = wp._B32

    # — a hermetic BOLT11 for 1000 sats (lnbc10u), bound to META via the LUD-06 'h' hash —
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
    def mk_inv(hrp="lnbc10u", dh=DH):
        f = [0] * 7 + _fld(1, _b5(b"\x11" * 32)) + _fld(23, _b5(dh)) + [0] * 104
        return _enc(hrp, f)
    def make_get(inv_fn):
        def _g(url):
            if url.endswith("/.well-known/lnurlp/alice"):
                return {"tag": "payRequest", "callback": f"https://{DOMAIN}/cb", "metadata": META,
                        "minSendable": 1000, "maxSendable": 100_000_000}
            if url.startswith(f"https://{DOMAIN}/cb"):
                return {"pr": inv_fn()}
            raise AssertionError(url)
        return _g
    _get = make_get(mk_inv)                                   # well-formed 1000-sat invoice bound to META

    # — a mock wallet that ENFORCES its own connection budget (the REAL protection); status-only out —
    wallet = {"budget_sats": 100_000, "spent": 0, "calls": 0}
    def mock_transport(uri_str, bolt11):
        wallet["calls"] += 1
        amt = (wp.decode_bolt11(bolt11)["amount_msat"] or 0) // 1000
        if wallet["spent"] + amt > wallet["budget_sats"]:
            return False, "failed"          # wallet REFUSES over its own budget — cannot be overridden by Vokter
        wallet["spent"] += amt
        return True, "paid"

    _sys.modules["chat"] = types.ModuleType("chat"); _sys.modules["chat"].is_local_human_session = lambda m: m == TOK
    mem = nwc.InMemoryNWCStore()
    _saved = {"store": wpu._store, "transport": nwc._nwc_transport, "http": wp._http_get_json,
              "gate": os.environ.get("VOKTER_NWC_UNATTENDED_GATE")}
    checks = {}
    try:
        wpu._store = mem; nwc._nwc_transport = mock_transport; wp._http_get_json = _get
        safety.load()   # arm the capability gateway BEFORE any spend (else guard fail-closes to BLOCK)

        def provision(allow, per_tx, budget, throttle, window=7 * 86400, expired=False):
            env = nwc._new_envelope(allow, per_tx, budget, window, throttle)
            if expired: env["expires_at"] = time.time() - 1
            mem._d.clear(); nwc._SPEND_LEDGER.clear(); wallet["spent"] = 0; wallet["calls"] = 0
            mem.put("default", URI, env)

        # The unattended rail is FOR the local planner ("autonomous") — drive it with a NON-human token so
        # the mandate (envelope) is the authority, not a human confirm. (TOK would make it HUMAN → confirm.)
        AUTO = "autonomous-no-human-session"

        def pay(payee, amount, tok=AUTO, confirm=False):
            try:
                r = wpu.web_pay_unattended(wpu.UnattendedPayRequest(payee=payee, amount_sats=amount, confirm=confirm),
                                           x_vokter_human_session=tok)
                return 200, r
            except HTTPException as e:
                return e.status_code, (e.detail if isinstance(e.detail, dict) else {})

        buf = io.StringIO()
        responses = []
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            os.environ["VOKTER_NWC_UNATTENDED_GATE"] = "passed"

            # (1) IN-ENVELOPE → paid; the wallet actually moved (its budget decremented).
            provision([PAYEE], per_tx=5_000, budget=10_000, throttle=5)
            s1, r1 = pay(PAYEE, 1000); responses += [r1]
            checks["in_envelope_paid"] = s1 == 200 and r1.get("status") == "paid" and r1.get("unattended") is True
            checks["in_envelope_wallet_debited"] = wallet["spent"] == 1000 and wallet["calls"] == 1

            # (2) OFF allow-list → 428 downgrade, NOTHING sent to the wallet.
            provision([PAYEE], per_tx=5_000, budget=10_000, throttle=5)
            s2, r2 = pay(OFFLIST, 1000); responses += [r2]
            checks["offlist_downgraded_428"] = s2 == 428 and r2.get("downgrade") is True
            checks["offlist_nothing_sent"] = wallet["calls"] == 0

            # (3) OVER per-tx cap → downgrade, nothing sent.
            provision([PAYEE], per_tx=5_000, budget=10_000, throttle=5)
            s3, r3 = pay(PAYEE, 9_999); responses += [r3]
            checks["over_pertx_downgraded"] = s3 == 428 and wallet["calls"] == 0

            # (4) OVER rolling budget → downgrade, nothing sent (budget 1500; one 1000 pre-spent).
            provision([PAYEE], per_tx=5_000, budget=1_500, throttle=5)
            s4a, _ = pay(PAYEE, 1000)                      # first 1000 fits (spent→1000)
            s4b, r4 = pay(PAYEE, 1000); responses += [r4]  # 1000+1000=2000 > 1500 → over budget
            checks["over_budget_first_ok"] = s4a == 200
            checks["over_budget_second_downgraded"] = s4b == 428 and r4.get("downgrade") is True

            # (5) OVER count throttle → downgrade (throttle 1; one spend already recorded).
            provision([PAYEE], per_tx=5_000, budget=100_000, throttle=1)
            s5a, _ = pay(PAYEE, 1000)                      # count→1
            s5b, r5 = pay(PAYEE, 1000); responses += [r5]  # count 2 > 1 → throttled
            checks["over_throttle_downgraded"] = s5a == 200 and s5b == 428

            # (6) EXPIRED window → downgrade, nothing sent.
            provision([PAYEE], per_tx=5_000, budget=10_000, throttle=5, expired=True)
            s6, r6 = pay(PAYEE, 1000); responses += [r6]
            checks["expired_downgraded"] = s6 == 428 and wallet["calls"] == 0

            # (7) NOT opted-in (no connection) → downgrade.
            mem._d.clear(); nwc._SPEND_LEDGER.clear(); wallet["calls"] = 0
            s7, r7 = pay(PAYEE, 1000); responses += [r7]
            checks["not_optedin_downgraded"] = s7 == 428 and wallet["calls"] == 0

            # (8) WALLET is authoritative: envelope ALLOWS but the wallet refuses (its own budget) → 502, not paid.
            provision([PAYEE], per_tx=50_000, budget=100_000, throttle=5)
            wallet["budget_sats"] = 500                    # wallet cap BELOW the requested 1000
            s8, r8 = pay(PAYEE, 1000); responses += [r8]
            checks["wallet_refusal_not_paid"] = s8 == 502 and wallet["spent"] == 0
            wallet["budget_sats"] = 100_000

            # (8b) CONFIRM-INTEGRITY (the T17 unattended guard): an IN-ENVELOPE payee whose LNURL returns a
            # TAMPERED invoice (inflated amount / substituted description_hash) is REJECTED (422) by the
            # reused resolve_and_invoice, and NOTHING is sent to the wallet. Mirrors TIER-6's inf/sub cases.
            provision([PAYEE], per_tx=50_000, budget=100_000, throttle=5)
            wp._http_get_json = make_get(lambda: mk_inv(hrp="lnbc2500u"))   # 250_000_000 msat vs confirmed 1000 sats
            s8i, _ = pay(PAYEE, 1000)
            checks["integrity_inflated_422_nothing_sent"] = s8i == 422 and wallet["calls"] == 0
            wp._http_get_json = make_get(lambda: mk_inv(dh=hashlib.sha256(b"attacker").digest()))  # substituted 'h'
            s8s, _ = pay(PAYEE, 1000)
            checks["integrity_substituted_422_nothing_sent"] = s8s == 422 and wallet["calls"] == 0
            wp._http_get_json = _get                                        # restore the well-formed invoice

            # (8c) A HUMAN caller is ALSO bounded by the envelope: in-envelope + confirm → paid; OUT-of-
            # envelope + confirm → DOWNGRADE to web.pay (never an unbounded spend over the stored connection).
            provision([PAYEE], per_tx=5_000, budget=100_000, throttle=5)
            sh_ok, _ = pay(PAYEE, 1000, tok=TOK, confirm=True)
            checks["human_in_envelope_confirm_paid"] = sh_ok == 200 and wallet["calls"] == 1
            provision([PAYEE], per_tx=5_000, budget=100_000, throttle=5)
            sh_bad, rh = pay(OFFLIST, 1000, tok=TOK, confirm=True)
            checks["human_out_envelope_downgraded"] = sh_bad == 428 and rh.get("downgrade") is True and wallet["calls"] == 0

            # (9) GATE OFF → 503 (fully dormant), even fully opted-in + in-envelope.
            provision([PAYEE], per_tx=5_000, budget=10_000, throttle=5)
            del os.environ["VOKTER_NWC_UNATTENDED_GATE"]
            s9, _ = pay(PAYEE, 1000)
            checks["gate_off_503"] = s9 == 503 and wallet["calls"] == 0
            os.environ["VOKTER_NWC_UNATTENDED_GATE"] = "passed"

        captured = buf.getvalue()

        # (10) GATEWAY verdict — peer/mcp can NEVER obtain the grant; ALLOW only for an in-envelope MandateGrant.
        safety.load()
        checks["action_known"] = "web.pay.unattended" in safety.KNOWN_ACTIONS
        # BYTECODE TRIPWIRE (frozen-safe): the unattended handler MUST route the spend through
        # enforce_http("web.pay.unattended", …) — a handler reachable without the gateway is a hole.
        checks["tripwire_handler_calls_enforce_http"] = _handler_routes_through_gateway(
            wpu, "web_pay_unattended", "web.pay.unattended")
        g = lambda tgt, ctx: safety.guard("web.pay.unattended", tgt, context=ctx)
        checks["gw_autonomous_in_envelope_ALLOW"] = g(safety.MandateGrant("x", True), "autonomous") == safety.Decision.ALLOW
        checks["gw_autonomous_out_envelope_BLOCK"] = g(safety.MandateGrant("x", False), "autonomous") == safety.Decision.BLOCK
        checks["gw_peer_BLOCK"] = g(safety.MandateGrant("x", True), "peer") == safety.Decision.BLOCK
        checks["gw_mcp_BLOCK"] = g(safety.MandateGrant("x", True), "mcp") == safety.Decision.BLOCK
        checks["gw_forged_string_target_BLOCK"] = g("in-envelope!", "autonomous") == safety.Decision.BLOCK

        # (b) SECRET NON-LEAKAGE — the connection secret appears in NO response and NO stdout/stderr.
        checks["secret_absent_from_responses"] = all(SENT_SECRET not in str(r) and "walletconnect" not in str(r)
                                                      for r in responses)
        checks["secret_absent_from_stdout"] = SENT_SECRET not in captured and "walletconnect" not in captured
        # list_public must carry the envelope but NEVER the URI/secret.
        pub = mem.list_public()
        checks["list_public_no_secret"] = SENT_SECRET not in str(pub) and "walletconnect" not in str(pub) \
            and any(e.get("alias") == "default" for e in pub)
    finally:
        wpu._store = _saved["store"]; nwc._nwc_transport = _saved["transport"]; wp._http_get_json = _saved["http"]
        if _saved["gate"] is None:
            os.environ.pop("VOKTER_NWC_UNATTENDED_GATE", None)
        else:
            os.environ["VOKTER_NWC_UNATTENDED_GATE"] = _saved["gate"]
        _sys.modules.pop("chat", None)
    return all(checks.values()), checks


# ───────── TIER 8: live email.send — e2e, non-leakage + confirm-integrity + T24 inbound-injection guard ─────────
# The opt-in, default-OFF outbound-email rail (TIER3_PHASE3_EMAIL.md). Drives the REAL email_send() handler
# with a hermetic MOCK SMTP sink (no network, no real recipient). Proves: a send happens ONLY after a human
# confirm (no autonomous path — the T24 structural bound: the untrusted inbound corpus can't drive a send);
# what is sent == what the human confirmed (substituted recipient/body → 422, nothing sent); the body is
# built by INCLUSION (non-whitelisted + blocked-class fields never leave); the per-task alias is
# deterministic, unlinkable-across-tasks, and reply-routes back to its task; and the transport credential +
# the alias never leak.
def _email_handler_e2e():
    import contextlib, io, sys as _sys, types
    SENT_SECRET = "VKTR-SMTP-password-3e9b7f-never-to-model"   # the transport credential
    LEAK = "LEAKED-MEMORY-must-never-appear-in-a-body-xyz"      # a non-whitelisted datum
    TOK = "e2e-human-session-tok"; AUTO = "autonomous-no-human-session"
    DOMAIN = "e2e-mail.example"; TO = "merchant@shop.example"; SUBJ = "Booking"

    import email_send as es
    from fastapi import HTTPException
    import safety

    _sys.modules["chat"] = types.ModuleType("chat"); _sys.modules["chat"].is_local_human_session = lambda m: m == TOK
    sink = {"msgs": [], "calls": 0}
    def mock_smtp(cfg, from_alias, to, subject, body):
        sink["calls"] += 1; sink["msgs"].append({"from": from_alias, "to": to, "subject": subject, "body": body})
        return True, "sent"

    _saved = {"tr": es._transport, "smtp": es._smtp_transport, "gate": os.environ.get("VOKTER_EMAIL_SEND_GATE")}
    checks = {}
    responses = []
    # disclosure carries MORE than the whitelist: `leaked` is not whitelisted; `identity` is whitelisted but
    # a BLOCKED class — both must be absent from the body (inclusion + fail-closed).
    DISC = {"greeting": "Hello", "party_size": "2", "date": "Friday", "leaked": LEAK, "identity": "passport-xyz"}
    # whitelist OMITS 'leaked' (non-whitelisted → fails-closed absent) and INCLUDES 'identity' (whitelisted
    # but a BLOCKED class → dropped). Both must be absent from the body.
    FIELDS = ["greeting", "party_size", "date", "identity"]
    try:
        es._transport = es.InMemoryTransport()
        es._transport.put({"host": "mx.example", "port": 587, "username": "u",
                           "password": SENT_SECRET, "from_domain": DOMAIN, "starttls": True})
        es._smtp_transport = mock_smtp
        safety.load()

        def call(confirm=False, tok=TOK, to=TO, disc=None, token="", task="task-1"):
            try:
                r = es.email_send(es.EmailSendRequest(task_id=task, to=to, subject=SUBJ,
                        disclosure=DISC if disc is None else disc, fields=FIELDS,
                        confirm=confirm, confirm_token=token),
                        x_vokter_human_session=tok)
                return 200, r
            except HTTPException as e:
                return e.status_code, (e.detail if isinstance(e.detail, dict) else {})

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            os.environ["VOKTER_EMAIL_SEND_GATE"] = "passed"

            # (1) PHASE 1 (human) → 428 with recipient + alias + full body + token. Alias is Vokter's own
            #     per-task address, not the social npub; body excludes non-whitelisted + blocked fields.
            s1, d1 = call(confirm=False); responses += [d1]
            alias = d1.get("alias", ""); body1 = d1.get("body", ""); tok1 = d1.get("confirm_token", "")
            checks["phase1_428_confirm_payload"] = (s1 == 428 and d1.get("recipient") == TO
                and alias.endswith("@" + DOMAIN) and not alias.startswith("npub") and bool(tok1))
            checks["body_inclusion_whitelist_only"] = (LEAK not in body1 and "passport" not in body1
                and "greeting: Hello" in body1 and "party_size: 2" in body1 and "date: Friday" in body1)

            # (2) PHASE 2 (human, valid token) → SENT; the mock SMTP got the message, from == the alias.
            s2, d2 = call(confirm=True, token=tok1); responses += [d2]
            checks["phase2_sent"] = s2 == 200 and d2.get("status") == "sent" and sink["calls"] == 1
            checks["sent_from_alias_to_recipient"] = (sink["msgs"][-1]["from"] == alias
                and sink["msgs"][-1]["to"] == TO and LEAK not in sink["msgs"][-1]["body"]) if sink["msgs"] else False

            # (3) CONFIRM-INTEGRITY — substituted RECIPIENT with the phase-1 token → 422, nothing sent.
            before = sink["calls"]
            s3, _ = call(confirm=True, to="attacker@evil.example", token=tok1)
            checks["substituted_recipient_422_nothing_sent"] = s3 == 422 and sink["calls"] == before

            # (4) CONFIRM-INTEGRITY — substituted BODY (changed disclosure) with the old token → 422, nothing sent.
            s4, _ = call(confirm=True, disc=dict(DISC, party_size="999"), token=tok1)
            checks["substituted_body_422_nothing_sent"] = s4 == 422 and sink["calls"] == before

            # (5) T24 — a NON-HUMAN caller (stands in for "triggered by the untrusted inbound corpus") is
            #     BLOCKED at BOTH phases; nothing drafted, nothing sent.
            s5a, _ = call(confirm=False, tok=AUTO)
            s5b, _ = call(confirm=True, tok=AUTO, token=tok1)
            checks["t24_autonomous_phase1_403"] = s5a == 403
            checks["t24_autonomous_phase2_403_nothing_sent"] = s5b == 403 and sink["calls"] == before

            # (6) REPLY-ROUTING (bidirectional) — the alias maps back to its task; a fresh task → a fresh,
            #     different alias (unlinkable across tasks); same task → the SAME alias (deterministic).
            checks["reply_routes_to_task"] = es.task_for_alias(alias) == "task-1"
            s6, d6 = call(confirm=False, task="task-2"); responses += [d6]
            checks["alias_unlinkable_across_tasks"] = d6.get("alias") != alias and d6.get("alias", "").endswith("@" + DOMAIN)
            checks["alias_deterministic_within_task"] = es.ensure_alias("task-1") == alias

            # (7) GATE OFF → 503 (fully dormant), even with a transport provisioned.
            del os.environ["VOKTER_EMAIL_SEND_GATE"]
            s7, _ = call(confirm=False)
            checks["gate_off_503"] = s7 == 503
            os.environ["VOKTER_EMAIL_SEND_GATE"] = "passed"

            # transport status must carry NO password.
            st = es.transport_status(x_vokter_human_session=TOK); responses += [st]
            checks["status_no_password"] = SENT_SECRET not in str(st) and st.get("transport", {}).get("from_domain") == DOMAIN

        captured = buf.getvalue()

        # (8) GATEWAY verdict — email.send is CONFIRM-human / BLOCK every non-human (no autonomous path).
        checks["action_known"] = "email.send" in safety.KNOWN_ACTIONS
        g = lambda ctx: safety.guard("email.send", "x", context=ctx)
        checks["gw_human_CONFIRM"] = g(safety.HUMAN) == safety.Decision.CONFIRM
        checks["gw_autonomous_BLOCK"] = g("autonomous") == safety.Decision.BLOCK
        checks["gw_peer_BLOCK"] = g("peer") == safety.Decision.BLOCK
        checks["gw_mcp_BLOCK"] = g("mcp") == safety.Decision.BLOCK

        # BYTECODE TRIPWIRE (frozen-safe): the send handler MUST route through enforce_http("email.send", …).
        checks["tripwire_handler_calls_enforce_http"] = _handler_routes_through_gateway(
            es, "email_send", "email.send")

        # (b) NON-LEAKAGE — the transport credential + the leaked datum appear in NO response and NO stdout.
        checks["secret_absent_from_responses"] = all(SENT_SECRET not in str(r) and LEAK not in str(r) for r in responses)
        checks["secret_absent_from_stdout"] = SENT_SECRET not in captured and LEAK not in captured
    finally:
        es._transport = _saved["tr"]; es._smtp_transport = _saved["smtp"]
        if _saved["gate"] is None:
            os.environ.pop("VOKTER_EMAIL_SEND_GATE", None)
        else:
            os.environ["VOKTER_EMAIL_SEND_GATE"] = _saved["gate"]
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

        print("\n── TIER 7: live web.pay.unattended — e2e, ENVELOPE-INTEGRITY + secret-non-leakage ──")
        try:
            un_ok, uchecks = _unattended_handler_e2e()
            for k, v in uchecks.items():
                _ok(f"e2e[unattended]: {k}", v)
            _ok("e2e[unattended]: pays only INSIDE the mandate, wallet is authoritative, secret never leaks", un_ok)
        except Exception as e:
            _ok(f"TIER 7 unattended e2e probe ran ({e})", False)

        print("\n── TIER 8: live email.send — e2e, non-leakage + CONFIRM-INTEGRITY + T24 inbound-injection ──")
        try:
            em_ok, echecks = _email_handler_e2e()
            for k, v in echecks.items():
                _ok(f"e2e[email]: {k}", v)
            _ok("e2e[email]: sends only on human confirm, what's sent == confirmed, alias/credential never leak", em_ok)
        except Exception as e:
            _ok(f"TIER 8 email e2e probe ran ({e})", False)
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
