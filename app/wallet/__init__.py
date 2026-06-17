"""
Wallet abstraction layer.

Every payment adapter implements WalletAdapter. The route layer always
demands explicit user confirmation before calling send() — this is
enforced at the HTTP layer, not here, but it is the contract every
adapter must respect: never spend without a prior human decision.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import time
import uuid


@dataclass
class Transaction:
    id: str
    adapter: str
    direction: str    # "in" | "out"
    amount: int
    unit: str         # "sat", "eurc", "xmr", …
    memo: str
    ts: float
    output: str = "" # cashu token / txid / bolt11 for the recipient

    @staticmethod
    def new(
        adapter: str,
        direction: str,
        amount: int,
        unit: str,
        memo: str = "",
        output: str = "",
    ) -> "Transaction":
        return Transaction(
            id=str(uuid.uuid4()),
            adapter=adapter,
            direction=direction,
            amount=amount,
            unit=unit,
            memo=memo,
            ts=time.time(),
            output=output,
        )


class WalletAdapter(ABC):
    name: str   # adapter identifier, e.g. "cashu", "lightning"
    unit: str   # currency unit, e.g. "sat", "eurc"

    @abstractmethod
    async def balance(self) -> int:
        """Current spendable balance in the adapter's unit."""
        ...

    @abstractmethod
    async def receive(self, token: str) -> Transaction:
        """Accept an incoming payment.
        `token` is adapter-specific: a cashuA string, a BOLT11 invoice, an address, etc.
        Returns a Transaction recording the receipt.
        """
        ...

    @abstractmethod
    async def send(self, amount: int, destination: str = "", memo: str = "") -> Transaction:
        """Create an outgoing payment.
        INVARIANT: callers MUST obtain explicit user confirmation before calling this.
        Spending-limit enforcement is also applied at the route layer.
        tx.output carries the artifact the recipient needs (token, txid, payment hash).
        """
        ...

    @abstractmethod
    async def history(self) -> list[Transaction]:
        """Transaction history, newest first."""
        ...
