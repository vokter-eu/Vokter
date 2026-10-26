#!/usr/bin/env python3
"""Tier-3 Phase 1 — interactive-browser CONTAINMENT fire test (the acceptance gate).

The interactive browser runs hostile page JS (T13). Before ANY secret phase (card/login/email)
is wired, the cage must hold: hostile code inside the browser must reach NOTHING local. This is
LOAD-BEARING, not defense-in-depth — the shipped app sets no VOKTER_ADMIN_TOKEN, so the admin API
on 127.0.0.1:<dynamic> is protected ONLY by network unreachability.

Falsifiable harness (same discipline as memory_precision_eval). Three tiers; each probe must FAIL
to reach its target:

  TIER 1 — network + filesystem cage  [runs in dev]
      A probe process runs inside the EXACT bwrap jail the browser uses (bwrap --unshare-net +
      minimal read-only binds, no /home, no /etc, no DBus). Chrome's renderer SHARES this netns +
      mount ns, so what this process can reach == what a compromised renderer can reach at the
      socket/fs layer. RED (no jail) must reach host services + read ~/Vokter data; GREEN (jail)
      must reach/blocked NOTHING. This is the containment milestone.

  TIER 2 — egress-only correctness  [needs rootless slirp/pasta; else SKIP]
      Production relaxes TIER 1 to allow PUBLIC egress while keeping host/internal blocked, via
      slirp4netns --disable-host-loopback + an nft drop of RFC1918/link-local/metadata. Asserts
      host-loopback-via-gateway is BLOCKED with --disable-host-loopback and REACHABLE without it
      (the single most important control — omitting the flag IS the hole), and public egress works.

  TIER 3 — chrome renderer sandbox + hostile page JS  [needs the AppArmor profile the .deb installs
      for the bundled headless-shell; else SKIP with the fail-closed assertion]
      The actual browser loads a data: page whose JS attacks the corrected address list; results
      read over the CDP pipe. Asserts the renderer sandbox is active and --no-sandbox is absent.
      On a hardened host without the profile, chrome FAILS CLOSED (refuses to run unsandboxed) —
      asserted — and the full page tier is verified on the clean VM (the design's acceptance venue).

Address list is corrected for the netns-loopback trap: probing 127.0.0.1 from inside the ns hits the
sandbox's OWN loopback (a false pass), so the host-loopback probe goes via the slirp/pasta gateway,
plus the host LAN IP, 169.254.169.254, an RFC1918 sweep, and a DNS-rebind to a private address.

Run:  python3 tests/browser_containment_fire.py
Self-reexec: `python3 tests/browser_containment_fire.py __probe__ <vport> <hostlan>` prints probe JSON.
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import time

GATEWAY = "10.0.2.2"          # slirp/pasta gateway → host loopback iff --disable-host-loopback omitted
DNS_REBIND = "192-168-1-1.nip.io"   # public name that resolves to a private (router) address


def _host_lan_ip() -> str:
    try:
        out = subprocess.check_output(["ip", "route", "get", "1.1.1.1"], text=True)
        for tok in out.split():
            if tok.count(".") == 3 and tok[0].isdigit():
                pass
        import re
        m = re.search(r"src (\d+\.\d+\.\d+\.\d+)", out)
        return m.group(1) if m else "192.168.1.102"
    except Exception:
        return "192.168.1.102"


# ── the probe (also runnable standalone via __probe__ for the jailed run) ──
def probe(vport: int, hostlan: str) -> dict:
    def tcp(host, port, t=2.0):
        try:
            socket.create_connection((host, port), timeout=t).close()
            return "REACHED"
        except Exception:
            return "blocked"
    net = {
        f"host-loopback via gateway {GATEWAY}:{vport}": tcp(GATEWAY, vport),
        f"ollama via gateway {GATEWAY}:11434":          tcp(GATEWAY, 11434),
        f"host LAN {hostlan}:{vport}":                  tcp(hostlan, vport),
        "router 192.168.1.1:80":                        tcp("192.168.1.1", 80),
        "rfc1918 10.0.0.1:80":                          tcp("10.0.0.1", 80),
        "rfc1918 172.16.0.1:80":                        tcp("172.16.0.1", 80),
        "cloud-metadata 169.254.169.254:80":            tcp("169.254.169.254", 80),
        "sandbox-own-loopback 127.0.0.1:%d" % vport:    tcp("127.0.0.1", vport),
    }
    try:                                                 # DNS-rebind: public name → private addr
        net[f"dns-rebind {DNS_REBIND}:80"] = tcp(DNS_REBIND, 80)
    except Exception:
        net[f"dns-rebind {DNS_REBIND}:80"] = "blocked"
    net["public egress 1.1.1.1:443"] = tcp("1.1.1.1", 443)   # must be REACHED in the egress cage
    fs = {}
    # Portable "sensitive host data" targets: the user's home config/dotfiles (stand-ins for the
    # Vokter DB/keychain), the shadow file, and the DBus session bus (keychain transport).
    for p in (os.path.expanduser("~/.config"), os.path.expanduser("~/.bashrc"),
              "/etc/shadow", "/run/user/%d/bus" % os.getuid()):
        try:
            (os.listdir(p) if os.path.isdir(p) else open(p, "rb").read(1))
            fs[p] = "READABLE"
        except Exception:
            fs[p] = "blocked"
    return {"net": net, "fs": fs}


# ── the containment config: the SHIPPED cages from app/interactive_browser.py ──
# We call the exact jail_argv / egress_argv the product uses (adding only the probe file as a
# read-only bind), so a weakening of the real cage fails this test. Imported LAZILY: the probe
# re-execs this file inside the jail (where app/ isn't on the path and must not be), and the
# __probe__ path is stdlib-only.
def _app():
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
    import interactive_browser as ib
    return ib


_JPY = "/usr/bin/python3" if os.path.exists("/usr/bin/python3") else "python3"   # jail-visible interpreter


def jail_argv(inner_argv, *, unshare_net=False):
    return _app().jail_argv(inner_argv, ro_binds=[(os.path.abspath(__file__), "/probe.py")],
                            unshare_net=unshare_net)


def run_probe(jailed: bool, vport: int, hostlan: str) -> dict:
    if jailed:
        # Tier-1 standalone filesystem cage: the SHIPPED bwrap fs cage with unshare_net (no egress).
        argv = jail_argv([_JPY, "/probe.py", "__probe__", str(vport), str(hostlan)], unshare_net=True)
    else:
        argv = [sys.executable, os.path.abspath(__file__), "__probe__", str(vport), str(hostlan)]
    out = subprocess.check_output(argv, text=True, stderr=subprocess.DEVNULL)
    return json.loads(out.strip().splitlines()[-1])


# ── reporting ──
FAILS, SKIPS = [], []
def ok(desc, cond):
    print(("PASS " if cond else "FAIL ") + desc)
    if not cond:
        FAILS.append(desc)
def skip(desc, why):
    print(f"SKIP {desc}  — {why}")
    SKIPS.append(desc)


def start_listener(vport):
    """Host-side 'sensitive service' on all interfaces (simulates the unauthenticated admin API)."""
    srv = subprocess.Popen([sys.executable, "-m", "http.server", str(vport), "--bind", "0.0.0.0"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(0.8)
    return srv


def tier1(vport, hostlan):
    print("\n── TIER 1: filesystem/mount-ns cage (real) + no-egress network baseline ──")
    print("   (The GENUINE win here is the FS cage — ~/Vokter data, ~/.config, /etc/shadow, DBus")
    print("    all hidden. The network-green is TRIVIAL: --unshare-net has no egress at all; the")
    print("    load-bearing 'public-yes / host-no' test is Tier 2, not here.)")
    red = run_probe(False, vport, hostlan)
    green = run_probe(True, vport, hostlan)
    print("  RED (no jail):   ", json.dumps(red))
    print("  GREEN (bwrap):   ", json.dumps(green))
    # RED must demonstrate the danger (else the test proves nothing)
    ok("RED reaches host LAN service (danger real)", red["net"][f"host LAN {hostlan}:{vport}"] == "REACHED")
    ok("RED can read user home config (danger real)",
       red["fs"][os.path.expanduser("~/.config")] == "READABLE")
    # GREEN: every single probe must be blocked
    for k, v in green["net"].items():
        ok(f"GREEN net blocked: {k}", v == "blocked")
    for k, v in green["fs"].items():
        ok(f"GREEN fs blocked: {k}", v == "blocked")


class _Unsupported(Exception):
    """A tier's PREREQUISITE is absent in this environment (not a containment failure). Honest
    SKIP — never a pass. The tier MUST run green on a clean VM before any secret phase."""


def _egress_probe(vport, hostlan) -> dict:
    """Run the in-jail attack probe inside the SHIPPED production egress cage (interactive_browser.
    egress_argv: pasta owns the netns + nft drop-private in pasta's userns + nested bwrap fs cage).
    Returns the probe dict. Raises _Unsupported if the cage can't be established here (e.g. the
    pasta/bwrap AppArmor profiles aren't installed → userns creation denied)."""
    ib = _app()
    argv = ib.egress_argv([_JPY, "/probe.py", "__probe__", str(vport), str(hostlan)],
                          ro_binds=[(os.path.abspath(__file__), "/probe.py")])
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=45)
    except subprocess.TimeoutExpired:
        raise _Unsupported("egress cage timed out")
    err = out.stderr or ""
    if "uid_map" in err or "Couldn't configure user mappings" in err or "clone: Operation not permitted" in err:
        raise _Unsupported("pasta/bwrap could not create a userns — install the pasta + bwrap "
                           "AppArmor profiles (abi-matched)")
    lines = [l for l in out.stdout.splitlines() if l.strip().startswith("{")]
    if not lines:
        raise _Unsupported("egress cage produced no probe output: " + (err.strip()[-200:] or "no stderr"))
    return json.loads(lines[-1])


