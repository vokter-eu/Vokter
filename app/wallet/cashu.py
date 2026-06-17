"""
Cashu e-cash adapter.

Implements receive / send for Cashu bearer tokens (cashuA… strings).
Proofs are stored in Vokter's local SQLite — non-custodial, never sent
to any third party.

Privacy note: proof swapping on receive (full unlinkability via BDHKE)
is not implemented in this phase. The mint can correlate the withdrawal
and the spend for unswapped proofs. Swap-on-receive will be added in
Phase 3+ once coincurve is integrated for blind-signature math.

Protocol specs: https://github.com/cashubtc/nuts
"""
import asyncio
import base64
import json
from contextlib import closing

import httpx
from fastapi import HTTPException

from db import get_db
from wallet import WalletAdapter, Transaction

# Serialises proof reads + writes inside send() to prevent double-spend when
# two coroutines race between _unspent() and _mark_spent().
_SEND_LOCK = asyncio.Lock()


# ---------- token codec -------------------------------------------------------

def _decode_token(s: str) -> dict:
    if not s.startswith("cashuA"):
        raise ValueError("Not a Cashu token (must start with 'cashuA')")
    b64 = s[len("cashuA"):]
    raw = base64.urlsafe_b64decode(b64 + "=" * ((4 - len(b64) % 4) % 4))
    return json.loads(raw)


def _encode_token(mint_url: str, proofs: list[dict], unit: str, memo: str = "") -> str:
    payload = {
        "token": [{"mint": mint_url, "proofs": proofs}],
        "unit": unit,
        "memo": memo,
    }
    b64 = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode()
    ).decode().rstrip("=")
    return "cashuA" + b64


# ---------- proof selection ---------------------------------------------------

def _select_proofs(proofs: list[dict], amount: int) -> list[dict] | None:
    """Greedy exact-change selection. Correct for standard 2^n Cashu denominations."""
    selected, remaining = [], amount
    for p in sorted(proofs, key=lambda p: p["amount"], reverse=True):
        if p["amount"] <= remaining:
            selected.append(p)
            remaining -= p["amount"]
    return selected if remaining == 0 else None


# ---------- adapter ----------------------------------------------------------

