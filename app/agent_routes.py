"""
Agent identity & registry API — Phase 6 (agent-to-agent).

Public card (served to any peer; the Nostr/MCP adapters fetch it from here):
  GET  /.well-known/agent-card.json  — A2A-standard discovery path
  GET  /api/agent/card               — friendly alias

Local-only admin endpoints — the human (or, later, the planner) drives these to
see and reach other agents. NOTE: like the rest of the app, these are
unauthenticated and assume localhost. Do NOT expose port 8080 publicly to make
A2A work — expose only /a2a + /.well-known via a reverse proxy.
  GET  /api/agents           — list known agents
  POST /api/agents/forget    — delete one agent (real deletion)
  POST /api/agents/discover  — fetch a peer's agent card over HTTP
  POST /api/agents/talk      — initiate: send a message to a peer, get the reply
"""
import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from agent_client import call_a2a, fetch_card
from agent_profile import build_agent_card
from known_agents import forget_agent, list_agents

router = APIRouter()


@router.get("/.well-known/agent-card.json")
def agent_card_well_known():
    return build_agent_card()


@router.get("/api/agent/card")
def agent_card():
    return build_agent_card()


@router.get("/api/agents")
def agents_list():
    return list_agents()


class ForgetReq(BaseModel):
    id: str


@router.post("/api/agents/forget")
def agents_forget(req: ForgetReq):
    return {"removed": forget_agent(req.id)}


class DiscoverReq(BaseModel):
    url: str


@router.post("/api/agents/discover")
async def agents_discover(req: DiscoverReq):
    try:
        return await fetch_card(req.url)
    except ValueError as exc:                       # SSRF gate / bad URL
        raise HTTPException(400, str(exc))
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"could not reach peer: {exc}")


class TalkReq(BaseModel):
    target:  str                 # 'http(s)://host/a2a' or 'nostr:npub...'
    message: str
    token:   str | None = None   # optional bearer for an authenticated A2A peer


@router.post("/api/agents/talk")
async def agents_talk(req: TalkReq):
    target = req.target.strip()
    if target.startswith("nostr:"):
        # Outbound over Nostr needs reply correlation (its own step); not yet.
        raise HTTPException(
            501, "Nostr outbound is not implemented yet — use an A2A http(s) URL"
        )
    try:
        reply = await call_a2a(target, req.message, token=req.token)
        return {"reply": reply}
    except ValueError as exc:                       # SSRF gate / bad URL
        raise HTTPException(400, str(exc))
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"could not reach peer: {exc}")