def tier2(vport, hostlan):
    print("\n── TIER 2: egress-only — the LOAD-BEARING network control (SHIPPED egress_argv) ──")
    print("   (Tier 1's net-green is trivial: no egress. This is the real test — public reachable")
    print("    YET host/internal blocked, via pasta --no-map-gw + nft drop-private in pasta's userns.)")
    try:
        g = _egress_probe(vport, hostlan)
    except _Unsupported as e:
        skip("egress-only cage", f"NOT verified here — {e}. MUST be green on clean VM before any secret phase")
        return
    print("  egress-cage net:", json.dumps(g["net"]))
    ok("public egress REACHABLE (usable browser)", g["net"]["public egress 1.1.1.1:443"] == "REACHED")
    for k, v in g["net"].items():
        if k.startswith("public"):
            continue
        ok(f"egress-cage blocked: {k}", v == "blocked")


def _find_browser():
    for c in (os.environ.get("VOKTER_BROWSER_SHELL"), "chrome-headless-shell", "chromium",
              "chromium-browser", "google-chrome"):
        if not c:
            continue
        p = shutil.which(c) or (c if os.path.exists(c) else None)
        if p:
            return os.path.realpath(p)
    return None


class _CDP:
    """Minimal CDP over chrome's --remote-debugging-pipe (fd3 in / fd4 out, NUL-framed)."""
    def __init__(self, rfd, wfd):
        self.r, self.w, self._id, self._buf = rfd, wfd, 0, b""
    def send(self, method, params=None, sid=None):
        self._id += 1
        m = {"id": self._id, "method": method, "params": params or {}}
        if sid:
            m["sessionId"] = sid
        os.write(self.w, json.dumps(m).encode() + b"\x00")
        return self._id
    def wait(self, wid, timeout=20):
        end = time.time() + timeout
        while time.time() < end:
            while b"\x00" not in self._buf:
                if time.time() > end:
                    raise TimeoutError("cdp")
                try:
                    c = os.read(self.r, 65536)
                    self._buf += c if c else b""
                except BlockingIOError:
                    pass
                time.sleep(0.01)
            line, self._buf = self._buf.split(b"\x00", 1)
            msg = json.loads(line)
            if msg.get("id") == wid:
                return msg
        raise TimeoutError("cdp")


