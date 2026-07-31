"""C1 — la frontera de confianza A2A (invariante #2 de docs/SECURITY_REVIEW.md).

Fija, con test, la invariante que el docstring afirmaba sin imponer ("dataSharing:
none-without-permission") — la 2ª instancia del patrón del bug A2A original. Es SOLO un
test: no cambia comportamiento; la frontera ya está bien impuesta (fail-closed presente,
wallet_send no enrutado, dispatcher sin token humano).

Invariante:
  Un par A2A NO-trusted solo obtiene la tarjeta de identidad PÚBLICA (introduce/hello/whoami);
  cualquier otro verbo se rechaza con _UNTRUSTED_REPLY sin alcanzar herramienta ni dato.
  Un par TRUSTED (bearer == A2A_TOKEN) puede además usar ask/browse/wallet_balance/plan/
  negotiate — pero NO wallet_send (no enrutado → "Unknown tool", cero pago) — y ni su `ask`
  recibe memoria personal, porque el dispatcher autentica con admin_headers() (sin
  X-Vokter-Human-Session).

Doble tripwire (pensado para la regresión futura, no solo hoy):
  * CONDUCTUAL — un spy sobre `_http` falla si un caller no-trusted provoca CUALQUIER llamada
    de backend en un verbo privado → caza que se debilite/quite el gate.
  * DE FUENTE — el gate `if not trusted: return _UNTRUSTED_REPLY` debe aparecer ANTES de
    cualquier handler de verbo privado (`if tool ==`) → caza un verbo peligroso colado por
    encima del gate, que el conductual por sí solo no vería.

Ejecutar:  desktop/runtime/venv/bin/python tests/a2a_trust_boundary_test.py
"""
import asyncio
import json
import os
import pathlib
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="vokter_a2a_boundary_test_")
os.environ["VOKTER_DB"] = os.path.join(_TMP, "test.db")
os.environ.pop("VOKTER_DB_KEY", None)
os.environ["VOKTER_A2A_TOKEN"]   = "A2ATEST_deadbeef"     # what "trusted" means today: one shared bearer
os.environ["VOKTER_ADMIN_TOKEN"] = "ADMINTEST_cafef00d"   # so the dispatcher's client is a real authed one
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import agent_dispatch
from agent_dispatch import dispatch_message, _UNTRUSTED_REPLY
import a2a_server
import auth

_BASE = agent_dispatch._BASE

# Capture the REAL client headers the dispatcher authenticates with, BEFORE swapping in a spy.
_REAL_HTTP_HEADERS = {k.lower() for k in dict(agent_dispatch._http.headers).keys()}


class _Resp:
    def __init__(self, payload): self._payload = payload
    def raise_for_status(self): pass
    def json(self): return self._payload


class _SpyStream:
    async def __aenter__(self): return self
    async def __aexit__(self, *exc): return False
    def raise_for_status(self): pass
    async def aiter_text(self):
        yield 'data: {"type": "done", "answer": "planned"}\n'


class _SpyHTTP:
    """Stands in for the dispatcher's httpx client and RECORDS every backend call, so an
    untrusted private verb making any call is a test failure."""
    def __init__(self): self.calls = []

    async def get(self, url, **kw):
        self.calls.append(("GET", url))
        if url.endswith("/api/wallet/balance"):
            return _Resp({"balance": 0, "unit": "sat", "adapter": "stub"})
        return _Resp({"name": "Vokter", "kind": "public-card"})     # /api/agent/card

    async def post(self, url, **kw):
        self.calls.append(("POST", url))
        if url.endswith("/api/ask"):
            return _Resp({"answer": "answer-text", "conversation_id": "cid"})
        if url.endswith("/api/browse"):
            return _Resp({"chunks": 1, "doc": "d"})
        return _Resp({})

    def stream(self, method, url, **kw):
        self.calls.append((method, url))
        return _SpyStream()


def _fresh_spy():
    spy = _SpyHTTP()
    agent_dispatch._http = spy
    return spy


def _dispatch(tool, trusted, args=None, ctx="ctx"):
    payload = json.dumps({"tool": tool, "args": args or {}})
    return asyncio.run(dispatch_message(payload, ctx, trusted=trusted))


def test_untrusted_gets_only_public_card():
    spy = _fresh_spy()
    for v in ("introduce", "hello", "whoami"):
        out = _dispatch(v, trusted=False)
        assert "public-card" in out, f"{v!r} (untrusted) must return the public card, got {out!r}"
    assert spy.calls == [("GET", f"{_BASE}/api/agent/card")] * 3, spy.calls


def test_untrusted_private_verbs_refused_zero_http():
    # CONDUCTUAL tripwire: refused with the untrusted reply AND no backend call whatsoever.
    spy = _fresh_spy()
    private = ["ask", "browse", "wallet_balance", "plan", "negotiate",
               "wallet_send", "delete_everything", "memory", "config"]
    for v in private:
        out = _dispatch(v, trusted=False)
        assert out == _UNTRUSTED_REPLY, f"untrusted {v!r} must be refused, got {out!r}"
    assert spy.calls == [], f"untrusted private verbs must make ZERO backend calls, got {spy.calls}"


