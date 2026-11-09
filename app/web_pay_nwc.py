"""Tier-3 Phase 2 — web.pay NWC capped-session rail (the ONE path where Vokter holds a secret).

The user provisions a BUDGET-LIMITED NWC connection from their OWN wallet (Nostr Wallet Connect, NIP-47).
That connection secret is treated EXACTLY like the OAuth scoped token:
  * keychain ONLY (service 'vokter-nwc'), NEVER returned to any caller or the model,
  * asserted absent in the TIER-6 leakage probe,
  * scoped to a budget the WALLET enforces,
  * one-action revocable (delete locally; the user also deletes the connection in their wallet).

The WALLET holds the key and enforces the cap; Vokter's secret only authorizes up-to-budget pay_invoice
requests the wallet already agreed to — Vokter CANNOT move funds beyond the cap or hold the key.
Per-payment human-confirm stays MANDATORY by default (the cap is a CEILING, not a consent bypass);
unattended-within-budget is a separate, EXPLICIT opt-in (a stored per-alias cap) and is NOT wired to
bypass the confirm here — the default is conservative (confirm each payment).
"""
from __future__ import annotations

import json

_SERVICE = "vokter-nwc"
_INDEX = "nwc/__index__"


def _conn_name(alias: str) -> str:
    return f"nwc/{(alias or 'default').strip()}"


def validate_nwc_uri(uri: str) -> bool:
    """A real NWC connection URI parses to a wallet pubkey + relay(s) + secret. We validate shape via
    nostr_sdk; we never log or echo the URI (it carries the connection secret)."""
    try:
        from nostr_sdk import NostrWalletConnectUri
        u = NostrWalletConnectUri.parse((uri or "").strip())
        return bool(u.public_key() and u.relays() and u.secret())
    except Exception:
        return False


class NWCStore:
    """The user's budget-limited NWC connection(s) in the OS keychain. The URI (which contains the
    connection secret) is NEVER returned to a caller/model — only booleans, the extracted cap, and the
    list of aliases (non-secret). An index of aliases is kept because the keychain has no list op."""

    def _kc(self):
        import keychain
        return keychain

    def _idx(self) -> list[str]:
        raw = self._kc().get_key(service=_SERVICE, name=_INDEX)
        try:
            return json.loads(raw) if raw else []
        except Exception:
            return []

    def _set_idx(self, aliases: list[str]) -> None:
        self._kc().set_key(json.dumps(sorted(set(aliases))), service=_SERVICE, name=_INDEX)

    def put(self, alias: str, uri: str, unattended_cap_sats: int = 0) -> None:
        alias = (alias or "default").strip()
        self._kc().set_key(json.dumps({"uri": uri, "unattended_cap_sats": int(unattended_cap_sats or 0)}),
                           service=_SERVICE, name=_conn_name(alias))
        self._set_idx(self._idx() + [alias])

    def _blob(self, alias: str) -> dict | None:
        raw = self._kc().get_key(service=_SERVICE, name=_conn_name(alias))
        try:
            return json.loads(raw) if raw else None
        except Exception:
            return None

    def has(self, alias: str) -> bool:
        return self._blob(alias) is not None

    def cap(self, alias: str) -> int:
        b = self._blob(alias)
        return int(b.get("unattended_cap_sats", 0)) if b else 0

    def _uri(self, alias: str) -> str | None:
        """INTERNAL ONLY — the connection secret. Never surfaced to a response/model/log."""
        b = self._blob(alias)
        return b.get("uri") if b else None

    def delete(self, alias: str) -> bool:
        alias = (alias or "default").strip()
        ok = self._kc().delete_key(service=_SERVICE, name=_conn_name(alias))
        self._set_idx([a for a in self._idx() if a != alias])
        return ok

    def list_public(self) -> list[dict]:
        """The NWC menu — alias + whether an unattended cap is set. NEVER the URI/secret."""
        out = []
        for a in self._idx():
            b = self._blob(a)
            if b is not None:
                out.append({"alias": a, "unattended_cap_sats": int(b.get("unattended_cap_sats", 0))})
        return out


class InMemoryNWCStore:
    """Test double — provision a sentinel connection without a keychain. Never used in production."""
    def __init__(self):
        self._d: dict[str, dict] = {}
    def put(self, alias, uri, unattended_cap_sats=0):
        self._d[(alias or "default").strip()] = {"uri": uri, "unattended_cap_sats": int(unattended_cap_sats or 0)}
    def has(self, alias): return (alias or "default").strip() in self._d
    def cap(self, alias): return int(self._d.get((alias or "default").strip(), {}).get("unattended_cap_sats", 0))
    def _uri(self, alias): return self._d.get((alias or "default").strip(), {}).get("uri")
    def delete(self, alias): return self._d.pop((alias or "default").strip(), None) is not None
    def list_public(self):
        return [{"alias": a, "unattended_cap_sats": int(v.get("unattended_cap_sats", 0))} for a, v in self._d.items()]


# ───────────────────────── the NWC transport (real nostr_sdk; patchable seam) ─────────────────────────
def _nwc_transport(uri_str: str, bolt11: str) -> tuple[bool, str]:
    """Send a pay_invoice over the user's NWC connection (NIP-47, via nostr_sdk). The WALLET signs and
    enforces its budget; we get only success/failure. Returns (ok, status) — NEVER the uri/secret/preimage.
    Patched by the hermetic TIER-6 fire-test with a mock wallet (enforces a budget)."""
    import asyncio
    from nostr_sdk import NostrWalletConnect, NostrWalletConnectUri, PayInvoiceRequest

    async def _go():
        nwc = NostrWalletConnect(NostrWalletConnectUri.parse(uri_str))
        await nwc.pay_invoice(PayInvoiceRequest(invoice=bolt11))   # raises on over-budget / decline / unreachable

    try:
        asyncio.run(_go())
        return True, "paid"
    except Exception:
        return False, "failed"   # status only — never surface the exception (no secret, no detail leak)


def pay_via_nwc(store, alias: str, bolt11: str) -> tuple[bool, str]:
    """Pay `bolt11` over the stored connection. Reads the URI (secret) ONLY to hand it to the transport,
    drops the reference immediately, and returns status only — the secret never reaches the caller/model."""
    uri = store._uri(alias)
    if not uri:
        return False, "no_connection"
    try:
        return _nwc_transport(uri, bolt11)
    finally:
        del uri   # minimize the secret's lifetime (T15-style; a copy may transit nostr_sdk)
