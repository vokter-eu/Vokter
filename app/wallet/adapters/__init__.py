"""
Adapter registry. Returns the active WalletAdapter based on VOKTER_WALLET_ADAPTER.

Supported values:
  cashu    — Cashu e-cash, privacy layer (default)
  lightning — Bitcoin Lightning via LNbits
  eurc     — EURC stablecoin (Circle, MiCA)
  eure     — EURe stablecoin (Monerium, MiCA)
  eurcv    — EURCV stablecoin (Société Générale, MiCA)
  monero   — Monero (XMR), privacy-first
  bitcoin  — Bitcoin on-chain
"""
from config import CASHU_MINT_URL, WALLET_ADAPTER
from wallet import WalletAdapter


def get_active_adapter() -> WalletAdapter:
    name = WALLET_ADAPTER.lower().strip()

    if name == "cashu":
        from wallet.cashu import CashuAdapter
        return CashuAdapter(CASHU_MINT_URL)

    if name == "lightning":
        from wallet.adapters.lightning import LightningAdapter
        return LightningAdapter()

    if name in ("eurc", "eure", "eurcv"):
        from wallet.adapters.erc20 import ERC20Adapter
        return ERC20Adapter(name)

    if name == "monero":
        from wallet.adapters.monero import MoneroAdapter
        return MoneroAdapter()

    if name == "bitcoin":
        from wallet.adapters.bitcoin import BitcoinAdapter
        return BitcoinAdapter()

    raise ValueError(
        f"Unknown wallet adapter: {name!r}. "
        "Set VOKTER_WALLET_ADAPTER to one of: cashu, lightning, eurc, eure, eurcv, monero, bitcoin"
    )
