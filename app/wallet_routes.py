"""
Wallet API routes.

Payment authorisation (C3). POST /api/wallet/send moves real, irreversible funds, so
it is the single choke point where the human-vs-agent boundary is ENFORCED: the request
must carry THIS launch's human-session token (X-Vokter-Human-Session), the same token
that gates personal memory. Deny-by-default — no token, no payment. Only the Electron
shell holds the token and attaches it (via its main-process proxy) after the human acted;
MCP hosts, peer agents, and a plain browser do NOT hold it, so they cannot spend. This is
the sole path to adapter.send() (see tests/wallet_gate_test.py, incl. the tripwire that
this stays true). The old `confirmed` flag is a caller self-attestation — kept only as a
UX/idempotency hint; it was never the security boundary.

No pre-authorised/recurring path exists: there is deliberately no way to spend without the
live human token today. When unattended payments (subscriptions, post-negotiation auto-pay)
are built, they must NOT weaken this route — see vokter-C3-plan.md §4 (mandate design).
"""
import asyncio
import os
import time
from contextlib import closing

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from chat import is_local_human_session
from config import CASHU_MINT_URL, WALLET_ADAPTER, WALLET_SPEND_LIMIT
from db import get_db
from wallet.adapters import get_active_adapter

router = APIRouter()

# Serialises check-then-send so two concurrent requests can't both pass
# the daily spending limit check before either updates the DB.
_send_lock = asyncio.Lock()


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
async def wallet_send(
    req: SendRequest,
    x_vokter_human_session: str | None = Header(default=None),
):
    # C3 gate — the real boundary: only the local human session may move funds.
    # Deny-by-default (no valid token → refused), mirroring chat.is_local_human_session
    # for personal memory. A peer/MCP/plain-browser never holds the token, so this 403s
    # them BEFORE any adapter is touched. Placed first so nothing below can run unauthorised.
    if not is_local_human_session(x_vokter_human_session):
        raise HTTPException(
            403,
            "Payment refused: wallet_send requires the local human session. Confirm the "
            "payment in the Vokter app — an external assistant or peer cannot authorise it.",
        )
    # `confirmed` is now only a UX/idempotency hint (the caller attests the human saw the
    # details); it is NOT the security boundary — the token above is.
    if not req.confirmed:
        raise HTTPException(
            400,
            "Payment requires explicit user confirmation. Send {confirmed: true} only after the user has approved the payment details.",
        )
    if req.amount <= 0:
        raise HTTPException(400, "amount must be positive")

    adapter = get_active_adapter()

    async with _send_lock:
        if WALLET_SPEND_LIMIT > 0:
            since = time.time() - 86_400
            with closing(get_db()) as db:
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
        # Adapters that don't write their own DB row (EVM, Lightning, …) need this.
        # INSERT OR IGNORE is a no-op for Cashu, which already writes atomically inside send().
        with closing(get_db()) as db:
            db.execute(
                "INSERT OR IGNORE INTO wallet_transactions"
                "(id,adapter,direction,amount,unit,memo,output,ts)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (tx.id, tx.adapter, tx.direction, tx.amount,
                 tx.unit, tx.memo, tx.output, tx.ts),
            )
            db.commit()
    return {"sent": tx.amount, "unit": tx.unit, "output": tx.output, "id": tx.id}


@router.get("/api/wallet/history")
async def wallet_history():
    adapter = get_active_adapter()
    with closing(get_db()) as db:
        rows = db.execute(
            "SELECT id, direction, amount, unit, memo, ts, output"
            " FROM wallet_transactions WHERE adapter=? ORDER BY ts DESC LIMIT 100",
            (adapter.name,),
        ).fetchall()
    return [
        {
            "id": r[0], "direction": r[1], "amount": r[2],
            "unit": r[3], "memo": r[4], "ts": r[5], "output": r[6],
        }
        for r in rows
    ]


