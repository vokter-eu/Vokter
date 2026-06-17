"""
Lightning Network adapter — LNbits backend (self-hosted).

LNbits is a free, self-hostable Lightning wallet server. Run it in Docker
alongside Vokter so no third party ever touches your funds.

  docker run -d -p 5000:5000 lnbits/lnbits

Required env vars:
  VOKTER_LNBITS_URL          e.g. http://lnbits:5000
  VOKTER_LNBITS_INVOICE_KEY  read-only key  (balance, receive, history)
  VOKTER_LNBITS_ADMIN_KEY    admin key      (send payments)

Activate: VOKTER_WALLET_ADAPTER=lightning

LNbits API docs: https://demo.lnbits.com/docs
"""
import os

import httpx
from fastapi import HTTPException

from wallet import WalletAdapter, Transaction

_URL  = os.getenv("VOKTER_LNBITS_URL",          "")
_INVK = os.getenv("VOKTER_LNBITS_INVOICE_KEY",  "")
_ADMK = os.getenv("VOKTER_LNBITS_ADMIN_KEY",    "")


class LightningAdapter(WalletAdapter):
    name = "lightning"
    unit = "sat"

    def _require_config(self) -> None:
        if not _URL or not _INVK:
            raise HTTPException(
                501,
                "Lightning adapter not configured. "
                "Set VOKTER_LNBITS_URL and VOKTER_LNBITS_INVOICE_KEY.",
            )

    async def balance(self) -> int:
        self._require_config()
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"{_URL}/api/v1/wallet", headers={"X-Api-Key": _INVK})
        if r.status_code != 200:
            raise HTTPException(502, f"LNbits error: {r.status_code}")
        return r.json()["balance"] // 1000   # msat → sat

    async def receive(self, amount_str: str) -> Transaction:
        """Create a BOLT11 invoice for `amount_str` sat. Returns the invoice in tx.output."""
        self._require_config()
        try:
            amount = int(amount_str)
        except ValueError:
            raise HTTPException(400, "amount must be an integer number of sat")
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(
                f"{_URL}/api/v1/payments",
                headers={"X-Api-Key": _INVK},
                json={"out": False, "amount": amount, "memo": "Vokter receive"},
            )
        if r.status_code not in (200, 201):
            raise HTTPException(502, f"LNbits invoice error: {r.status_code}")
        bolt11 = r.json().get("payment_request", "")
        return Transaction.new(self.name, "in", amount, self.unit, output=bolt11)

    async def send(self, amount: int, bolt11: str = "", memo: str = "") -> Transaction:
        """Pay a BOLT11 invoice. `destination` must be a bolt11 string."""
        self._require_config()
        if not _ADMK:
            raise HTTPException(501, "Lightning send requires VOKTER_LNBITS_ADMIN_KEY")
        if not bolt11.lower().startswith("ln"):
            raise HTTPException(400, "destination must be a BOLT11 invoice (starts with 'ln')")
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(
                f"{_URL}/api/v1/payments",
                headers={"X-Api-Key": _ADMK},
                json={"out": True, "bolt11": bolt11},
            )
            if r.status_code not in (200, 201):
                raise HTTPException(502, f"LNbits payment error: {r.status_code} — {r.text}")
            payment_hash = r.json().get("payment_hash", "")
            # The BOLT11 invoice encodes the real amount; fetch it from LNbits
            # rather than trusting the caller's `amount` parameter.
            actual_amount = amount
            if payment_hash:
                try:
                    details = await c.get(
                        f"{_URL}/api/v1/payments/{payment_hash}",
                        headers={"X-Api-Key": _ADMK},
                    )
                    if details.status_code == 200:
                        actual_amount = abs(details.json().get("amount", amount * 1000)) // 1000
                except Exception:
                    pass  # fall back to caller-supplied amount
        return Transaction.new(self.name, "out", actual_amount, self.unit, memo=memo, output=payment_hash)

    async def history(self) -> list[Transaction]:
        self._require_config()
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(f"{_URL}/api/v1/payments", headers={"X-Api-Key": _INVK})
        if r.status_code != 200:
            raise HTTPException(502, f"LNbits history error: {r.status_code}")
        txs = []
        for p in r.json():
            direction = "out" if p.get("out") else "in"
            amount = abs(p.get("amount", 0)) // 1000
            txs.append(Transaction(
                id=p.get("checking_id", ""),
                adapter=self.name,
                direction=direction,
                amount=amount,
                unit=self.unit,
                memo=p.get("memo", ""),
                ts=p.get("time", 0),
                output="",
            ))
        return txs
