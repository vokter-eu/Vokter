"""
Wallet API routes.

Every payment goes through an explicit confirmation gate: POST /api/wallet/send
refuses any request where confirmed=false. This is enforced here so no adapter
can accidentally bypass it — the invariant is structural, not advisory.
"""
import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config import CASHU_MINT_URL, WALLET_ADAPTER, WALLET_SPEND_LIMIT
from db import get_db
from wallet.adapters import get_active_adapter

router = APIRouter()


class ReceiveRequest(BaseModel):
    token: str


class SendRequest(BaseModel):
    amount: int
    destination: str = ""
    memo: str = ""
    confirmed: bool = False   # must be True or the request is rejected


@router.get("/api/wallet/balance")
async def wallet_balance():
    adapter = get_active_adapter()
    bal = await adapter.balance()
    return {"adapter": adapter.name, "balance": bal, "unit": adapter.unit}


@router.post("/api/wallet/receive")
async def wallet_receive(req: ReceiveRequest):
    if not req.token.strip():
        raise HTTPException(400, "token is required")
    adapter = get_active_adapter()
    tx = await adapter.receive(req.token.strip())
    return {"received": tx.amount, "unit": tx.unit, "id": tx.id, "output": tx.output}


@router.post("/api/wallet/send")
async def wallet_send(req: SendRequest):
    if not req.confirmed:
        raise HTTPException(
            400,
            "Payment requires explicit user confirmation. Send {confirmed: true} only after the user has approved the payment details.",
        )
    if req.amount <= 0:
        raise HTTPException(400, "amount must be positive")

    adapter = get_active_adapter()

    if WALLET_SPEND_LIMIT > 0:
        since = time.time() - 86_400
        with get_db() as db:
            row = db.execute(
                "SELECT COALESCE(SUM(amount),0) FROM wallet_transactions"
                " WHERE adapter=? AND direction='out' AND ts>?",
                (adapter.name, since),
            ).fetchone()
        spent_today = row[0]
        if spent_today + req.amount > WALLET_SPEND_LIMIT:
            raise HTTPException(
                403,
                f"Daily spending limit ({WALLET_SPEND_LIMIT} {adapter.unit}) would be exceeded "
                f"(already spent today: {spent_today})",
            )

    tx = await adapter.send(req.amount, req.destination, req.memo)
    return {"sent": tx.amount, "unit": tx.unit, "output": tx.output, "id": tx.id}


@router.get("/api/wallet/history")
async def wallet_history():
    adapter = get_active_adapter()
    txs = await adapter.history()
    return [
        {
            "id": t.id, "direction": t.direction, "amount": t.amount,
            "unit": t.unit, "memo": t.memo, "ts": t.ts,
        }
        for t in txs
    ]


@router.get("/api/wallet/adapters")
async def wallet_adapters():
    return {
        "active": WALLET_ADAPTER,
        "adapters": [
            {
                "name": "cashu",
                "label": "Cashu e-cash",
                "tier": "default",
                "description": "Privacy layer: Chaumian blind signatures. Mint-agnostic.",
                "status": "ready" if CASHU_MINT_URL else "needs VOKTER_CASHU_MINT_URL",
            },
            {
                "name": "lightning",
                "label": "Lightning (LNbits)",
                "tier": "mainstream",
                "description": "Bitcoin Lightning via self-hosted LNbits.",
                "status": "stub — set VOKTER_LNBITS_URL + keys",
            },
            {
                "name": "eurc",
                "label": "EURC — Circle",
                "tier": "regulated",
                "description": "MiCA euro stablecoin on Ethereum.",
                "status": "stub — set VOKTER_ETH_RPC_URL + private key",
            },
            {
                "name": "eure",
                "label": "EURe — Monerium",
                "tier": "regulated",
                "description": "MiCA euro stablecoin on Gnosis Chain.",
                "status": "stub — set VOKTER_ETH_RPC_URL + private key",
            },
            {
                "name": "eurcv",
                "label": "EURCV — Société Générale",
                "tier": "regulated",
                "description": "MiCA euro stablecoin on Ethereum.",
                "status": "stub — set VOKTER_ETH_RPC_URL + private key",
            },
            {
                "name": "monero",
                "label": "Monero (XMR)",
                "tier": "cyberpunk",
                "description": "Untraceable by design. Ring signatures + stealth addresses.",
                "status": "stub — needs monero-wallet-rpc + VOKTER_MONERO_RPC_URL",
            },
            {
                "name": "bitcoin",
                "label": "Bitcoin (on-chain)",
                "tier": "mainstream",
                "description": "On-chain BTC via Bitcoin Core RPC.",
                "status": "stub — set VOKTER_BTC_RPC_URL",
            },
        ],
    }
