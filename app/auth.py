"""
Admin API gate (security finding H1).

The human's admin API — everything under /api/ except the public agent card —
must not be reachable just because someone can reach port 8080. This gate
requires VOKTER_ADMIN_TOKEN on those paths.

Trust domains are kept separate on purpose:
  * VOKTER_ADMIN_TOKEN — the human's admin API (here).
  * VOKTER_A2A_TOKEN   — elevates a peer *agent* over /a2a (a2a_server).
A peer agent must never hold the admin token.

Opt-in: when VOKTER_ADMIN_TOKEN is empty the gate allows everything (safe only
because the app is loopback-bound by default and the browser UI is loopback-
only). config.py prints a loud warning if Vokter is exposed without it.

Assumption (recorded): the human never reaches /api/* from off-box. The browser
UI is a local, loopback-only tool, so it needs no token. If a remote admin UI is
ever added (e.g. Phase 7 TEE), this needs real auth and must be revisited.
"""
import hmac

from config import ADMIN_TOKEN, BIND

# /api/ paths that stay public (the agent identity card any peer may read).
_PUBLIC_API_PATHS = frozenset({"/api/agent/card"})

# Loopback hostnames the admin API may legitimately be addressed as (DNS-rebinding defense).
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def _host_name(host_header: str) -> str:
    """Hostname from a Host header, port stripped. Handles bracketed IPv6 (`[::1]`, `[::1]:8080`)
    and bare IPv6 (`::1`) as well as `host`/`host:port`."""
    h = (host_header or "").strip()
    if not h:
        return ""
    if h.startswith("["):                       # [::1] or [::1]:port
        end = h.find("]")
        return h[1:end] if end != -1 else h[1:]
    if h.count(":") > 1:                         # bare IPv6 (no brackets, no port), e.g. ::1
        return h
    return h.rsplit(":", 1)[0] if ":" in h else h


def host_allowed(host_header: str | None) -> bool:
    """DNS-rebinding defense for the admin API.

    On the shipped default (no VOKTER_ADMIN_TOKEN) the admin API is loopback-only, so the request's
    Host MUST name loopback — a rebinding page keeps its own hostname in the Host header (evil.com),
    so a strict loopback-hostname allowlist blocks it even after the IP rebinds to 127.0.0.1. An
    ABSENT Host is rejected (fail-closed). When VOKTER_ADMIN_TOKEN is set the operator deliberately
    exposed the API and the token is the control, so Host is not enforced here. The port is not the
    control — only the hostname is (an attacker can't make a browser send a loopback hostname for a
    request aimed at their own domain)."""
    if ADMIN_TOKEN:
        return True                             # intentional exposure → the admin token governs
    name = _host_name(host_header or "").lower()
    if not name:
        return False                            # no Host header → reject
    allowed = set(_LOOPBACK_HOSTS)
    if BIND and BIND.lower() not in ("0.0.0.0", "::", ""):
        allowed.add(BIND.lower())               # a specific (non-wildcard) bind is also addressable
    return name in allowed


def requires_admin(path: str) -> bool:
    """True if this path is part of the protected admin API."""
    if not path.startswith("/api/"):
        return False                      # /, /static, /a2a, /.well-known, /docs
    return path not in _PUBLIC_API_PATHS


def admin_token_ok(provided: str | None) -> bool:
    """Constant-time check of a presented admin token."""
    if not ADMIN_TOKEN:
        return True                       # opt-in: gate disabled when unset
    return hmac.compare_digest((provided or "").encode(), ADMIN_TOKEN.encode())


def admin_headers() -> dict:
    """Header an internal, trusted component (dispatch, MCP) sends to reach the
    local admin API. Empty (no-op) when the gate is disabled."""
    return {"X-Vokter-Admin-Token": ADMIN_TOKEN} if ADMIN_TOKEN else {}
