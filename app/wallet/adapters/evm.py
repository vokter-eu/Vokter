"""
EVM (Ethereum-compatible) adapter — any ERC-20 token on any EVM chain.

Works with Ethereum, Polygon, Base, Gnosis Chain, Avalanche C-Chain,
Arbitrum, Optimism, or any future EVM-compatible network. Same code,
different RPC URL and contract address. Non-custodial: private key
never leaves Vokter.

Required env vars:
  VOKTER_EVM_RPC_URL         your node's RPC endpoint (HTTP/WS)
  VOKTER_EVM_PRIVATE_KEY     hex private key (0x…) — stays in your env
  VOKTER_EVM_TOKEN_CONTRACT  ERC-20 contract address (leave empty for native ETH/MATIC/…)
  VOKTER_EVM_TOKEN_SYMBOL    display label, e.g. EURC, EURe, EURCV, ETH
  VOKTER_EVM_TOKEN_DECIMALS  token decimals (default 18; EURC and EURCV use 6)
  VOKTER_EVM_CHAIN           human label only — ethereum | polygon | base | gnosis | avalanche | …

Install:
  pip install web3

── Known contract addresses (verify from issuer's official docs before use) ──

EURC (Circle):
  Ethereum  → 0x1aBaEA1f7C830bD89Acc67eC4af516284b1bC33  (6 decimals)
  Avalanche → 0xC891EB4cbdEFf6e073e859e987815Ed1505c2ACD  (6 decimals)
  Base      → 0x60a3E35Cc302bFA44Cb288Bc5a4F316Fdb1adb42  (6 decimals)

EURe (Monerium):
  Ethereum  → 0x3231Cb76718CDeF2155FC47b5286d82e6eDA273f  (18 decimals)
  Gnosis    → 0xcB444e90D8198415266c6a2724b7900fb12FC56E  (18 decimals)
  Polygon   → 0x18ec0A6E18E5bc3784fDd3a3634b31245ab704F6  (18 decimals)

EURCV (Société Générale — EUR CoinVertible):
  Ethereum  → verify at socgen.com / Etherscan               (6 decimals)

New EU bank stablecoins (chain unknown at build time):
  Set VOKTER_WALLET_ADAPTER=evm and fill the four env vars above
  once the issuer publishes the contract address and chain.

Activate presets : VOKTER_WALLET_ADAPTER=eurc | eure | eurcv
Activate generic : VOKTER_WALLET_ADAPTER=evm  (fully manual)
"""
import os

from fastapi import HTTPException

from wallet import WalletAdapter, Transaction

_RPC      = os.getenv("VOKTER_EVM_RPC_URL",         "")
_KEY      = os.getenv("VOKTER_EVM_PRIVATE_KEY",      "")
_CONTRACT = os.getenv("VOKTER_EVM_TOKEN_CONTRACT",   "")
_SYMBOL   = os.getenv("VOKTER_EVM_TOKEN_SYMBOL",     "EVM")
_DECIMALS = int(os.getenv("VOKTER_EVM_TOKEN_DECIMALS", "18"))
_CHAIN    = os.getenv("VOKTER_EVM_CHAIN",             "")

# Known decimal places for MiCA stablecoins when no env override is provided.
# EURC and EURCV use 6 decimals; EURe uses 18. Without this mapping, a preset
# like `eurc` would silently use 18 decimals and send 10^12 × the intended amount.
_PRESET_DECIMALS: dict[str, int] = {
    "eurc":  6,
    "eurcv": 6,
    "eure":  18,
}

# Minimum ERC-20 ABI — only the functions we need
_ERC20_ABI = [
    {"name": "balanceOf",  "type": "function", "stateMutability": "view",
     "inputs":  [{"name": "account", "type": "address"}],
     "outputs": [{"name": "", "type": "uint256"}]},
    {"name": "decimals",   "type": "function", "stateMutability": "view",
     "inputs":  [], "outputs": [{"name": "", "type": "uint8"}]},
    {"name": "transfer",   "type": "function", "stateMutability": "nonpayable",
     "inputs":  [{"name": "to", "type": "address"}, {"name": "amount", "type": "uint256"}],
     "outputs": [{"name": "", "type": "bool"}]},
]


