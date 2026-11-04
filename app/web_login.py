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


# ───────────────────────── gateway-choked route (§7) ─────────────────────────
class WebLoginRequest(BaseModel):
    site: str              # the model's CLAIMED URL — used only to navigate; the binding + the human
                           # confirm use the RESOLVED origin read back from the browser, not this.
    identity_ref: str      # WHICH account/alias — a REFERENCE, dereferenced to a secret by CODE later.
    confirm: bool = False  # the human's explicit approval.
    # Deliberately NO password / value / token / otp field: a secret can never ride this request.


@router.post("/api/web/login")
def web_login(req: WebLoginRequest, x_vokter_human_session: str | None = Header(default=None)):
    """Sign in to a site as `identity_ref` WITHOUT the secret reaching the model. Gateway-choked:
    web.login is CONFIRM-human-only (428 until the human approves; 403/BLOCK for peer/mcp/autonomous).
    The request carries only a REFERENCE, never a secret.

    SKELETON: the secret path — navigate → read the RESOLVED origin from the browser → origin-checked
    OAuth(broker)/keychain lookup → injector types it (status-only) — lands on the gated/clean-VM
    route AFTER the fire-test secret-non-leakage probe is green. Nothing secret runs here yet."""
    from chat import is_local_human_session
    from safety import HUMAN, enforce_http
    import interactive_browser as ib

    # Only the local human can confirm; any other context → web.login BLOCKs (403).
    context = HUMAN if is_local_human_session(x_vokter_human_session) else "autonomous"

    # CONFIRM-human-only, BEFORE anything acts (CI tripwire). NOTE: the target here is the model's
    # CLAIMED site; the SHIPPED path re-gates on the RESOLVED origin (read from the browser) so the
    # human confirms the real destination, not a claim (§1.4 origin-binding).
    enforce_http("web.login", f"{req.site} as {req.identity_ref}", context=context, confirmed=req.confirm)

    if not ib.browser_supported():
        raise HTTPException(503, "interactive browser not available on this platform (contained Linux only)")
    raise HTTPException(501, "web.login secret path lands on the packaged/clean-VM route after the "
                             "fire-test secret-non-leakage probe is green (Phase 1 skeleton)")