@router.get("/api/wallet/adapters")
async def wallet_adapters():
    evm_rpc = bool(os.getenv("VOKTER_EVM_RPC_URL"))
    sol_rpc = bool(os.getenv("VOKTER_SOLANA_RPC_URL"))
    return {
        "active": WALLET_ADAPTER,
        "adapters": [
            # ── Default / privacy ──────────────────────────────────────────
            {
                "name": "cashu", "tier": "default",
                "label": "Cashu e-cash",
                "description": "Chaumian blind signatures. Works with any Cashu mint.",
                "status": "ready" if CASHU_MINT_URL else "needs VOKTER_CASHU_MINT_URL",
            },
            # ── Lightning ──────────────────────────────────────────────────
            {
                "name": "lightning", "tier": "mainstream",
                "label": "Lightning Network (LNbits)",
                "description": "Bitcoin Lightning via self-hosted LNbits.",
                "status": "stub — set VOKTER_LNBITS_URL + keys",
            },
            # ── EVM stablecoins (MiCA-regulated) ──────────────────────────
            {
                "name": "eurc", "tier": "regulated",
                "label": "EURC — Circle (EVM)",
                "description": "MiCA euro stablecoin. Ethereum, Avalanche, Base.",
                "status": ("ready" if evm_rpc else "stub") + " — set VOKTER_EVM_* vars",
            },
            {
                "name": "eure", "tier": "regulated",
                "label": "EURe — Monerium (EVM)",
                "description": "MiCA euro stablecoin. Ethereum, Gnosis, Polygon.",
                "status": ("ready" if evm_rpc else "stub") + " — set VOKTER_EVM_* vars",
            },
            {
                "name": "eurcv", "tier": "regulated",
                "label": "EURCV — Société Générale (EVM)",
                "description": "MiCA euro stablecoin. Ethereum.",
                "status": ("ready" if evm_rpc else "stub") + " — set VOKTER_EVM_* vars",
            },
            {
                "name": "evm", "tier": "regulated",
                "label": "Custom EVM token",
                "description": "Any ERC-20 on any EVM chain. Set contract + RPC manually.",
                "status": ("ready" if evm_rpc else "stub") + " — set VOKTER_EVM_* vars",
            },
            # ── Solana stablecoins ─────────────────────────────────────────
            {
                "name": "eurc-solana", "tier": "regulated",
                "label": "EURC — Circle (Solana)",
                "description": "EURC SPL token on Solana.",
                "status": ("ready" if sol_rpc else "stub") + " — set VOKTER_SOLANA_* vars",
            },
            {
                "name": "eure-solana", "tier": "regulated",
                "label": "EURe — Monerium (Solana)",
                "description": "EURe SPL token on Solana.",
                "status": ("ready" if sol_rpc else "stub") + " — set VOKTER_SOLANA_* vars",
            },
            {
                "name": "sol", "tier": "mainstream",
                "label": "SOL (native)",
                "description": "Native Solana token.",
                "status": ("ready" if sol_rpc else "stub") + " — set VOKTER_SOLANA_* vars",
            },
            {
                "name": "solana", "tier": "mainstream",
                "label": "Custom Solana SPL token",
                "description": "Any SPL token. Set mint address manually.",
                "status": ("ready" if sol_rpc else "stub") + " — set VOKTER_SOLANA_* vars",
            },
            # ── Cyberpunk / privacy ────────────────────────────────────────
            {
                "name": "monero", "tier": "cyberpunk",
                "label": "Monero (XMR)",
                "description": "Untraceable by design. Ring signatures + stealth addresses.",
                "status": "stub — set VOKTER_MONERO_RPC_URL",
            },
            {
                "name": "bitcoin", "tier": "mainstream",
                "label": "Bitcoin (on-chain)",
                "description": "On-chain BTC via Bitcoin Core RPC.",
                "status": "stub — set VOKTER_BTC_RPC_URL",
            },
        ],
    }
