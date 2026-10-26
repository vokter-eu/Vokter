"""Tier-3 Phase 1 — interactive browser + deterministic INJECTOR (the spine, INNOCUOUS fields only).

NO SECRETS this phase: no password / PAN / CVV / OTP. The injector is the deterministic path where
CODE types a value into a resolved field and the model only ever learns 'succeeded'|'failed' — never
the value, never in a prompt, never in memory. Here it is wired ONLY to innocuous fields (search
boxes, non-credential text). The secret variants (card checkout, login) are later phases, each behind
its own gate, and ONLY after the containment fire test passes on a CLEAN VM
(tests/browser_containment_fire.py — the hard gate; see TIER3_PHASE1_BROWSER_CONTAINMENT.md).

CONTAINMENT: the browser runs hostile page JS (T13), so it is confined by bwrap — new
user/net/ipc/pid namespaces, read-only minimal runtime, tmpfs scratch, and NO reach to host
loopback, the Vokter API/DB, or the keychain. This module owns the CANONICAL jail config
(`jail_argv`); the fire test imports it, so the test verifies the REAL cage. Import-light: Playwright
is imported lazily inside `launch` so the fire test (and the gateway) can import the cage cheaply.

Linux-only: feature-gated OFF where bwrap is absent (`browser_supported`) — never degraded to
uncontained. The gateway choke is `web.act` (CONFIRM-human-only); read-only navigation stays under
`browse`. Every fill/submit/click passes `enforce_http("web.act", …)` BEFORE acting (CI tripwire).
"""
import os
import shlex
import shutil
import subprocess
import sys

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

router = APIRouter()


# ───────────────────────── containment: the canonical cage ─────────────────────────
# Architecture VALIDATED on a clean Ubuntu 24.04 VM (apparmor_restrict_unprivileged_userns=1); see
# vokter-internal/TIER3_PHASE1_BROWSER_CONTAINMENT.md + tier3_fire_scratch/validated_vm/FINDINGS.md.
# The naive "bwrap --unshare-net + an external slirp/pasta joins the netns" is BROKEN on 24.04:
# apparmor denies an external helper's setns into bwrap's confined userns. So ownership is INVERTED:
#   * a network helper (pasta) that has its OWN AppArmor profile OWNS the netns (pasta --config-net),
#   * nft drop-private runs in PASTA's userns (it owns the netns; the nested bwrap cannot manage it),
#   * bwrap nests for FILESYSTEM isolation only (no --unshare-net; --uid 1000 so a nested browser is
#     not root — chrome refuses to run as root without --no-sandbox, which we forbid).
# The fire test imports jail_argv()/egress_argv() so it exercises the SHIPPED config, not a copy.
# PACKAGING (part of "done"): the .deb must install THREE abi-matched AppArmor profiles (bwrap, pasta,
# the browser) and bundle `passt` + `nftables`; the containment is not real for users until it does.
_RO_BINDS = ("/usr", "/bin", "/lib", "/lib64")
_BROWSER_RO_BINDS = ("/etc", "/opt", "/sys")   # the browser additionally needs these (fonts/ssl/runtime)

# nft ruleset: drop egress to every private / link-local / cloud-metadata range, allow the rest
# (public). Loaded in PASTA's userns (which owns the netns). `--no-map-gw` already blocks the gateway→
# host-loopback path; this closes RFC1918 reached via pasta's upstream forwarding.
_NFT_DROP_PRIVATE = (
    "table ip f {\n"
    "  chain out {\n"
    "    type filter hook output priority 0; policy accept;\n"
    "    ip daddr 127.0.0.0/8 drop\n"   # pasta splices ns-localhost → HOST localhost (the Vokter
                                        # admin API lives there) — this closes that path. Chrome
                                        # uses unix sockets, not TCP-localhost, so it is unaffected.
    "    ip daddr 10.0.0.0/8 drop\n"
    "    ip daddr 172.16.0.0/12 drop\n"
    "    ip daddr 192.168.0.0/16 drop\n"
    "    ip daddr 169.254.0.0/16 drop\n"
    "  }\n"
    "}\n"
)


# Binary resolution. The .deb bundles passt (pasta) + chromium-headless-shell under the install
# root and Depends on bubblewrap + nftables (system). The orchestrator/self-test entrypoint sets
# these env vars to the packaged paths; everything falls back to PATH for dev.
def _bin(name: str, env_var: str) -> str | None:
    v = os.environ.get(env_var)
    if v and (os.path.exists(v) or shutil.which(v)):
        return shutil.which(v) or v
    return shutil.which(name)


def pasta_bin(): return _bin("pasta", "VOKTER_PASTA")
def nft_bin():   return _bin("nft", "VOKTER_NFT")
def bwrap_bin(): return _bin("bwrap", "VOKTER_BWRAP")


