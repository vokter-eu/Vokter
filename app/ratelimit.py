"""
In-memory sliding-window rate limiter — purely local, no network, no DB.

Used by the Nostr listener to bound the work an inbound flood can cause. Two
layers matter because Nostr pubkeys are free to mint:

  * per-peer  — one chatty/abusive peer can't monopolise us.
  * global    — caps total inbound work, which is the only thing that survives a
                Sybil flood (an attacker rotating pubkeys defeats per-peer limits
                but still hits the shared global ceiling).

Memory: a rotating-pubkey attacker would otherwise grow the per-key map without
bound, so empty/expired windows are swept periodically. Steady-state footprint
is ~the number of distinct peers seen within one window.
"""
import os
import time
from collections import deque


class SlidingWindow:
    def __init__(self, max_events: int, window: float):
        self.max     = max_events
        self.window  = window
        self._hits: dict[str, deque[float]] = {}
        self._last_sweep = 0.0

    def _sweep(self, now: float) -> None:
        # Drop keys whose entire window has expired; bounds memory under a
        # rotating-pubkey flood. Cheap: runs at most once per window.
        if now - self._last_sweep < self.window:
            return
        cutoff = now - self.window
        for key in [k for k, dq in self._hits.items() if not dq or dq[-1] <= cutoff]:
            del self._hits[key]
        self._last_sweep = now

    def allow(self, key: str) -> bool:
        """Record a hit for key; return False if it exceeds the window budget."""
        now = time.monotonic()
        self._sweep(now)
        dq = self._hits.setdefault(key, deque())
        cutoff = now - self.window
        while dq and dq[0] <= cutoff:
            dq.popleft()
        if len(dq) >= self.max:
            return False
        dq.append(now)
        return True


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


# Defaults are deliberately generous for a real peer and tight for a flood.
_WINDOW    = float(_int_env("VOKTER_NOSTR_RATE_WINDOW", 60))
_per_peer  = SlidingWindow(_int_env("VOKTER_NOSTR_RATE_PER_PEER", 20), _WINDOW)
_global    = SlidingWindow(_int_env("VOKTER_NOSTR_RATE_GLOBAL", 120), _WINDOW)

_GLOBAL_KEY = "*"


def inbound_allowed(peer_key: str) -> bool:
    """True if an inbound message from peer_key may be processed.

    Per-peer budget FIRST: a peer over its own cap is rejected without spending a
    global slot, so one pubkey can consume at most its per-peer share of the
    global ceiling — it can't starve everyone else. A spread (Sybil) flood is
    still caught: distinct peers each pass per-peer, then accumulate against the
    shared global cap.
    """
    if not _per_peer.allow(peer_key):
        return False
    return _global.allow(_GLOBAL_KEY)


# A2A-over-HTTP reuses the SlidingWindow mechanism but keeps its OWN ceiling — a
# Nostr flood must not drain the A2A budget or vice versa. Only a GLOBAL cap
# here: per-IP belongs at the reverse proxy. Behind it every client appears as
# 127.0.0.1, and these counters are per-worker, so an in-app per-IP window would
# just throttle everyone to one peer's share. The global cap protects regardless
# of topology.
_a2a_global = SlidingWindow(
    _int_env("VOKTER_A2A_RATE_GLOBAL", 120),
    float(_int_env("VOKTER_A2A_RATE_WINDOW", 60)),
)


def a2a_allowed() -> bool:
    """True if another inbound /a2a request may be processed (global cap only)."""
    return _a2a_global.allow(_GLOBAL_KEY)