class EVMAdapter(WalletAdapter):
    """Handles any ERC-20 token (or native coin) on any EVM-compatible chain."""

    def __init__(self, preset_symbol: str = "") -> None:
        self.name = preset_symbol.lower() if preset_symbol else "evm"
        self.unit = preset_symbol.upper() if preset_symbol else _SYMBOL
        # Preset wins over env var for known tokens; env var overrides both.
        env_override = os.getenv("VOKTER_EVM_TOKEN_DECIMALS")
        self._decimals = (
            int(env_override) if env_override
            else _PRESET_DECIMALS.get(self.name, _DECIMALS)
        )

    def _require_config(self) -> None:
        if not _RPC or not _KEY:
            raise HTTPException(
                501,
                f"{self.unit} adapter not configured. "
                "Set VOKTER_EVM_RPC_URL and VOKTER_EVM_PRIVATE_KEY. "
                "See wallet/adapters/evm.py for contract addresses.",
            )

    def _client(self):
        """Return a web3 instance. Import lazily so missing web3 doesn't crash startup."""
        try:
            from web3 import Web3
        except ImportError:
            raise HTTPException(
                501,
                "web3 package not installed. Run: pip install web3  "
                "then restart Vokter.",
            )
        w3 = Web3(Web3.HTTPProvider(_RPC))
        if not w3.is_connected():
            raise HTTPException(502, f"Cannot reach EVM RPC at {_RPC!r}")
        return w3

    def _account(self, w3):
        return w3.eth.account.from_key(_KEY)

    async def balance(self) -> int:
        self._require_config()
        w3 = self._client()
        acct = self._account(w3)
        if _CONTRACT:
            contract = w3.eth.contract(
                address=w3.to_checksum_address(_CONTRACT),
                abi=_ERC20_ABI,
            )
            raw = contract.functions.balanceOf(acct.address).call()
            return raw // (10 ** self._decimals)
        else:
            # native coin (ETH, MATIC, …)
            wei = w3.eth.get_balance(acct.address)
            return wei // (10 ** 18)

    async def receive(self, _: str) -> Transaction:
        self._require_config()
        w3 = self._client()
        acct = self._account(w3)
        # Return deposit address so the sender can transfer to it
        return Transaction.new(
            self.name, "in", 0, self.unit,
            memo="deposit address",
            output=acct.address,
        )

    async def send(self, amount: int, to_address: str = "", memo: str = "") -> Transaction:
        self._require_config()
        if not to_address:
            raise HTTPException(400, "destination address is required")
        w3 = self._client()
        acct = self._account(w3)
        to = w3.to_checksum_address(to_address)

        if _CONTRACT:
            contract = w3.eth.contract(
                address=w3.to_checksum_address(_CONTRACT),
                abi=_ERC20_ABI,
            )
            raw_amount = amount * (10 ** self._decimals)
            tx = contract.functions.transfer(to, raw_amount).build_transaction({
                "from":  acct.address,
                "nonce": w3.eth.get_transaction_count(acct.address),
                "gas":   100_000,
                "gasPrice": w3.eth.gas_price,
            })
        else:
            raw_amount = amount * (10 ** 18)
            tx = {
                "to":    to,
                "value": raw_amount,
                "from":  acct.address,
                "nonce": w3.eth.get_transaction_count(acct.address),
                "gas":   21_000,
                "gasPrice": w3.eth.gas_price,
            }

        signed = acct.sign_transaction(tx)
        # web3.py v6 uses raw_transaction (snake_case); v5 used rawTransaction.
        raw_tx = getattr(signed, "raw_transaction", None) or getattr(signed, "rawTransaction", None)
        if not raw_tx:
            raise HTTPException(500, "Failed to serialize signed transaction (unexpected web3 version?)")
        tx_hash = w3.eth.send_raw_transaction(raw_tx)
        return Transaction.new(
            self.name, "out", amount, self.unit,
            memo=memo,
            output=tx_hash.hex(),
        )

    async def history(self) -> list[Transaction]:
        self._require_config()
        # TODO: query Transfer events from the contract via eth_getLogs
        # or use a local indexer (The Graph self-hosted, or Blockscout)
        raise HTTPException(501, f"{self.unit} history: query Transfer events via eth_getLogs")
