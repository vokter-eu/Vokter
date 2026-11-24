"""Tier-3 Phase 1 — OAuth / PKCE delegation broker (Path 1, the PRIMARY login path).

The sovereign-best login: no shared secret exists. The site issues a SCOPED, REVOCABLE token; there is
no password to see, store, or leak. Vokter holds a narrow capability, not a credential.

DECISIONS (TIER3_PHASE1_LOGIN.md §1.5, locked):
  * Consent happens in the user's OWN SYSTEM BROWSER — never a Vokter window. Vokter never renders the
    IdP login, so it cannot even accidentally see the credential; the user sees the real IdP URL in
    their real address bar (phishing-resistant). The seam is the feature.
  * The loopback redirect URI (127.0.0.1:<random>) brings the authorization code back to the TRUSTED
    backend; the broker exchanges it SERVER-SIDE with PKCE. The code/token never transit the model.
  * Only the resulting scoped token is stored (OS keychain). Revocation = one action.
  * Prefer the API-TOKEN sub-shape where the site has an API: no browser, no secret in any page.

GATE (TIER3_PHASE1_LOGIN.md §6): a REAL interactive flow against a REAL site is refused until the
clean-VM fire-test secret-non-leakage probe is green on the frozen .deb. `begin_delegation` enforces
this. The lower-level primitives are pure machinery, exercised against a MOCK IdP in tests (never a
real credential). The token NEVER reaches the model — the broker returns a status + metadata, never
the token itself.
"""
from __future__ import annotations

import base64
import hashlib
import os
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlencode, urlparse, parse_qs


class GateNotPassed(RuntimeError):
    """A real interactive OAuth flow was attempted before the secret-non-leakage ship gate is green."""


class BrokerError(RuntimeError):
    """Any delegation failure (state mismatch, timeout, token-exchange error)."""


def import_keychain():
    """Import the `keychain` desktop module, resolving it in BOTH run shapes.

    Frozen backend (.deb): `keychain` is a declared hiddenimport (desktop/freeze/
    vokter_backend.spec), so the plain import resolves from the bundle and this is
    a straight pass-through — the fallback below NEVER runs.

    Dev / from source: the backend runs out of app/, so desktop/ (where keychain.py
    lives) is not on sys.path and the plain import raises ModuleNotFoundError. We
    then APPEND desktop/ to sys.path and retry. Append (not insert-0) keeps app/
    modules at priority so this can't shadow them; there is no other `keychain` on
    the path to win. This mirrors the orchestrator reaching app/hwdetect the other
    direction. Dev-env-only — the frozen path never reaches it."""
    try:
        import keychain
    except ModuleNotFoundError:
        import sys
        from pathlib import Path
        desktop = str(Path(__file__).resolve().parents[1] / "desktop")
        if desktop not in sys.path:
            sys.path.append(desktop)
        import keychain
    return keychain


# ───────────────────────── the ship gate ─────────────────────────
_GATE_ENV = "VOKTER_TIER3_SECRET_GATE"   # set to "passed" ONLY by the packaged containment-selftest
                                         # after the secret-non-leakage probe is green on the frozen .deb.


def ship_gate_ok() -> bool:
    return os.environ.get(_GATE_ENV, "").strip().lower() == "passed"


# ───────────────────────── PKCE + CSRF state ─────────────────────────
def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def gen_pkce() -> tuple[str, str]:
    """(verifier, challenge) for PKCE S256. The verifier stays in the backend; only the challenge
    goes to the IdP, so an intercepted authorization code cannot be exchanged without the verifier."""
    verifier = _b64url(secrets.token_bytes(48))
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    return verifier, challenge


def gen_state() -> str:
    """Opaque CSRF token binding the authorize request to its redirect (anti-forgery)."""
    return _b64url(secrets.token_bytes(24))


