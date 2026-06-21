"""
Negotiation API — local, for the human to drive Vokter as buyer and seller.

  POST /api/negotiate/listing   — set what you'll sell + price bounds (seller)
  GET  /api/negotiate/listings  — list your listings (floor included; local only)
  POST /api/negotiate/forget    — remove a listing
  POST /api/negotiate/start     — haggle with a peer; returns the converged deal
                                  WITHOUT accepting (you approve, then /accept)
  POST /api/negotiate/accept    — send the human-approved acceptance

Like the rest of the admin API these are localhost-only (gated by the H1 auth
middleware). The seller state machine itself runs inside dispatch, reached over
the agent transport (Nostr / A2A).
"""
from contextlib import closing

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from db import get_db
from negotiation import accept_offer, negotiate_with

router = APIRouter()


class Listing(BaseModel):
    item:       str
    opening:    int
    floor:      int
    max_rounds: int = 4
    unit:       str = "sat"


@router.post("/api/negotiate/listing")
def set_listing(req: Listing):
    if req.floor > req.opening:
        raise HTTPException(400, "floor cannot exceed opening price")
    if req.opening <= 0 or req.floor <= 0:
        raise HTTPException(400, "prices must be positive")
    if req.max_rounds < 1:
        raise HTTPException(400, "max_rounds must be >= 1")
    with closing(get_db()) as db:
        db.execute(
            """INSERT INTO negotiation_listings (item, opening, floor, max_rounds, unit)
                   VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(item) DO UPDATE SET
                   opening=excluded.opening, floor=excluded.floor,
                   max_rounds=excluded.max_rounds, unit=excluded.unit""",
            (req.item, req.opening, req.floor, req.max_rounds, req.unit),
        )
        db.commit()
    return {"item": req.item, "opening": req.opening, "floor": req.floor,
            "max_rounds": req.max_rounds, "unit": req.unit}


@router.get("/api/negotiate/listings")
def list_listings():
    with closing(get_db()) as db:
        rows = db.execute(
            "SELECT item, opening, floor, max_rounds, unit FROM negotiation_listings ORDER BY item"
        ).fetchall()
    return [{"item": r[0], "opening": r[1], "floor": r[2], "max_rounds": r[3], "unit": r[4]}
            for r in rows]


class ForgetListing(BaseModel):
    item: str


@router.post("/api/negotiate/forget")
def forget_listing(req: ForgetListing):
    with closing(get_db()) as db:
        cur = db.execute("DELETE FROM negotiation_listings WHERE item = ?", (req.item,))
        db.commit()
    return {"removed": cur.rowcount > 0}


class StartReq(BaseModel):
    target:     str                 # 'nostr:npub...' or 'http(s)://host/a2a'
    item:       str
    max_budget: int
    open_bid:   int | None = None
    max_rounds: int = 4
    token:      str | None = None   # bearer for an authenticated A2A peer


@router.post("/api/negotiate/start")
async def start(req: StartReq):
    """Haggle as the buyer and return the converged deal. Does NOT accept — the
    human reviews the result and calls /accept to commit."""
    try:
        return await negotiate_with(
            req.target, req.item, req.max_budget,
            open_bid=req.open_bid, max_rounds=req.max_rounds, token=req.token,
        )
    except ValueError as exc:           # bad target / blocked / no relays
        raise HTTPException(400, str(exc))
    except TimeoutError as exc:
        raise HTTPException(504, str(exc))


class AcceptReq(BaseModel):
    target:     str
    session_id: str
    amount:     int
    token:      str | None = None


@router.post("/api/negotiate/accept")
async def accept(req: AcceptReq):
    """Send the human-confirmed acceptance of a deal reached by /start."""
    try:
        return await accept_offer(req.target, req.session_id, req.amount, token=req.token)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except TimeoutError as exc:
        raise HTTPException(504, str(exc))
