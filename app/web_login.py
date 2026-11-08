"""Tier-3 Phase 1 — web.login: sign in without the password reaching the model.

SKELETON + origin-binding scaffolding ONLY. NO secret path executes here: no keychain read, no OAuth
token, no password injected, nothing typed. Those land on the gated path AFTER the fire-test
secret-non-leakage probe is green on the frozen .deb (TIER3_PHASE1_LOGIN.md §6/§7). This module adds:

  * the `web.login` gateway choke (CONFIRM-human-only; BLOCK for peer/mcp/autonomous),
  * the reference-not-secret request shape ({site, identity_ref, confirm} — NO value field, so a
    secret can never ride this request), and
  * the origin-binding primitives: normalize_origin / origin_matches, plus read_resolved_origin which
    takes the RESOLVED top-level origin FROM THE BROWSER (never the model's claimed URL).

Distinct from web.act by design: web.act fill stays innocuous-only (SECRET_KINDS untouched); the
password/token is NEVER a field here and NEVER reaches the model (status-only, same as the injector).
"""
from urllib.parse import urlsplit

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

router = APIRouter()

_DEFAULT_PORTS = {"http": 80, "https": 443}


# ───────────────────────── origin-binding primitives (pure) ─────────────────────────
def normalize_origin(url_or_origin: str) -> str:
    """Canonical scheme://host[:port] — lowercased host, default port dropped. Raises on anything
    that is not an absolute http(s) origin: origin-binding needs a real web origin, never a bare
    host, a path, or a model's free-text 'claim'."""
    parts = urlsplit((url_or_origin or "").strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError(f"not an absolute http(s) origin: {url_or_origin!r}")
    host = parts.hostname.lower()
    port = parts.port
    if port is None or port == _DEFAULT_PORTS[parts.scheme]:
        return f"{parts.scheme}://{host}"
    return f"{parts.scheme}://{host}:{port}"


def origin_matches(resolved: str, registered: str) -> bool:
    """Exact-origin match (scheme+host+port), like a password manager's default — NO subdomain
    wildcarding. A credential registered for one origin is released ONLY into that exact origin, so a
    prompt-injected model that navigated the cage to the wrong site cannot pull the secret."""
    try:
        return normalize_origin(resolved) == normalize_origin(registered)
    except ValueError:
        return False


def read_resolved_origin(session) -> str:
    """The RESOLVED top-level origin, read FROM THE BROWSER after navigation — never the model's
    claimed URL. This is the value the credential lookup and the human-confirm are bound to (§1.4).
    `session.top_origin()` is an innocuous read (no secret); the live read runs only behind the
    feature gate, on the contained browser."""
    return normalize_origin(session.top_origin())


# ───────────────────────── per-site registry (code-side; NEVER the model) ─────────────────────────
# What the login routine navigates, the origin a credential is bound to, and which fields to drive.
# The MODEL only names a site key; CODE resolves it here — login_url + registered_origin are NEVER taken
# from the model's claim. Start SMALL (verified entries only); add sites as their selectors are confirmed
# against the live page. Security does NOT depend on completeness: a missing entry refuses (404), and the
# gate is the registered origin + the injector's EXACT origin re-read — a wrong selector only makes a
# login fail, never leak. (OAuth sub-shape — idp/provider/scopes — is added when Path 1 goes live.)
_SITE_REGISTRY = {
    # OAuth / PKCE (Path 1 — structurally SAFER: no password exists to capture; T14 can't steal what is
    # never typed). Consent happens in the user's OWN system browser (never the cage); only a scoped,
    # REVOCABLE token is stored. Provision a real client_id (a registered OAuth app) before an entry
    # works — an empty client_id is refused (503), never a broken authorize URL.
    "github": {
        "mode": "oauth",
        "provider": "GitHub",
        "registered_origin": "https://github.com",
        "authorize": "https://github.com/login/oauth/authorize",
        "token": "https://github.com/login/oauth/access_token",
        "revoke": None,
        "client_id": "",   # ← set to your registered OAuth app's client_id to enable this entry
        "scopes": ["read:user"],
    },
    # Password / keychain-injector (Path 2 — fallback for password-only sites; the weaker path, T14
    # residual: a hostile target page can capture what is typed). login_url + registered_origin + the
    # field selectors are code-side (never the model's claim); the injector re-checks the resolved origin.
    "github-password": {
        "mode": "password",
        "login_url": "https://github.com/login",
        "registered_origin": "https://github.com",
        "selectors": {"username": "#login_field", "password": "#password", "submit": "input[name='commit']"},
    },
}


def _registry_lookup(site: str):
    return _SITE_REGISTRY.get((site or "").strip().lower())


# ───────────────────────── cage seams (patchable in the hermetic e2e fire test) ─────────────────────────
def _open_login_session():
    """Open a CONTAINED browser session for a login. Patchable so the hermetic e2e fire test can drive the
    FULL handler with a fake transport (no cage), while the real cage path ships unchanged."""
    import interactive_browser as ib
    return ib.launch()


def _credential_store():
    """The production read-only keychain credential store. Patchable in tests."""
    from web_login_injector import KeychainCredentialStore
    return KeychainCredentialStore()


def _close_session(s):
    try:
        s._t.close()
    except Exception:
        pass


def _resolve_origin_readonly(entry) -> str:
    """Phase 1 (read-only, NO secret): navigate the registry's login_url in the cage and read the RESOLVED
    top-level origin from the browser — the destination the human will confirm (honesty, not the gate)."""
    s = _open_login_session()
    try:
        s.navigate(entry["login_url"])
        return read_resolved_origin(s)
    finally:
        _close_session(s)


def _run_password_login(entry, identity: str) -> str:
    """Phase 2 (confirmed): navigate + inject. perform_password_login RE-READS the resolved origin and
    refuses unless it exactly equals the registered origin (THE gate), dereferences the secret keyed on
    that origin, types it, and returns status only — the secret never reaches here or the model."""
    from web_login_injector import perform_password_login, LoginSelectors
    s = _open_login_session()
    try:
        s.navigate(entry["login_url"])
        return perform_password_login(
            s, _credential_store(),
            registered_origin=entry["registered_origin"], identity=identity,
            selectors=LoginSelectors(**entry["selectors"]), username=identity)
    finally:
        _close_session(s)


# ───────────────────────── OAuth seams (Path 1; patchable in tests) ─────────────────────────
def _token_store():
    """Production OAuth token store — the scoped, revocable token in the OS keychain. Patchable."""
    from oauth_broker import KeychainTokenStore
    return KeychainTokenStore()


def _open_system_browser(url):
    """Open the IdP consent in the user's OWN system browser (never the cage). Patchable in tests."""
    from oauth_broker import open_in_system_browser
    open_in_system_browser(url)


def _run_oauth_login(entry, identity: str) -> str:
    """Path 1 (OAuth/PKCE): consent in the user's SYSTEM browser → loopback code (trusted backend) →
    server-side PKCE exchange → scoped token in the keychain. GATED (GateNotPassed until the ship gate is
    green) and NEVER returns the token — only status + metadata reach the handler/model. No cage, no
    secret in any page: the sovereign-best path."""
    from oauth_broker import begin_delegation, IdP
    idp = IdP(authorize=entry["authorize"], token=entry["token"], client_id=entry["client_id"],
              scopes=entry["scopes"], revoke=entry.get("revoke"))
    res = begin_delegation(idp, origin=entry["registered_origin"], identity=identity,
                           store=_token_store(), open_url=_open_system_browser, wait_timeout=300)
    return res.get("status", "failed")


# ───────────────────────── gateway-choked two-phase route ─────────────────────────
class WebLoginRequest(BaseModel):
    site: str              # the model's CLAIMED site KEY — resolved via the code-side registry; its
                           # login_url + registered_origin are never taken from the model's claim.
    identity_ref: str      # WHICH account/alias — a REFERENCE, dereferenced to a secret by CODE.
    confirm: bool = False  # the human's explicit approval (phase 2).
    # Deliberately NO password / value / token / otp field: a secret can never ride this request.


@router.post("/api/web/login")
def web_login(req: WebLoginRequest, x_vokter_human_session: str | None = Header(default=None)):
    """Sign in as `identity_ref` WITHOUT the secret reaching the model. Two phases, STATELESS:

    Two modes from the code-side registry (OAuth-first — the structurally safer path):
      * OAuth (Path 1): consent in the user's OWN system browser → loopback code → server-side PKCE
        exchange → scoped, revocable token in the keychain. No cage, no secret in any page, nothing to
        capture. Phase-1 428 shows provider + scopes; phase 2 runs begin_delegation.
      * password (Path 2, fallback): phase-1 navigates read-only in the cage and reads the RESOLVED
        origin (honesty); phase 2 injects, and perform_password_login RE-READS the resolved origin and
        refuses unless it EXACTLY equals the registered origin — THAT (registry origin + injector
        re-check) is the gate, not the confirm step. Status-only; the secret never reaches the model.

      Phase 1 (confirm=false) — CONSENT. enforce_http BLOCKs non-human (403) BEFORE anything acts; the
        human-unconfirmed 428 is intercepted and ENRICHED for the confirm UI.
      Phase 2 (confirm=true) — the human approved. enforce_http allows (human+confirm), BLOCKs non-human.
        GateNotPassed (ship gate not green) → 503; both paths stay dormant until the self-test passes.

    Stateless by design: phase 2 is the same {site, identity_ref, confirm:true} round-tripped by the
    TRUSTED renderer. No server-side session/flow state: a page/peer/model cannot reach web.login at all
    (CONFIRM-human-only + bridge-only human-session token), so the origin check is the entire boundary.
    """
    from chat import is_local_human_session
    from safety import HUMAN, enforce_http
    import interactive_browser as ib
    from oauth_broker import GateNotPassed

    context = HUMAN if is_local_human_session(x_vokter_human_session) else "autonomous"
    target = f"{req.site} as {req.identity_ref}"   # gateway-recorded target — secret-free by construction

    if not req.confirm:
        # Phase 1 — CONSENT. Gate FIRST: 403 for non-human BEFORE any navigation / cage launch.
        try:
            enforce_http("web.login", target, context=context, confirmed=False)
        except HTTPException as e:
            if e.status_code != 428:
                raise                           # non-human → 403 propagates; nothing acts
        entry = _registry_lookup(req.site)
        if entry is None:
            raise HTTPException(404, f"no login registered for site '{req.site}'")
        if entry.get("mode") == "oauth":
            # OAuth consents in the user's OWN system browser (no cage); the sheet shows provider + scopes.
            # resolved_origin here is the IdP's registered authorize origin — the human ALSO verifies it in
            # their real address bar (§1.5). An unconfigured entry (no client_id) is refused up front.
            if not entry.get("client_id"):
                raise HTTPException(503, f"OAuth not configured for '{req.site}' (no client_id provisioned)")
            raise HTTPException(428, detail={
                "error": "confirmation_required", "action": "web.login",
                "resolved_origin": entry["registered_origin"], "identity": req.identity_ref,
                "mode": "oauth", "provider": entry.get("provider"), "scopes": entry.get("scopes")})
        # password: navigate read-only in the cage and read the RESOLVED origin (honesty, not the gate).
        if not ib.browser_supported():
            raise HTTPException(503, "interactive browser not available on this platform (contained Linux only)")
        resolved = _resolve_origin_readonly(entry)
        raise HTTPException(428, detail={
            "error": "confirmation_required", "action": "web.login",
            "resolved_origin": resolved, "identity": req.identity_ref, "mode": "password",
            "provider": None, "scopes": None})

    # Phase 2 — confirmed: enforce_http allows human+confirm, BLOCKs non-human (403).
    enforce_http("web.login", target, context=context, confirmed=True)
    entry = _registry_lookup(req.site)
    if entry is None:
        raise HTTPException(404, f"no login registered for site '{req.site}'")
    try:
        if entry.get("mode") == "oauth":
            if not entry.get("client_id"):
                raise HTTPException(503, f"OAuth not configured for '{req.site}' (no client_id provisioned)")
            status = _run_oauth_login(entry, req.identity_ref)       # system-browser consent; no cage
        else:
            if not ib.browser_supported():
                raise HTTPException(503, "interactive browser not available on this platform (contained Linux only)")
            status = _run_password_login(entry, req.identity_ref)    # cage inject
    except GateNotPassed:
        raise HTTPException(503, "login unavailable: the containment self-test has not passed on this install")
    return {"status": status}