def test_trusted_wallet_send_not_routed_no_payment():
    spy = _fresh_spy()
    out = _dispatch("wallet_send", trusted=True, args={"amount": 100, "destination": "x"})
    assert "Unknown tool" in out, f"wallet_send must NOT be routed via A2A, got {out!r}"
    assert spy.calls == [], f"wallet_send must reach no tool (no payment), got {spy.calls}"


def test_trusted_allowed_verbs_are_routed():
    import negotiation
    saved = getattr(negotiation, "handle_inbound", None)
    negotiation.handle_inbound = lambda ck, a: "NEGOTIATED"
    try:
        cases = {
            "ask":            ("POST", "/api/ask"),
            "browse":         ("POST", "/api/browse"),
            "wallet_balance": ("GET",  "/api/wallet/balance"),
            "plan":           ("POST", "/api/plan"),
        }
        for i, (verb, (meth, path)) in enumerate(cases.items()):
            spy = _fresh_spy()
            out = _dispatch(verb, trusted=True,
                            args={"question": "q", "url": "http://x", "goal": "g"}, ctx=f"c{i}")
            assert out != _UNTRUSTED_REPLY and "Unknown tool" not in out, \
                f"{verb!r} must be routed for a trusted peer, got {out!r}"
            assert any(m == meth and path in u for (m, u) in spy.calls), \
                f"{verb!r} must hit {meth} {path}, calls={spy.calls}"
        # negotiate routes to the negotiation handler (no _http).
        out = _dispatch("negotiate", trusted=True)
        assert out == "NEGOTIATED", f"negotiate must route to handle_inbound, got {out!r}"
    finally:
        if saved is not None:
            negotiation.handle_inbound = saved


def test_dispatcher_never_carries_human_session_token():
    # The original bug: personal memory riding A2A into a peer. The dispatcher authenticates
    # with admin_headers() (admin token only) — never the human-session token — so a trusted
    # `ask` hits /api/ask with human=False and memory is withheld. Lock that at the source.
    assert "x-vokter-human-session" not in _REAL_HTTP_HEADERS, _REAL_HTTP_HEADERS
    assert "x-vokter-human-session" not in {k.lower() for k in auth.admin_headers()}
    assert "x-vokter-admin-token" in _REAL_HTTP_HEADERS, \
        f"sanity: expected a real authed client (admin token), got {_REAL_HTTP_HEADERS}"


def test_is_trusted_is_the_single_shared_token():
    class _Req:
        def __init__(self, authz):
            self.headers = {"authorization": authz} if authz is not None else {}
    assert a2a_server._is_trusted(_Req("Bearer A2ATEST_deadbeef")) is True
    assert a2a_server._is_trusted(_Req("Bearer wrong")) is False
    assert a2a_server._is_trusted(_Req("A2ATEST_deadbeef")) is False   # missing 'Bearer ' scheme
    assert a2a_server._is_trusted(_Req(None)) is False                 # no Authorization header
    saved = a2a_server.A2A_TOKEN
    a2a_server.A2A_TOKEN = ""                                          # no token → nobody is trusted
    try:
        assert a2a_server._is_trusted(_Req("Bearer A2ATEST_deadbeef")) is False
    finally:
        a2a_server.A2A_TOKEN = saved


def test_source_tripwire_gate_precedes_private_handlers():
    # DE FUENTE tripwire: the fail-closed gate must come BEFORE any private-verb handler.
    # Catches a dangerous verb inserted above the gate — invisible to the behavioural test.
    src = pathlib.Path(agent_dispatch.__file__).read_text()
    gate = src.index("return _UNTRUSTED_REPLY")     # the `if not trusted:` fail-closed default
    first_private = src.index("if tool ==")          # first private-verb handler (public uses `in`)
    assert gate < first_private, (
        "FAIL-OPEN REGRESSION: a private-verb handler ('if tool ==') appears BEFORE the "
        "`if not trusted: return _UNTRUSTED_REPLY` gate — an untrusted caller could reach it."
    )


def main():
    test_untrusted_gets_only_public_card()
    test_untrusted_private_verbs_refused_zero_http()
    test_trusted_wallet_send_not_routed_no_payment()
    test_trusted_allowed_verbs_are_routed()
    test_dispatcher_never_carries_human_session_token()
    test_is_trusted_is_the_single_shared_token()
    test_source_tripwire_gate_precedes_private_handlers()
    print("OK — A2A trust boundary: untrusted gets only the public card (zero private HTTP), "
          "trusted gets ask/browse/wallet_balance/plan/negotiate but NOT wallet_send "
          "('Unknown tool', no payment), dispatcher never carries the human token (memory "
          "stays off A2A), 'trusted' is the single shared bearer, and the fail-closed gate "
          "precedes every private handler (source tripwire).")


if __name__ == "__main__":
    main()
