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
        import keychain  # desktop module; bundled (hiddenimport) in the frozen backend
        return keychain
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
    """Minimal IdP config for a delegation. In production these come from a per-site registry."""
    def __init__(self, *, authorize: str, token: str, client_id: str, scopes: list[str],
                 revoke: str | None = None, client_secret: str = ""):
        self.authorize, self.token, self.client_id = authorize, token, client_id
        self.scopes, self.revoke, self.client_secret = scopes, revoke, client_secret


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


def revoke_delegation(store, *, origin: str, identity: str, idp: IdP | None = None) -> bool:
    """One-action revocation: revoke at the IdP (if a revoke endpoint is known) AND delete locally.
    Returns True if the local token was removed."""
    name = token_name(origin, identity)
    if idp and idp.revoke:
        tok = store.get(name)
        if tok:
            revoke(idp.revoke, tok, client_id=idp.client_id)
    return store.delete(name)
