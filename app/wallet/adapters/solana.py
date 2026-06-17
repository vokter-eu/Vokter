"""
Solana adapter — native SOL and SPL tokens.

Covers EURC on Solana (Circle), EURe on Solana (Monerium), and any
future SPL token a European bank might issue. Non-custodial: your
keypair never leaves Vokter.

Required env vars:
  VOKTER_SOLANA_RPC_URL        RPC endpoint, e.g. http://solana-validator:8899
                                or a self-hosted RPC node
  VOKTER_SOLANA_PRIVATE_KEY    base58-encoded secret key (64-byte keypair)
  VOKTER_SOLANA_TOKEN_MINT     SPL token mint address (leave empty for native SOL)
  VOKTER_SOLANA_TOKEN_SYMBOL   display label, e.g. EURC, EURe, SOL

Install:
  pip install solana solders

── Known SPL token mint addresses (verify from issuer before use) ──

EURC (Circle on Solana):
  mint → HzwqbKZw8HxMN6bF2yFZNrht3c2iXXzpKcFu7uBEDKtr  (6 decimals)

EURe (Monerium on Solana):
  mint → verify at monerium.com / Solscan                   (6 decimals, provisional)

New EU bank stablecoins on Solana (chain not yet announced):
  Set VOKTER_WALLET_ADAPTER=solana and VOKTER_SOLANA_TOKEN_MINT=<mint>
  once the issuer publishes the mint address.

Activate presets : VOKTER_WALLET_ADAPTER=sol | eurc-solana | eure-solana
Activate generic : VOKTER_WALLET_ADAPTER=solana  (fully manual via env vars)
"""
import os

from fastapi import HTTPException

from wallet import WalletAdapter, Transaction

_RPC     = os.getenv("VOKTER_SOLANA_RPC_URL",      "")
_KEY_B58 = os.getenv("VOKTER_SOLANA_PRIVATE_KEY",  "")
_MINT    = os.getenv("VOKTER_SOLANA_TOKEN_MINT",    "")
_SYMBOL  = os.getenv("VOKTER_SOLANA_TOKEN_SYMBOL",  "SOL")

# Preset mint addresses. Users must verify these from official issuer docs.
_KNOWN_MINTS: dict[str, tuple[str, int]] = {
    # (mint_address, decimals)
    "eurc-solana": ("HzwqbKZw8HxMN6bF2yFZNrht3c2iXXzpKcFu7uBEDKtr", 6),
    "eure-solana": ("",  6),   # TODO: fill once Monerium publishes Solana mint
}


class SolanaAdapter(WalletAdapter):
    """Handles native SOL or any SPL token on the Solana network."""

    def __init__(self, preset: str = "") -> None:
        self.name = preset if preset else ("sol" if not _MINT else "solana")
        self._is_native = (preset == "sol") or (not preset and not _MINT)
        self._preset = preset
        # Determine mint + symbol from preset or env vars
        if preset in _KNOWN_MINTS:
            self._mint, self._decimals = _KNOWN_MINTS[preset]
            self.unit = preset.split("-")[0].upper()  # "eurc-solana" → "EURC"
        else:
            self._mint    = _MINT
            self._decimals = 6 if _MINT else 9   # SOL has 9 decimals (lamports)
            self.unit     = _SYMBOL if not self._is_native else "SOL"

    def _require_config(self) -> None:
        if not _RPC or not _KEY_B58:
            raise HTTPException(
                501,
                f"{self.unit} (Solana) adapter not configured. "
                "Set VOKTER_SOLANA_RPC_URL and VOKTER_SOLANA_PRIVATE_KEY. "
                "See wallet/adapters/solana.py for mint addresses.",
            )
        if not self._is_native and not self._mint:
            raise HTTPException(
                501,
                f"SPL token mint address not set. "
                "Set VOKTER_SOLANA_TOKEN_MINT to the token's mint address.",
            )

    def _client(self):
        try:
            from solana.rpc.api import Client
        except ImportError:
            raise HTTPException(
                501,
                "solana package not installed. Run: pip install solana solders  "
                "then restart Vokter.",
            )
        return Client(_RPC)

    def _keypair(self):
        try:
            from solders.keypair import Keypair  # type: ignore[import]
            import base58
        except ImportError:
            raise HTTPException(501, "Install: pip install solana solders base58")
        secret = base58.b58decode(_KEY_B58)
        return Keypair.from_bytes(secret)

    async def balance(self) -> int:
        self._require_config()
        client = self._client()
        kp = self._keypair()
        if self._is_native:
            resp = client.get_balance(kp.pubkey())
            lamports = resp.value
            return lamports // (10 ** 9)   # lamports → SOL (integer)
        else:
            # SPL token balance
            # TODO: client.get_token_accounts_by_owner(kp.pubkey(), mint=self._mint)
            raise HTTPException(501, f"{self.unit} SPL balance: implement get_token_accounts_by_owner")

    async def receive(self, _: str) -> Transaction:
        self._require_config()
        kp = self._keypair()
        return Transaction.new(
            self.name, "in", 0, self.unit,
            memo="deposit address",
            output=str(kp.pubkey()),
        )

    async def send(self, amount: int, to_address: str = "", memo: str = "") -> Transaction:
        self._require_config()
        if not to_address:
            raise HTTPException(400, "destination Solana address is required")
        if self._is_native:
            # TODO: build + sign SystemProgram.transfer transaction
            raise HTTPException(501, "SOL transfer: build SystemProgram.transfer tx")
        else:
            # TODO: build + sign spl-token transfer instruction
            raise HTTPException(501, f"{self.unit} SPL transfer: build spl-token transfer ix")

    async def history(self) -> list[Transaction]:
        self._require_config()
        kp = self._keypair()
        # TODO: client.get_signatures_for_address(kp.pubkey()) then fetch each tx
        raise HTTPException(501, "Solana history: use get_signatures_for_address")
