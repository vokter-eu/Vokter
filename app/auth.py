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

from config import ADMIN_TOKEN

# /api/ paths that stay public (the agent identity card any peer may read).
_PUBLIC_API_PATHS = frozenset({"/api/agent/card"})


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
