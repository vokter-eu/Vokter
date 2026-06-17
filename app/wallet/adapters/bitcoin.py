"""
Bitcoin on-chain adapter.

Non-custodial BTC: your private key never leaves Vokter.
Uses Bitcoin Core RPC for blockchain access — run your own node.

Required env vars:
  VOKTER_BTC_RPC_URL    e.g. http://127.0.0.1:8332
  VOKTER_BTC_RPC_USER   Bitcoin Core rpcuser
  VOKTER_BTC_RPC_PASS   Bitcoin Core rpcpassword

Activate: VOKTER_WALLET_ADAPTER=bitcoin

Optional install:
  pip install python-bitcoinrpc

Implementation TODOs:
  balance()  — getbalance RPC call, convert BTC to sat
  receive()  — getnewaddress → return address in tx.output
  send()     — sendtoaddress (amount in BTC)
  history()  — listtransactions

For privacy, consider routing your Bitcoin Core node through Tor
and using BIP-84 (native SegWit) addresses.
"""
import os

from fastapi import HTTPException

from wallet import WalletAdapter, Transaction

_URL  = os.getenv("VOKTER_BTC_RPC_URL",  "")
_USER = os.getenv("VOKTER_BTC_RPC_USER", "")
_PASS = os.getenv("VOKTER_BTC_RPC_PASS", "")


class BitcoinAdapter(WalletAdapter):
    name = "bitcoin"
    unit = "sat"

    def _require_config(self) -> None:
        if not _URL:
            raise HTTPException(
                501,
                "Bitcoin adapter not configured. "
                "Set VOKTER_BTC_RPC_URL. "
                "See wallet/adapters/bitcoin.py for full instructions.",
            )

    async def balance(self) -> int:
        self._require_config()
        # TODO: call getbalance via JSON-RPC, multiply by 1e8 for sat
        raise HTTPException(501, "Bitcoin balance not yet implemented")

    async def receive(self, _: str) -> Transaction:
        self._require_config()
        # TODO: getnewaddress → return address in tx.output
        raise HTTPException(501, "Bitcoin receive not yet implemented")

    async def send(self, amount: int, address: str = "", memo: str = "") -> Transaction:
        self._require_config()
        # TODO: sendtoaddress (amount = amount / 1e8 BTC)
        raise HTTPException(501, "Bitcoin send not yet implemented")

    async def history(self) -> list[Transaction]:
        self._require_config()
        # TODO: listtransactions
        raise HTTPException(501, "Bitcoin history not yet implemented")