def clean_env() -> dict:
    """Env for spawning the contained browser: strip the PyInstaller bundle's LD_LIBRARY_PATH so
    chrome loads SYSTEM libraries, not the frozen backend's `_internal/` (which is built against the
    BUILD host's glibc and can be newer than the target). PyInstaller stashes the original in
    LD_LIBRARY_PATH_ORIG — restore it; else drop the var entirely."""
    env = dict(os.environ)
    orig = env.pop("LD_LIBRARY_PATH_ORIG", None)
    if orig is not None:
        env["LD_LIBRARY_PATH"] = orig
    else:
        env.pop("LD_LIBRARY_PATH", None)
    return env


def browser_binary() -> str | None:
    """The contained browser binary: the bundled chromium-headless-shell (VOKTER_BROWSER_SHELL) or,
    in dev, any chromium/chrome on PATH."""
    v = os.environ.get("VOKTER_BROWSER_SHELL")
    if v and os.path.exists(v):
        return os.path.realpath(v)
    for c in ("chrome-headless-shell", "chromium", "chromium-browser", "google-chrome"):
        p = shutil.which(c)
        if p:
            return os.path.realpath(p)
    return None


def browser_supported() -> bool:
    """True only where the FULL containment exists: Linux + bwrap + pasta + nft + a browser.
    Elsewhere the interactive browser is ABSENT (feature-gated off) — never run uncontained."""
    return (sys.platform.startswith("linux")
            and all((bwrap_bin(), pasta_bin(), nft_bin(), browser_binary())))


def jail_argv(inner_argv, *, ro_binds=(), scratch="/tmp", unshare_net=False):
    """The INNER filesystem cage (bwrap): new user/ipc/pid namespaces, a read-only minimal runtime, a
    tmpfs scratch, and NO bind of /home, the Vokter DB, or the DBus/keychain socket. Runs as a
    non-root uid (`--uid/--gid 1000`) so a nested browser is not root. NEVER `--no-sandbox` (build-
    fail). By default does NOT unshare the net namespace — it is meant to nest INSIDE the pasta-owned,
    egress-filtered netns (see egress_argv). `unshare_net=True` gives a standalone no-egress cage (the
    Tier-1 filesystem test only). `ro_binds` = extra (src, dst) read-only binds (the browser runtime;
    the probe file in tests)."""
    argv = [bwrap_bin() or "bwrap", "--unshare-user", "--unshare-ipc", "--unshare-pid",
            "--uid", "1000", "--gid", "1000"]
    if unshare_net:
        argv.insert(1, "--unshare-net")
    for p in _RO_BINDS:
        argv += ["--ro-bind", p, p]
    argv += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", scratch]
    for src, dst in ro_binds:
        argv += ["--ro-bind", src, dst]
    argv += ["--die-with-parent", "--"]
    argv += list(inner_argv)
    assert "--no-sandbox" not in argv, "BUILD-FAIL: --no-sandbox must never appear in the browser cage"
    return argv


def egress_argv(inner_argv, *, ro_binds=()):
    """The FULL contained-browser cage (the SHIPPED launch path). pasta OWNS the netns (`--config-net`
    egress; `--no-map-gw` blocks the gateway→host-loopback path), nft drop-private loads in pasta's
    userns (which owns the netns), then the bwrap filesystem cage nests inside (inheriting the
    egress-filtered netns). Result: public reachable, host-loopback/LAN/RFC1918/metadata blocked.
    Requires the pasta + bwrap + browser AppArmor profiles (shipped by the .deb). `inner_argv` runs
    inside the bwrap cage."""
    jail = jail_argv(inner_argv, ro_binds=ro_binds, unshare_net=False)
    # nft is piped in (no tempfile); then exec the bwrap fs cage. The whole thing runs in pasta's
    # userns, which holds CAP_NET_ADMIN over the netns it created.
    nft = shlex.quote(nft_bin() or "nft")
    sh = (f"printf %s {shlex.quote(_NFT_DROP_PRIVATE)} | {nft} -f - && exec "
          + " ".join(shlex.quote(a) for a in jail))
    return [pasta_bin() or "pasta", "--config-net", "--no-map-gw", "--", "bash", "-c", sh]


# ───────────────────────── the injector: INNOCUOUS fields only ─────────────────────────
# Phase 1 types ONLY these. Secret kinds are DELIBERATELY not handled — the injector refuses them
# by construction (no secret-retrieval path exists yet). SECRET_KINDS is listed only to make the
# refusal explicit and greppable; wiring any of them is a separate, gated phase.
INNOCUOUS_KINDS = frozenset({"search", "text", "query", "filter", "quantity", "date",
                             "select", "textarea", "non_credential"})
