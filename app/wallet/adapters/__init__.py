"""
Adapter registry. Returns the active WalletAdapter based on VOKTER_WALLET_ADAPTER.

┌─────────────┬────────────────────────────────────────────────────┐
│  Adapter    │  What it handles                                   │
├─────────────┼────────────────────────────────────────────────────┤
│  cashu      │  Cashu e-cash (default, privacy layer)             │
│  lightning  │  Bitcoin Lightning via LNbits (self-hosted)        │
│─────────────┼────────────────────────────────────────────────────│
│  eurc       │  EURC (Circle) on any EVM chain                    │
│  eure       │  EURe (Monerium) on any EVM chain                  │
│  eurcv      │  EURCV (Société Générale) on Ethereum              │
│  evm        │  Any ERC-20 / native coin on any EVM chain         │
│─────────────┼────────────────────────────────────────────────────│
│  eurc-solana│  EURC (Circle) on Solana                           │
│  eure-solana│  EURe (Monerium) on Solana                         │
│  sol        │  Native SOL                                         │
│  solana     │  Any SPL token (set VOKTER_SOLANA_TOKEN_MINT)      │
│─────────────┼────────────────────────────────────────────────────│
│  monero     │  Monero (XMR) — untraceable                        │
│  bitcoin    │  Bitcoin on-chain via Bitcoin Core RPC             │
└─────────────┴────────────────────────────────────────────────────┘
"""
from config import CASHU_MINT_URL, WALLET_ADAPTER
from wallet import WalletAdapter


def get_active_adapter() -> WalletAdapter:
    name = WALLET_ADAPTER.lower().strip()

    # ── Cashu ─────────────────────────────────────────────────────
    if name == "cashu":
        from wallet.cashu import CashuAdapter
        return CashuAdapter(CASHU_MINT_URL)

    # ── Lightning ─────────────────────────────────────────────────
    if name == "lightning":
        from wallet.adapters.lightning import LightningAdapter
        return LightningAdapter()

    # ── EVM (Ethereum-compatible chains) ──────────────────────────
    if name in ("eurc", "eure", "eurcv"):
        from wallet.adapters.evm import EVMAdapter
        return EVMAdapter(preset_symbol=name)

    if name == "evm":
        from wallet.adapters.evm import EVMAdapter
        return EVMAdapter()

    # ── Solana ────────────────────────────────────────────────────
    if name in ("eurc-solana", "eure-solana"):
        from wallet.adapters.solana import SolanaAdapter
        return SolanaAdapter(preset=name)

    if name == "sol":
        from wallet.adapters.solana import SolanaAdapter
        return SolanaAdapter(preset="sol")

    if name == "solana":
        from wallet.adapters.solana import SolanaAdapter
        return SolanaAdapter()

    # ── Privacy / cyberpunk ───────────────────────────────────────
    if name == "monero":
        from wallet.adapters.monero import MoneroAdapter
        return MoneroAdapter()

    if name == "bitcoin":
        from wallet.adapters.bitcoin import BitcoinAdapter
        return BitcoinAdapter()

    raise ValueError(
        f"Unknown wallet adapter: {name!r}. "
        "Valid values: cashu, lightning, "
        "eurc, eure, eurcv, evm, "
        "eurc-solana, eure-solana, sol, solana, "
        "monero, bitcoin"
    )
