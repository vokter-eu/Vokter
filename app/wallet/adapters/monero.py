"""
Monero (XMR) adapter — untraceable by design.

Monero transactions are unlinkable at the protocol level via ring signatures,
stealth addresses, and RingCT. No on-chain correlation is possible without
your private view key. This is the most private payment rail available.

Set up monero-wallet-rpc (run alongside your Vokter Docker stack):

  monero-wallet-rpc \\
    --wallet-file /path/to/your-wallet \\
    --password "" \\
    --rpc-bind-port 18082 \\
    --disable-rpc-login \\
    --daemon-address node.moneroworld.com:18089   # or your own node

Required env vars:
  VOKTER_MONERO_RPC_URL   e.g. http://127.0.0.1:18082/json_rpc

Optional Python client (makes implementation easier):
  pip install monero

Activate: VOKTER_WALLET_ADAPTER=monero

Implementation TODOs:
  balance()  — POST {_RPC} {"method":"get_balance","params":{"account_index":0}}
  receive()  — POST {_RPC} {"method":"get_address"}  → show stealth address
  send()     — POST {_RPC} {"method":"transfer","params":{"destinations":[{"amount":...,"address":...}]}}
  history()  — POST {_RPC} {"method":"get_transfers","params":{"in":true,"out":true}}
"""
import os

from fastapi import HTTPException

from wallet import WalletAdapter, Transaction

_RPC = os.getenv("VOKTER_MONERO_RPC_URL", "")


class MoneroAdapter(WalletAdapter):
    name = "monero"
    unit = "xmr"

    def _require_config(self) -> None:
        if not _RPC:
            raise HTTPException(
                501,
                "Monero adapter not configured. "
                "Set VOKTER_MONERO_RPC_URL. "
                "See wallet/adapters/monero.py for full instructions.",
            )

    async def balance(self) -> int:
        self._require_config()
        # TODO: call get_balance via JSON-RPC, return unlocked_balance in piconero
        raise HTTPException(501, "Monero balance not yet implemented")

    async def receive(self, _: str) -> Transaction:
        self._require_config()
        # TODO: get_address → return stealth address in tx.output
        raise HTTPException(501, "Monero receive not yet implemented")

    async def send(self, amount: int, address: str = "", memo: str = "") -> Transaction:
        self._require_config()
        # TODO: transfer via JSON-RPC; amount in piconero (1 XMR = 1e12 piconero)
        raise HTTPException(501, "Monero send not yet implemented")

    async def history(self) -> list[Transaction]:
        self._require_config()
        # TODO: get_transfers with in=True, out=True
        raise HTTPException(501, "Monero history not yet implemented")
