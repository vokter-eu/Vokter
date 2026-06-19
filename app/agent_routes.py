"""
Agent identity API — Phase 6 (agent-to-agent presentation).

GET /.well-known/agent-card.json  — A2A-standard discovery path for the card
GET /api/agent/card               — friendly alias, same payload

The card is public (network identity, name, sovereignty policy, the introduce
skill). All adapters (Nostr 'hello' verb, MCP) fetch it from here — the card is
built once, in the core (agent_profile), and only translated by adapters.
"""
from fastapi import APIRouter

from agent_profile import build_agent_card

router = APIRouter()


@router.get("/.well-known/agent-card.json")
def agent_card_well_known():
    return build_agent_card()


@router.get("/api/agent/card")
def agent_card():
    return build_agent_card()
