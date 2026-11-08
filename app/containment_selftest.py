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
