"""
ERC-20 stablecoin adapter — EURC, EURe, EURCV.

MiCA-regulated euro-denominated stablecoins on Ethereum / Gnosis Chain.
Non-custodial: your private key never leaves your machine.

Required env vars:
  VOKTER_ETH_RPC_URL       local or trusted RPC, e.g. http://geth:8545
  VOKTER_ETH_PRIVATE_KEY   hex private key (0x…) — kept only in your env

Install:
  pip install web3

Supported tokens (set VOKTER_WALLET_ADAPTER to the token name):
  eurc   — EURC by Circle         (Ethereum: 0x1aBaEA1f7C830bD89Acc67eC4af516284b1bC33)
  eure   — EURe by Monerium       (Gnosis Chain: 0xcB444e90D8198415266c6a2724b7900fb12FC56E)
  eurcv  — EURCV by Société Générale (Ethereum: 0x5B2BFF57024F0C0D9f31F23D95df0e55d5e71f50)

Activate: VOKTER_WALLET_ADAPTER=eurc  (or eure / eurcv)

Implementation TODOs:
  balance() — web3.eth.contract(abi=ERC20_ABI, address=contract).functions.balanceOf(account).call()
  receive() — return your wallet address so the sender can transfer to it
  send()    — build + sign + send ERC-20 transfer transaction locally
  history() — filter Transfer events from the contract
"""
import os

from fastapi import HTTPException

from wallet import WalletAdapter, Transaction

_RPC = os.getenv("VOKTER_ETH_RPC_URL",     "")
_KEY = os.getenv("VOKTER_ETH_PRIVATE_KEY", "")

_CONTRACTS: dict[str, str] = {
    "eurc":  "0x1aBaEA1f7C830bD89Acc67eC4af516284b1bC33",  # Ethereum mainnet
    "eure":  "0xcB444e90D8198415266c6a2724b7900fb12FC56E",  # Gnosis Chain
    "eurcv": "0x5B2BFF57024F0C0D9f31F23D95df0e55d5e71f50",  # Ethereum mainnet
}


class ERC20Adapter(WalletAdapter):

    def __init__(self, token: str) -> None:
        self.name = token.lower()
        self.unit = token.upper()
        self._contract = _CONTRACTS.get(self.name, "")

    def _require_config(self) -> None:
        if not _RPC or not _KEY:
            raise HTTPException(
                501,
                f"{self.unit} adapter not configured. "
                "Set VOKTER_ETH_RPC_URL and VOKTER_ETH_PRIVATE_KEY. "
                "See wallet/adapters/erc20.py for full instructions.",
            )

    async def balance(self) -> int:
        self._require_config()
        # TODO: from web3 import Web3
        #   w3 = Web3(Web3.HTTPProvider(_RPC))
        #   contract = w3.eth.contract(address=self._contract, abi=ERC20_ABI)
        #   account = w3.eth.account.from_key(_KEY).address
        #   raw = contract.functions.balanceOf(account).call()
        #   return raw // 10**6  # EURC/EURCV use 6 decimals; EURe uses 18
        raise HTTPException(501, f"{self.unit} balance: install web3 and implement (see erc20.py)")

    async def receive(self, _: str) -> Transaction:
        self._require_config()
        # TODO: return your wallet address so the sender can transfer to it
        #   from web3 import Web3; account = Web3().eth.account.from_key(_KEY).address
        raise HTTPException(501, f"{self.unit} receive not yet implemented")

    async def send(self, amount: int, to_address: str = "", memo: str = "") -> Transaction:
        self._require_config()
        # TODO: build ERC-20 transfer calldata, sign locally with _KEY, broadcast
        raise HTTPException(501, f"{self.unit} send not yet implemented")

    async def history(self) -> list[Transaction]:
        self._require_config()
        # TODO: query Transfer(from, to, value) events from the contract
        raise HTTPException(501, f"{self.unit} history not yet implemented")