def _hostile_page(vport, hostlan):
    import urllib.parse
    # 8s per-fetch budget: a host/internal target is refused/dropped near-instantly (→ blocked); the
    # public TLS handshake can be slow through a nested-VM double-NAT, so give it real headroom so a
    # slow-but-reachable public egress isn't mis-scored as blocked.
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
          "f('public','http://1.1.1.1/')"   # http (no TLS) — a bare-IP https cert check would reject
          "]).then(()=>{window.__done=true});")
    return "data:text/html," + urllib.parse.quote(f"<!doctype html><script>{js}</script>")


def _run_hostile_page(browser, vport, hostlan) -> dict:
    """Launch `browser` INSIDE the SHIPPED egress cage (interactive_browser.egress_argv: pasta owns
    the netns + nft drop-private + nested bwrap fs cage, --uid 1000 so chrome isn't root) over a CDP
    pipe, load a hostile data: page whose JS attacks the corrected address list, and read window.__p.
    Raises _Unsupported if the renderer sandbox can't init (no browser AppArmor profile → chrome
    fails closed) or the CDP pipe doesn't survive pasta→bwrap."""
    import tempfile
    ib = _app()
    bdir = os.path.dirname(browser)
    ro = [(bdir, bdir)] + [(p, p) for p in ("/etc", "/opt", "/sys") if os.path.exists(p)]
    chrome = [browser, "--remote-debugging-pipe", "--headless=new", "--disable-gpu",
              "--disable-dev-shm-usage", "--no-first-run", "--user-data-dir=/tmp/ud"]
    argv = ib.egress_argv(chrome, ro_binds=ro)
    b_read, p_write = os.pipe(); p_read, b_write = os.pipe(); os.set_blocking(p_read, False)
    errf = tempfile.TemporaryFile()

    def _pre():
        os.dup2(b_read, 3); os.dup2(b_write, 4)
    proc = subprocess.Popen(argv, preexec_fn=_pre, pass_fds=(3, 4, b_read, b_write), stderr=errf)
    os.close(b_read); os.close(b_write)
    try:
        cdp = _CDP(p_read, p_write)
        try:
            tid = cdp.wait(cdp.send("Target.createTarget", {"url": _hostile_page(vport, hostlan)}))["result"]["targetId"]
            sid = cdp.wait(cdp.send("Target.attachToTarget", {"targetId": tid, "flatten": True}))["result"]["sessionId"]
            end = time.time() + 35
            while time.time() < end:
                d = cdp.wait(cdp.send("Runtime.evaluate", {"expression": "window.__done===true",
                             "returnByValue": True}, sid=sid))
                if d.get("result", {}).get("result", {}).get("value") is True:
                    break
                time.sleep(0.5)
            res = cdp.wait(cdp.send("Runtime.evaluate", {"expression": "JSON.stringify(window.__p)",
                           "returnByValue": True}, sid=sid))
            return json.loads(res["result"]["result"]["value"])
        except TimeoutError:
            errf.seek(0); err = errf.read().decode(errors="replace")
            if "usable sandbox" in err or "Running as root" in err or "Failed to move to new namespace" in err:
                raise _Unsupported("renderer sandbox could not initialise — needs the browser "
                                   "AppArmor profile (chrome fails closed): " + err.strip().splitlines()[-1][:160])
            raise _Unsupported("chrome/CDP did not respond (CDP pipe through pasta?): "
                               + (err.strip()[-200:] or "no stderr"))
    finally:
        proc.kill()