SECRET_KINDS = frozenset({"password", "pan", "card", "cardnumber", "cvv", "cvc", "otp",
                          "secret", "credential", "pin"})


class InjectorError(Exception):
    """Raised when the injector is asked to do something not permitted this phase."""


def _require_innocuous(kind: str | None) -> None:
    k = (kind or "").strip().lower()
    if k in SECRET_KINDS:
        raise InjectorError(
            f"field kind '{k}' is a SECRET — not permitted in Phase 1; secrets are a later phase, "
            "behind their own gate and only after the clean-VM containment fire test passes")
    if k not in INNOCUOUS_KINDS:
        raise InjectorError(f"unknown field kind '{k}' — Phase 1 allows only innocuous fields "
                            f"({sorted(INNOCUOUS_KINDS)})")


class Session:
    """Drives a CONTAINED browser via a CDP `transport`. The model proposes a plan (navigate;
    'fill the search box with X'; click Submit); CODE here executes and returns only a STATUS.
    The typed value is NEVER returned to the caller or the model — the single structural property
    the secret phases will inherit (the value bypasses the model entirely)."""

    def __init__(self, transport):
        self._t = transport

    def navigate(self, url: str) -> str:
        return "succeeded" if self._t.navigate(url) else "failed"

    def read_text(self) -> str:
        return self._t.read_text()

    def fill(self, selector: str, value: str, *, kind: str) -> str:
        """Type `value` into the field at `selector`. `kind` MUST be innocuous (enforced). Returns
        'succeeded'|'failed' — never the value. The value is handed to the transport (code), never
        surfaced back to the model."""
        _require_innocuous(kind)
        return "succeeded" if self._t.type_into(selector, value) else "failed"

    def click(self, selector: str) -> str:
        return "succeeded" if self._t.click(selector) else "failed"


class _CDP:
    """Minimal CDP client over chrome's --remote-debugging-pipe (fd3 in / fd4 out, NUL-framed). Raw
    CDP (not Playwright) by design: identical security (inherited pipe, no TCP control port), and no
    Node driver to bundle into the frozen backend."""
    def __init__(self, rfd, wfd):
        self.r, self.w, self._id, self._buf = rfd, wfd, 0, b""
    def send(self, method, params=None, sid=None):
        import json
        self._id += 1
        m = {"id": self._id, "method": method, "params": params or {}}
        if sid:
            m["sessionId"] = sid
        os.write(self.w, json.dumps(m).encode() + b"\x00")
        return self._id
    def wait(self, wid, timeout=30):
        import json
        import time
        end = time.time() + timeout
        while time.time() < end:
            while b"\x00" not in self._buf:
                if time.time() > end:
                    raise InjectorError("CDP timeout")
                try:
                    c = os.read(self.r, 65536)
                    self._buf += c if c else b""
                except BlockingIOError:
                    pass
                time.sleep(0.01)
            line, self._buf = self._buf.split(b"\x00", 1)
            m = json.loads(line)
            if m.get("id") == wid:
                return m
        raise InjectorError("CDP timeout")
    def eval(self, expr, sid, timeout=30):
        r = self.wait(self.send("Runtime.evaluate",
                      {"expression": expr, "returnByValue": True, "awaitPromise": True}, sid=sid), timeout)
        return r.get("result", {}).get("result", {}).get("value")


