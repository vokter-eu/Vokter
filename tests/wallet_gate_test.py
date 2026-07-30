"""C3 — el gate de wallet_send. Mover fondos exige el token de sesión humana (P2/C3).

Prueba la invariante que importa, como el gate de memoria, sin red ni modelo:
  * SIN marca humana (None/""/incorrecta) → wallet_send DENIEGA (403) y adapter.send()
                        NO se alcanza — un par/MCP/navegador plano nunca mueve fondos.
  * CON marca humana válida → pasa el gate y LLEGA a adapter.send().
  * Sin token configurado (dev crudo, sin Electron) → deny estricto: toda marca deniega.

Tripwire (regla de invariantes): wallet_send es HOY el ÚNICO camino a los fondos —
solo wallet_routes.py obtiene un adapter con get_active_adapter(). Si un cambio futuro
cablea el scheduler/negotiation para pagar solos, este test se ROMPE, forzando el diseño
de pre-autorización (mandato, vokter-C3-plan.md §4) ANTES de shippear auto-pago. El gate
de token solo cierra porque este camino es único.

Ejecutar:  desktop/runtime/venv/bin/python tests/wallet_gate_test.py
"""
import asyncio
import os
import pathlib
import subprocess
import sys
import tempfile

# El env debe fijarse ANTES de importar módulos respaldados por config. DB sqlite PLANA
# y token conocido para el test. Límite de gasto 0 (por defecto) → sin rama de límite.
_TMP = tempfile.mkdtemp(prefix="vokter_walletgate_test_")
os.environ["VOKTER_DB"] = os.path.join(_TMP, "test.db")
os.environ.pop("VOKTER_DB_KEY", None)
os.environ["VOKTER_HUMAN_SESSION_TOKEN"] = "TESTTOKEN_wallet_cafe"
os.environ["VOKTER_WALLET_SPEND_LIMIT"] = "0"
_APP = os.path.join(os.path.dirname(__file__), "..", "app")
sys.path.insert(0, _APP)

import wallet_routes
from wallet_routes import wallet_send, SendRequest
from fastapi import HTTPException

_TOKEN = "TESTTOKEN_wallet_cafe"


class _ReachedSend(Exception):
    """Raised by the stub adapter to prove control reached adapter.send()."""


class _StubAdapter:
    name = "stub"
    unit = "sat"

    def __init__(self):
        self.send_called = False

    async def send(self, amount, destination="", memo=""):
        self.send_called = True
        raise _ReachedSend()          # we only need to prove we GOT here — no DB, no funds


def _install_stub():
    stub = _StubAdapter()
    wallet_routes.get_active_adapter = lambda: stub   # patch the sole adapter source
    return stub


def test_no_human_mark_denied_and_send_not_reached():
    stub = _install_stub()
    for mark in (None, "", "wrong-token"):
        try:
            asyncio.run(wallet_send(
                SendRequest(amount=10, destination="x", confirmed=True),
                x_vokter_human_session=mark,
            ))
            assert False, f"expected 403 for mark={mark!r}"
        except HTTPException as e:
            assert e.status_code == 403, f"expected 403, got {e.status_code} for mark={mark!r}"
    assert stub.send_called is False, "adapter.send() must NEVER be reached without a human mark"


def test_valid_human_mark_reaches_send():
    stub = _install_stub()
    wallet_routes._send_lock = asyncio.Lock()   # fresh lock → binds to THIS asyncio.run loop
    try:
        asyncio.run(wallet_send(
            SendRequest(amount=10, destination="x", confirmed=True),
            x_vokter_human_session=_TOKEN,
        ))
        assert False, "with a valid mark, control should reach adapter.send() (stub raises)"
    except _ReachedSend:
        pass
    assert stub.send_called is True, "a valid human mark must be allowed through to send()"