def build_authorize_url(authorize_endpoint: str, *, client_id: str, redirect_uri: str,
                        scopes: list[str], state: str, challenge: str) -> str:
    """The URL the SYSTEM browser opens. Minimal scopes only (shown to the human at CONFIRM)."""
    q = urlencode({
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": " ".join(scopes),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })
    sep = "&" if urlparse(authorize_endpoint).query else "?"
    return f"{authorize_endpoint}{sep}{q}"


# ───────────────────────── loopback redirect receiver ─────────────────────────
class LoopbackReceiver:
    """A one-shot localhost HTTP server that catches the IdP redirect (?code=&state=). Binds
    127.0.0.1:0 (random port) so the redirect never leaves the machine; verifies `state`; hands the
    code to the backend. Context-managed; always shuts down. NOT the contained browser — this is the
    trusted backend catching the user's own browser's redirect."""

    def __init__(self, expected_state: str):
        self._expected = expected_state
        self._code: str | None = None
        self._err: str | None = None
        self._event = threading.Event()
        receiver = self

        class _H(BaseHTTPRequestHandler):
            def log_message(self, *a):  # never log the query (it carries the code)
                pass

            def do_GET(self):
                qs = parse_qs(urlparse(self.path).query)
                state = (qs.get("state") or [""])[0]
                code = (qs.get("code") or [""])[0]
                err = (qs.get("error") or [""])[0]
                if err:
                    receiver._err = err
                elif state != receiver._expected:
                    receiver._err = "state_mismatch"          # CSRF / forged redirect → reject
                elif not code:
                    receiver._err = "no_code"
                else:
                    receiver._code = code
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                ok = receiver._code is not None
                self.wfile.write(b"<html><body>Vokter: sign-in "
                                 + (b"received" if ok else b"failed")
                                 + b" \xe2\x80\x94 you can close this tab.</body></html>")
                receiver._event.set()

        self._server = HTTPServer(("127.0.0.1", 0), _H)
        self.port = self._server.server_address[1]
        self.redirect_uri = f"http://127.0.0.1:{self.port}/"

    def __enter__(self):
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def wait(self, timeout: float = 300) -> str:
        """Block until the redirect arrives (or timeout). Returns the authorization code; raises on
        state mismatch, an IdP error, or timeout. The code is returned to the backend, never the model."""
        if not self._event.wait(timeout):
            raise BrokerError("timed out waiting for the sign-in redirect")
        if self._err:
            raise BrokerError(f"sign-in failed: {self._err}")
        return self._code  # type: ignore[return-value]

    def __exit__(self, *exc):
        self._server.shutdown()
        self._server.server_close()


# ───────────────────────── system-browser consent ─────────────────────────
def open_in_system_browser(url: str) -> None:
    """Open `url` in the user's OWN default browser (never the contained cage, never a Vokter window).
    The user authenticates to the IdP in the surface they already trust."""
    import webbrowser
    webbrowser.open(url)   # resolves to xdg-open / the platform default


# ───────────────────────── server-side token exchange + revoke ─────────────────────────
def exchange_code(token_endpoint: str, *, client_id: str, redirect_uri: str, code: str,
                  verifier: str, client_secret: str = "", timeout: float = 30) -> dict:
    """Exchange the authorization code for a token SERVER-SIDE (PKCE: send the verifier). Returns the
    token response dict. Raises BrokerError on a non-2xx. The token stays in the backend.

    `client_secret` is PKCE-additive, not a replacement: a truly public client (Microsoft/Entra, which
    FORBIDS a secret) sends none (""), so it is included ONLY when provided. Standard/confidential
    clients (GitHub OAuth Apps, Google Desktop apps) REQUIRE it at the token endpoint even with PKCE —
    the mock IdP in tests didn't, which hid this. `Accept: application/json` is sent because GitHub's
    token endpoint defaults to a form-encoded body otherwise, which `r.json()` cannot parse."""
    import httpx
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "code_verifier": verifier,
    }
    if client_secret:
        data["client_secret"] = client_secret
    r = httpx.post(token_endpoint, data=data, headers={"Accept": "application/json"}, timeout=timeout)
    if r.status_code // 100 != 2:
        raise BrokerError(f"token exchange failed: HTTP {r.status_code}")
    try:
        body = r.json()
    except Exception:
        # A non-JSON 2xx body means the Accept: application/json negotiation didn't take (e.g. a
        # form-encoded GitHub response) — surface it rather than crash on .json().
        raise BrokerError("token exchange returned a non-JSON body "
                          f"(content-type {r.headers.get('content-type','?')}): {r.text[:200]}")
    # Some providers (notably GitHub) return token-exchange FAILURES as HTTP 200 with an
    # {error, error_description} body instead of a non-2xx — surface the provider's real reason
    # (incorrect_client_credentials / redirect_uri_mismatch / bad_verification_code / …) rather than
    # a blind "no access_token". error_description is a provider message, not a secret.
    if isinstance(body, dict) and body.get("error"):
        desc = body.get("error_description") or body.get("error_uri") or ""
        raise BrokerError(f"token exchange rejected by provider: {body['error']}"
                          + (f" — {desc}" if desc else ""))
    return body


def revoke(revoke_endpoint: str, token: str, *, client_id: str, timeout: float = 30) -> bool:
    """Best-effort server-side revocation at the IdP (RFC 7009). Pair with store.delete() so 'revoke'
    is one local+remote action. Returns True on a 2xx."""
    import httpx
    try:
        r = httpx.post(revoke_endpoint, data={"token": token, "client_id": client_id}, timeout=timeout)
        return r.status_code // 100 == 2
    except Exception:
        return False


def revoke_provider_api(endpoint_template: str, token: str, *, client_id: str, client_secret: str,
                        timeout: float = 30) -> bool:
    """Provider-specific token revocation for IdPs that have NO RFC 7009 endpoint (notably GitHub).

    GitHub: DELETE https://api.github.com/applications/{client_id}/token, HTTP Basic auth
    client_id:client_secret, JSON body {"access_token": <token>}; a 2xx (204 No Content) means the
    token is dead at the provider. `endpoint_template` carries a `{client_id}` placeholder.

    Returns True ONLY on a real 2xx. Any error — network failure, 401 (bad app credentials), 422, or a
    404 (GitHub's answer for an already-dead/unknown token) — returns False, because the caller must
    NOT claim upstream revocation it couldn't confirm. 404-as-not-confirmed is the conservative/honest
    default (we'd rather tell the user to check than imply a revocation that may not have happened)."""
    import httpx
    url = endpoint_template.format(client_id=client_id)
    try:
        r = httpx.request("DELETE", url, json={"access_token": token},
                          auth=(client_id, client_secret), timeout=timeout)
        return r.status_code // 100 == 2
    except Exception:
        return False


# ───────────────────────── token store (scoped, revocable; NOT a password) ─────────────────────────
def token_name(origin: str, identity: str) -> str:
    """Keychain item name, scoped to (origin, identity) so origin-binding governs retrieval too."""
    return f"oauth/{origin}/{identity}"


class InMemoryTokenStore:
    """Test double — keeps tokens in-process only. Never used in production."""
    def __init__(self):
        self._d: dict[str, str] = {}
    def put(self, name: str, value: str) -> None:
        self._d[name] = value
    def get(self, name: str) -> str | None:
        return self._d.get(name)
    def delete(self, name: str) -> bool:
        return self._d.pop(name, None) is not None


class KeychainTokenStore:
    """Production default: the scoped token lives in the OS keychain (service 'vokter', name
    'oauth/<origin>/<identity>'), reusing the orchestrator's timeout-protected, availability-degrading
    keychain path. A locked/absent keychain degrades to None/raise exactly like the DB-key path — the
    token is a revocable capability, not a password, so storing it here keeps the settled limit
    ('Vokter does not store user passwords') intact."""
    _SERVICE = "vokter"
    def _kc(self):
        return import_keychain()  # frozen: hiddenimport; dev: resolved onto sys.path
    def put(self, name: str, value: str) -> None:
        self._kc().set_key(value, service=self._SERVICE, name=name)
    def get(self, name: str) -> str | None:
        return self._kc().get_key(service=self._SERVICE, name=name)
    def delete(self, name: str) -> bool:
        return self._kc().delete_key(service=self._SERVICE, name=name)


# ───────────────────────── API-token sub-shape (preferred: no browser) ─────────────────────────
def use_api_token(store, origin: str, identity: str) -> bool:
    """Sub-shape (a): where the site offers an API, a pre-provisioned scoped token is used server-side
    — no browser, no secret in any page. Returns True if a token is present for (origin, identity).
    The token itself is never returned to the caller/model (status only)."""
    return store.get(token_name(origin, identity)) is not None


# ───────────────────────── the high-level interactive flow (GATED) ─────────────────────────
class IdP:
    """Minimal IdP config for a delegation. In production these come from a per-site registry.

    `revoke_method` says HOW the still-live upstream token is killed (the registry sets it per
    provider): 'rfc7009' (standard POST revoke, e.g. Google), 'provider_api' (a provider-specific call,
    e.g. GitHub's DELETE .../token), or 'manual' (no server-side revoke exists — the user must revoke it
    themselves in the provider's settings). `revoke` is the endpoint for the first two ('provider_api'
    may carry a `{client_id}` template); it is None for 'manual'."""
    def __init__(self, *, authorize: str, token: str, client_id: str, scopes: list[str],
                 revoke: str | None = None, client_secret: str = "", revoke_method: str = "manual"):
        self.authorize, self.token, self.client_id = authorize, token, client_id
        self.scopes, self.revoke, self.client_secret = scopes, revoke, client_secret
        self.revoke_method = revoke_method


def begin_delegation(idp: IdP, *, origin: str, identity: str, store,
                     open_url=open_in_system_browser, wait_timeout: float = 300) -> dict:
    """Run the full delegation: PKCE + state → system-browser consent → loopback code → server-side
    exchange → store the scoped token. Returns {status, origin, identity, scopes} — NEVER the token.

    GATED: refuses with GateNotPassed unless the secret-non-leakage ship gate is green (so it cannot
    run against a REAL site before the fire test). Tests drive it against a MOCK IdP with an injected
    `open_url` that simulates the user's browser — never a real credential."""
    if not ship_gate_ok():
        raise GateNotPassed(
            "OAuth delegation against a real site is blocked until the fire-test secret-non-leakage "
            "probe is green on the frozen .deb (set " + _GATE_ENV + "=passed only via that gate)")
    verifier, challenge = gen_pkce()
    state = gen_state()
    with LoopbackReceiver(state) as rx:
        url = build_authorize_url(idp.authorize, client_id=idp.client_id,
                                  redirect_uri=rx.redirect_uri, scopes=idp.scopes,
                                  state=state, challenge=challenge)
        open_url(url)                                   # the user's OWN browser; Vokter never renders it
        code = rx.wait(wait_timeout)                    # state verified inside
    tok = exchange_code(idp.token, client_id=idp.client_id, redirect_uri=rx.redirect_uri,
                        code=code, verifier=verifier, client_secret=idp.client_secret)
    access = tok.get("access_token")
    if not access:
        raise BrokerError("token response carried no access_token")
    store.put(token_name(origin, identity), access)     # scoped, revocable — never shown to the model
    return {"status": "succeeded", "origin": origin, "identity": identity, "scopes": idp.scopes}


def _manual_revoke_hint(origin: str) -> str:
    """The honest instruction when Vokter could NOT revoke the token upstream: the local copy is gone,
    but the token is still VALID at the provider until the user kills it themselves. Provider-specific
    where known (GitHub), generic otherwise. Non-secret, safe to show the user."""
    host = (urlparse(origin).hostname or origin or "").lower()
    if "github.com" in host:
        return ("token deleted locally but still valid on GitHub — revoke it at "
                "github.com/settings/applications")
    where = host or "the provider"
    return (f"token deleted locally but may still be valid at {where} — revoke it in that provider's "
            "connected-apps / security settings")


def revoke_delegation(store, *, origin: str, identity: str, idp: IdP | None = None) -> dict:
    """One-action revocation that is HONEST about what actually happened upstream.

    Always deletes the LOCAL token (Vokter stops holding the capability). Then, per the IdP's
    `revoke_method`, attempts to kill the token at the PROVIDER and reports the truth:

      * rfc7009      → POST the RFC 7009 revoke endpoint (Google); upstream_revoked = a real 2xx.
      * provider_api → if BOTH client_id and client_secret are available, call the provider API
                       (GitHub's DELETE .../token) and check the HTTP result; upstream_revoked = 2xx.
                       If credentials are missing, OR the call fails, we do NOT claim revocation.
      * manual / unknown → no server-side revoke exists; never claim upstream revocation.

    Returns {local_deleted, upstream_revoked, method[, manual_step]}. The CRITICAL invariant:
    `upstream_revoked` is True ONLY when a real upstream 2xx was observed — otherwise it is False and
    `manual_step` tells the user the token is still live and where to revoke it. We never imply an
    upstream revocation that didn't happen (the old code's silent local-only delete was the bug:
    'revocation in one action' was false for any non-RFC7009 provider like GitHub).

    NOT ship-gated (unlike begin_delegation): revocation destroys a capability already held; gating it
    could trap a live token if the gate later goes un-green. Honest teardown must always be available."""
    name = token_name(origin, identity)
    method = idp.revoke_method if idp else "manual"
    tok = store.get(name)

    if tok is None:
        store.delete(name)   # idempotent; nothing was stored, so nothing upstream to revoke either
        return {"local_deleted": False, "upstream_revoked": False, "method": method}

    upstream = False
    if idp and idp.revoke:
        if method == "rfc7009":
            upstream = revoke(idp.revoke, tok, client_id=idp.client_id)
        elif method == "provider_api" and idp.client_id and idp.client_secret:
            # missing credentials → skip the call entirely (no creds, no request) and fall back honest
            upstream = revoke_provider_api(idp.revoke, tok, client_id=idp.client_id,
                                           client_secret=idp.client_secret)

    local_deleted = store.delete(name)   # delete AFTER the upstream attempt (spec: check result, then delete)
    result = {"local_deleted": local_deleted, "upstream_revoked": upstream, "method": method}
    if not upstream:
        result["manual_step"] = _manual_revoke_hint(origin)
    return result