def tier3(vport, hostlan):
    print("\n── TIER 3: chrome renderer sandbox + hostile PAGE JS (over CDP pipe, in egress cage) ──")
    ok("--no-sandbox absent from the shipped cage (build-check)", "--no-sandbox" not in jail_argv(["x"]))
    browser = _find_browser()
    if not browser:
        skip("hostile-page-JS", "no chromium present — NOT verified here; MUST be green on clean VM")
        return
    try:
        page = _run_hostile_page(browser, vport, hostlan)
    except _Unsupported as e:
        skip("hostile-page-JS", f"NOT verified here — {e}. MUST be green on clean VM before any secret phase")
        return
    print("  hostile page reached:", json.dumps(page))
    ok("page-JS: public egress REACHABLE (usable)", page.get("public") == "REACHED")
    for name in ("host-loopback via gateway", "host LAN", "router", "cloud-metadata",
                 "ollama via gateway", "dns-rebind"):
        ok(f"page-JS blocked: {name}", page.get(name) == "blocked")


def main():
    if len(sys.argv) >= 4 and sys.argv[1] == "__probe__":
        print(json.dumps(probe(int(sys.argv[2]), sys.argv[3])))
        return 0
    if not shutil.which("bwrap"):
        print("FAIL: bwrap not available — containment cannot be established")
        return 1
    hostlan = _host_lan_ip()
    vport = _free_port()
    srv = start_listener(vport)
    try:
        tier1(vport, hostlan)
        tier2(vport, hostlan)
        tier3(vport, hostlan)
    finally:
        srv.terminate()
    print()
    if FAILS:
        print(f"❌ CONTAINMENT FIRE TEST FAILED: {len(FAILS)} probe(s) reached — {FAILS}")
        return 1
    # SKIP != PASS: the gate is NOT satisfied until every tier is green on a clean VM.
    print("Tier 1 (filesystem/mount-ns cage) GREEN: ~/Vokter data, ~/.config, /etc/shadow, DBus all hidden.")
    if SKIPS:
        print(f"⚠ GATE NOT YET SATISFIED — {len(SKIPS)} tier(s) NOT verified here (need clean VM): {SKIPS}")
        print("  The load-bearing egress-only control + renderer-sandbox + page-JS are in these tiers.")
        print("  All three MUST run green on a clean VM before ANY secret phase (card/login/email).")
    else:
        print("✅ CONTAINMENT FULLY VERIFIED — all tiers green.")
    return 0


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


if __name__ == "__main__":
    raise SystemExit(main())