def test_header_wiring_through_asgi():
    """Prueba el CABLEADO del header por la pila ASGI REAL (no un kwarg de Python): que
    FastAPI mapea la cabecera X-Vokter-Human-Session → el parámetro x_vokter_human_session
    y la lee de cabeceras, no del body. "Si falla, es cableado del token, no la lógica" — el
    mismo estándar causal del gate #1 (chat.py usa el parámetro idéntico, validado en VM)."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    stub = _install_stub()
    wallet_routes._send_lock = asyncio.Lock()   # fresh lock → binds to the TestClient loop
    app = FastAPI()
    app.include_router(wallet_routes.router)
    client = TestClient(app, raise_server_exceptions=False)
    body = {"amount": 10, "destination": "x", "confirmed": True}

    # Sin cabecera → el gate deniega (403) y send() no se alcanza.
    r = client.post("/api/wallet/send", json=body)
    assert r.status_code == 403, f"no header must be denied, got {r.status_code}"
    assert stub.send_called is False, "adapter.send() must not be reached without the header"

    # Con la cabecera REAL → pasa el gate y llega a send() (el stub lo interrumpe → no 403).
    r = client.post("/api/wallet/send", json=body,
                    headers={"X-Vokter-Human-Session": _TOKEN})
    assert r.status_code != 403, f"valid header must pass the gate, got {r.status_code}"
    assert stub.send_called is True, "the real header must reach adapter.send() through ASGI"


def test_confirmed_is_not_the_boundary():
    # confirmed=True but NO human mark → still denied (403), proving the flag is not security.
    stub = _install_stub()
    try:
        asyncio.run(wallet_send(
            SendRequest(amount=10, destination="x", confirmed=True),
            x_vokter_human_session=None,
        ))
        assert False, "confirmed=True must NOT let an unauthenticated caller pay"
    except HTTPException as e:
        assert e.status_code == 403
    assert stub.send_called is False


def test_deny_by_default_when_no_token_configured():
    stub = _install_stub()
    import chat
    saved = chat.HUMAN_SESSION_TOKEN
    chat.HUMAN_SESSION_TOKEN = ""            # raw dev / docker, no Electron minting a token
    try:
        for mark in ("", "anything", _TOKEN):
            try:
                asyncio.run(wallet_send(
                    SendRequest(amount=10, destination="x", confirmed=True),
                    x_vokter_human_session=mark,
                ))
                assert False, "no token configured → every mark must be denied (strict)"
            except HTTPException as e:
                assert e.status_code == 403
    finally:
        chat.HUMAN_SESSION_TOKEN = saved
    assert stub.send_called is False


def test_tripwire_only_wallet_routes_can_obtain_a_paying_adapter():
    """Invariante-como-test: mover fondos necesita get_active_adapter(); HOY solo lo llama
    wallet_routes.py. Si el scheduler/negotiation empieza a obtener un adapter para auto-pago,
    esto se rompe → obliga a resolver la pre-autorización (mandato) antes de shippear."""
    app_dir = pathlib.Path(_APP).resolve()
    out = subprocess.run(
        ["grep", "-rn", "get_active_adapter", str(app_dir), "--include=*.py"],
        capture_output=True, text=True,
    ).stdout.strip().splitlines()
    offenders = []
    for line in out:
        if "__pycache__" in line:
            continue
        # The definition lives in app/wallet/adapters/__init__.py; callers elsewhere are the risk.
        if f"{os.sep}wallet{os.sep}" in line:
            continue
        if "wallet_routes.py" in line:
            continue
        offenders.append(line)
    assert not offenders, (
        "NEW get_active_adapter() user(s) outside wallet_routes — a fund path that bypasses "
        f"the human-token gate. Gate auto-pay first (vokter-C3-plan.md §4):\n" + "\n".join(offenders)
    )


def main():
    test_no_human_mark_denied_and_send_not_reached()
    test_valid_human_mark_reaches_send()
    test_header_wiring_through_asgi()
    test_confirmed_is_not_the_boundary()
    test_deny_by_default_when_no_token_configured()
    test_tripwire_only_wallet_routes_can_obtain_a_paying_adapter()
    print("OK — wallet gate: DENIED without a human mark (send() never reached), allowed WITH "
          "it, confirmed is not the boundary, deny-by-default with no token, and wallet_send "
          "is the sole fund path (tripwire).")


if __name__ == "__main__":
    main()