class CashuAdapter(WalletAdapter):
    name = "cashu"

    def __init__(self, mint_url: str) -> None:
        self._mint = (mint_url or "").rstrip("/")
        self._unit = "sat"  # updated on first successful mint contact

    @property
    def unit(self) -> str:
        return self._unit

    def _require_mint(self) -> None:
        if not self._mint:
            raise HTTPException(400, "No Cashu mint configured. Set VOKTER_CASHU_MINT_URL.")

    # -- DB helpers -----------------------------------------------------------

    def _unspent(self) -> list[dict]:
        with closing(get_db()) as db:
            rows = db.execute(
                "SELECT proof_json FROM cashu_proofs WHERE mint=? AND spent=0",
                (self._mint,),
            ).fetchall()
        return [json.loads(r[0]) for r in rows]

    def _store_proof(self, db, proof: dict) -> bool:
        """Returns True if the proof was newly stored, False if already known."""
        cur = db.execute(
            "INSERT OR IGNORE INTO cashu_proofs(id, mint, amount, proof_json, spent)"
            " VALUES(?,?,?,?,0)",
            (proof["secret"], self._mint, proof["amount"], json.dumps(proof)),
        )
        return cur.rowcount > 0

    def _mark_spent(self, secrets: list[str]) -> None:
        with closing(get_db()) as db:
            db.executemany(
                "UPDATE cashu_proofs SET spent=1 WHERE id=?",
                [(s,) for s in secrets],
            )
            db.commit()

    def _record_tx(self, tx: Transaction) -> None:
        with closing(get_db()) as db:
            db.execute(
                "INSERT INTO wallet_transactions"
                "(id,adapter,direction,amount,unit,memo,output,ts)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (tx.id, tx.adapter, tx.direction, tx.amount,
                 tx.unit, tx.memo, tx.output, tx.ts),
            )
            db.commit()

    # -- mint helpers ---------------------------------------------------------

    async def _known_keysets(self) -> set[str] | None:
        """Returns active keyset IDs, or None if the mint is unreachable.
        None means 'unknown' — callers must not silently skip validation."""
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(f"{self._mint}/v1/keysets")
            if r.status_code == 200:
                return {ks["id"] for ks in r.json().get("keysets", [])}
        except httpx.HTTPError:
            pass
        return None

    async def _discover_unit(self) -> None:
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(f"{self._mint}/v1/info")
            if r.status_code == 200:
                methods = r.json().get("nuts", {}).get("4", {}).get("methods", [])
                if methods:
                    self._unit = methods[0].get("unit", "sat")
        except httpx.HTTPError:
            pass

    # -- public interface -----------------------------------------------------

    async def balance(self) -> int:
        return sum(p["amount"] for p in self._unspent())

    async def receive(self, token_str: str) -> Transaction:
        self._require_mint()
        try:
            token = _decode_token(token_str.strip())
        except (ValueError, json.JSONDecodeError) as e:
            raise HTTPException(400, f"Invalid Cashu token: {e}")

        unit = token.get("unit", "sat")
        known = await self._known_keysets()
        total = 0

        with closing(get_db()) as db:
            for entry in token.get("token", []):
                mint = (entry.get("mint") or "").rstrip("/")
                if mint != self._mint:
                    raise HTTPException(
                        400,
                        f"Token is from mint {mint!r} but configured mint is {self._mint!r}",
                    )
                for proof in entry.get("proofs", []):
                    # known is None when mint is unreachable — skip validation rather
                    # than accept blindly. known=set() means mint responded with no
                    # active keysets, which should also reject.
                    if known is not None and proof.get("id") not in known:
                        raise HTTPException(
                            400,
                            f"Keyset {proof.get('id')!r} is not active on this mint"
                            " — token may use an expired keyset",
                        )
                    if self._store_proof(db, proof):
                        total += proof["amount"]
            db.commit()

        if total == 0:
            raise HTTPException(400, "Token contains no proofs")

        self._unit = unit
        tx = Transaction.new(self.name, "in", total, unit)
        self._record_tx(tx)
        return tx

    async def send(self, amount: int, destination: str = "", memo: str = "") -> Transaction:
        self._require_mint()
        if amount <= 0:
            raise HTTPException(400, "Amount must be positive")

        # Discover unit before the lock — it's an HTTP call unrelated to proof state.
        await self._discover_unit()

        async with _SEND_LOCK:
            proofs = self._unspent()
            selected = _select_proofs(proofs, amount)
            if selected is None:
                available = sum(p["amount"] for p in proofs)
                raise HTTPException(
                    400,
                    f"Cannot make exact change for {amount} {self._unit}. "
                    f"Available balance: {available}. "
                    "Cashu requires proof denominations that sum exactly to the requested amount. "
                    "Try a different amount, or receive proofs with smaller denominations.",
                )

            token_str = _encode_token(self._mint, selected, self._unit, memo)
            tx = Transaction.new(
                self.name, "out", amount, self._unit,
                memo=memo or destination,
                output=token_str,
            )

            # Single atomic transaction: mark proofs spent + record the outgoing tx.
            # If either write fails neither is committed — no silent fund loss.
            with closing(get_db()) as db:
                db.executemany(
                    "UPDATE cashu_proofs SET spent=1 WHERE id=?",
                    [(p["secret"],) for p in selected],
                )
                db.execute(
                    "INSERT INTO wallet_transactions"
                    "(id,adapter,direction,amount,unit,memo,output,ts)"
                    " VALUES(?,?,?,?,?,?,?,?)",
                    (tx.id, tx.adapter, tx.direction, tx.amount,
                     tx.unit, tx.memo, tx.output, tx.ts),
                )
                db.commit()

        return tx

    async def history(self) -> list[Transaction]:
        with closing(get_db()) as db:
            rows = db.execute(
                "SELECT id,adapter,direction,amount,unit,memo,ts,output"
                " FROM wallet_transactions WHERE adapter=? ORDER BY ts DESC LIMIT 100",
                (self.name,),
            ).fetchall()
        return [
            Transaction(
                id=r[0], adapter=r[1], direction=r[2], amount=r[3],
                unit=r[4], memo=r[5], ts=r[6], output=r[7],
            )
            for r in rows
        ]
