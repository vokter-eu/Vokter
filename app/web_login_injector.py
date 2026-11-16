"""Tier-3 Phase 1 — Path 2: keychain → origin-checked password injector (password-only sites).

THE delicate path: a real plaintext password transits Vokter's process (T15). Discipline:
  * The password is dereferenced by CODE, keyed on the RESOLVED top-level origin (read from the
    browser after navigation — NEVER the model's claimed URL) + identity. It is released ONLY into a
    page whose resolved origin equals the credential's registered origin (the password-manager refusal
    pattern): wrong (or opaque/unreadable) origin → the secret is never dereferenced and never typed.
  * Typed ONLY here, over the CDP pipe into the contained browser (interactive_browser.type_secret),
    NEVER via web.act fill (which stays innocuous-only; SECRET_KINDS / _require_innocuous untouched, so
    `password` never becomes a model-facing field kind).
  * Status-only ('succeeded'|'failed'). The value is never returned, logged, held in memory/DB, nor put
    in a prompt/SSE frame. The local reference is dropped (`del`) the instant the keystroke returns.
  * GATED: GateNotPassed unless the ship gate is green (VOKTER_TIER3_SECRET_GATE=passed) — set only by
    the packaged containment-selftest after the secret-non-leakage probe is green on the frozen .deb.
    Shares the ONE gate with Path 1 (oauth_broker), so no secret path can run before that green.

Honest residual (T15): a Python str cannot be reliably zeroed, so the password transiently exists in
this process (and as a copy inside the CDP json frame). "Never in Vokter's process" is NOT claimable —
only "never persisted, returned, logged, or shown to the model." A hostile TARGET page (T14) can still
capture what is typed into it. These are exactly why OAuth (Path 1, no secret at all) is primary.

Custody / settled limit: Vokter READS the user's existing OS keychain and NEVER writes a user password
(KeychainCredentialStore has NO put/set). The user provisions the credential; Vokter dereferences it.
"""
from __future__ import annotations

from oauth_broker import GateNotPassed, ship_gate_ok, import_keychain  # the ONE ship gate + keychain resolver, shared with Path 1
from web_login import origin_matches, read_resolved_origin


# ───────────────────────── credential store (READ-ONLY in production) ─────────────────────────
_SERVICE = "vokter-login"   # distinct from the DB-key slot ('vokter'/'db_key'); scoped per (origin, identity)


def credential_name(origin: str, identity: str) -> str:
    """Keychain item name, scoped to (origin, identity) so origin-binding governs retrieval too."""
    return f"login/{origin}/{identity}"


class KeychainCredentialStore:
    """Production default: the user's password lives in the OS keychain under (service 'vokter-login',
    name 'login/<origin>/<identity>'). READ-ONLY by construction — there is NO put/set here, so Vokter
    cannot write a user password (the settled limit 'Vokter does not store user passwords' holds
    structurally, not by convention). A locked/absent keychain → None, so login fails closed."""
    def get_secret(self, origin: str, identity: str) -> str | None:
        keychain = import_keychain()   # frozen: hiddenimport; dev: resolved onto sys.path
        return keychain.get_key(service=_SERVICE, name=credential_name(origin, identity))


class InMemoryCredentialStore:
    """Test double ONLY — lets a test provision a SENTINEL secret. Never used in production (prod uses
    the read-only KeychainCredentialStore). `put` exists solely so a test can seed the sentinel."""
    def __init__(self):
        self._d: dict[str, str] = {}
    def put(self, origin: str, identity: str, secret: str) -> None:
        self._d[credential_name(origin, identity)] = secret
    def get_secret(self, origin: str, identity: str) -> str | None:
        return self._d.get(credential_name(origin, identity))


# ───────────────────────── the login routine (code, not model steps) ─────────────────────────
class LoginSelectors:
    """Which fields the login routine drives — from a per-site registry (code-side, never the model)."""
    def __init__(self, *, username: str, password: str, submit: str | None = None):
        self.username, self.password, self.submit = username, password, submit


def perform_password_login(session, store, *, registered_origin: str, identity: str,
                           selectors: LoginSelectors, username: str | None = None) -> str:
    """Sign in on a password-only site WITHOUT the password reaching the model. Returns 'succeeded'|
    'failed' — NEVER the secret.

    All code; a single human CONFIRM was already granted upstream at the web.login gateway:
      1. GATED: GateNotPassed unless the ship gate is green.
      2. Read the RESOLVED top-level origin FROM THE BROWSER (never the model's claim). Opaque/
         unreadable (data:/about:blank/file:) → fail closed, nothing dereferenced.
      3. origin_matches(resolved, registered)? No → refuse BEFORE any dereference or keystroke.
      4. Dereference the secret keyed on the RESOLVED origin (+ identity) — the lookup is itself
         origin-bound, so even a bug in step 3 cannot pull another origin's secret.
      5. Type username (innocuous fill) then the password (type_secret, login-only path), submit.
      6. Drop the secret reference immediately; return STATUS only.
    """
    if not ship_gate_ok():
        raise GateNotPassed(
            "password-injector login against a real site is blocked until the fire-test "
            "secret-non-leakage probe is green on the frozen .deb (VOKTER_TIER3_SECRET_GATE=passed)")

    import interactive_browser as ib

    # (2) resolved origin, read from the browser — never the model's claimed URL.
    try:
        resolved = read_resolved_origin(session)
    except ValueError:
        return "failed"   # opaque/unreadable origin (data:/about:blank/file:) → refuse

    # (3) password-manager refusal pattern: wrong origin → never dereferenced, never typed.
    if not origin_matches(resolved, registered_origin):
        return "failed"

    # (4) dereference keyed on the RESOLVED origin (+ identity) — origin-bound lookup.
    secret = store.get_secret(resolved, identity)
    if secret is None:
        return "failed"   # no credential provisioned / keychain locked → fail closed; no value involved

    try:
        # (5) username is an account handle, not a secret → ordinary innocuous fill.
        if username is not None and session.fill(selectors.username, username, kind="text") != "succeeded":
            return "failed"
        if ib.type_secret(session, selectors.password, secret) != "succeeded":
            return "failed"
        if selectors.submit and session.click(selectors.submit) != "succeeded":
            return "failed"
        return "succeeded"
    finally:
        # (6) minimize lifetime: drop our reference. T15 (honest): a Python str can't be reliably
        # zeroed and a copy may transiently remain inside the CDP json frame; it is never persisted,
        # returned, logged, or shown to the model.
        del secret