class CDPTransport:
    """Drives the CONTAINED browser over the CDP pipe. The LLM never touches this — only Session's
    status-returning methods. The typed value reaches the field via code (Runtime.evaluate set-value
    for innocuous fields), never the model. Closed over the egress cage = pasta+nft+bwrap."""
    def __init__(self, browser: str):
        bdir = os.path.dirname(browser)
        ro = [(bdir, bdir)] + [(p, p) for p in _BROWSER_RO_BINDS if os.path.exists(p)]
        chrome = [browser, "--remote-debugging-pipe", "--headless=new", "--disable-gpu",
                  "--disable-dev-shm-usage", "--no-first-run", "--user-data-dir=/tmp/ud"]
        argv = egress_argv(chrome, ro_binds=ro)
        self._br, self._pw = os.pipe()      # browser reads _br (→fd3); we write _pw
        self._pr, self._bw = os.pipe()      # browser writes _bw (→fd4); we read _pr
        os.set_blocking(self._pr, False)

        def _pre():
            os.dup2(self._br, 3); os.dup2(self._bw, 4)
        self._proc = subprocess.Popen(argv, preexec_fn=_pre, env=clean_env(),
                                      pass_fds=(3, 4, self._br, self._bw), stderr=subprocess.DEVNULL)
        os.close(self._br); os.close(self._bw)
        self._cdp = _CDP(self._pr, self._pw)
        tid = self._cdp.wait(self._cdp.send("Target.createTarget", {"url": "about:blank"}))["result"]["targetId"]
        self._sid = self._cdp.wait(self._cdp.send("Target.attachToTarget",
                                   {"targetId": tid, "flatten": True}))["result"]["sessionId"]

    def navigate(self, url):
        import json
        self._cdp.wait(self._cdp.send("Page.enable", sid=self._sid))
        self._cdp.send("Page.navigate", {"url": url}, sid=self._sid)
        import time
        end = time.time() + 30
        while time.time() < end:
            if self._cdp.eval("document.readyState==='complete'", self._sid) is True:
                return True
            time.sleep(0.2)
        return False

    def type_into(self, selector, value):
        import json
        expr = ("(()=>{const el=document.querySelector(%s); if(!el) return false; el.focus();"
                "el.value=%s; el.dispatchEvent(new Event('input',{bubbles:true}));"
                "el.dispatchEvent(new Event('change',{bubbles:true})); return true;})()"
                % (json.dumps(selector), json.dumps(value)))
        return self._cdp.eval(expr, self._sid) is True

    def click(self, selector):
        import json
        expr = "(()=>{const el=document.querySelector(%s); if(!el) return false; el.click(); return true;})()" % json.dumps(selector)
        return self._cdp.eval(expr, self._sid) is True

    def read_text(self):
        return self._cdp.eval("document.body?document.body.innerText:''", self._sid) or ""

    def close(self):
        try:
            self._proc.kill()
        except Exception:
            pass


def launch() -> Session:
    """Launch a CONTAINED headless browser and return a Session driven over a raw CDP pipe through
    egress_argv (pasta owns the netns + nft drop-private + nested bwrap fs cage — validated on the
    clean VM). Feature-gated: requires bwrap + pasta + nft + the bundled browser, with the pasta +
    bwrap + browser AppArmor profiles the .deb installs."""
    if not browser_supported():
        raise InjectorError("interactive browser unavailable here — needs contained Linux "
                            "(bwrap + pasta + nft + a browser) with the shipped AppArmor profiles")
    return Session(CDPTransport(browser_binary()))


# ───────────────────────── gateway-choked route (§7) ─────────────────────────
class WebActRequest(BaseModel):
    op: str                       # navigate | read | fill | submit | click
    url: str | None = None
    selector: str | None = None
    value: str | None = None      # innocuous field value only (gateway + _require_innocuous enforce)
    kind: str | None = None
    confirm: bool = False         # the human's explicit approval for a consequential act


@router.post("/api/web/act")
def web_act(req: WebActRequest, x_vokter_human_session: str | None = Header(default=None)):
    """The one interactive-browser entry point, gateway-choked. Read-only navigation reuses the
    `browse` allowlist; any fill/submit/click is `web.act` CONFIRM-human-only (428 until approved,
    BLOCK for peer/mcp/autonomous). The gate is called BEFORE the injector ever acts — a page or a
    peer can never drive a form. NO secrets this phase (innocuous kinds only, enforced twice)."""
    from chat import is_local_human_session
    from safety import HUMAN, Decision, enforce_http, guard

    # Only the local human session acts; anything else is a non-human context → web.act BLOCKs.
    context = HUMAN if is_local_human_session(x_vokter_human_session) else "autonomous"
    op = (req.op or "").strip().lower()

    if op in ("navigate", "read"):
        # Read-only: the allowlist/SSRF predicate of `browse` is the guard (lighter path).
        if op == "navigate":
            if not req.url:
                raise HTTPException(400, "navigate requires url")
            if guard("browse", req.url, context=context) != Decision.ALLOW:
                raise HTTPException(403, "URL not in allowlist (add a browse permission first)")
    elif op in ("fill", "submit", "click"):
        # Consequential: CONFIRM-human-only. 428 until the human approves; 403 for any non-human.
        enforce_http("web.act", req.selector or req.url, context=context, confirmed=req.confirm)
        if op == "fill":
            try:
                _require_innocuous(req.kind)   # second, structural guard: no secret field this phase
            except InjectorError as e:
                raise HTTPException(400, str(e))   # refused secret/unknown field → clean 4xx, not 500
    else:
        raise HTTPException(400, f"unknown op '{op}'")

    # Execution runs the CONTAINED browser — feature-gated; the real run is the packaged/clean-VM
    # path (the containment fire test is the gate). The gating above has already happened, so this
    # never acts un-gated even once the launch is wired.
    if not browser_supported():
        raise HTTPException(503, "interactive browser not available on this platform (contained Linux only)")
    raise HTTPException(501, "contained browser execution lands on the packaged/clean-VM path "
                             "after the containment fire test passes (Phase 1 skeleton)")
