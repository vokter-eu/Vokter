"""
Agent identity card — Phase 6 (agent-to-agent presentation).

When another agent meets Vokter, it needs to learn, in a standard format,
"who is this and what can it do". Rather than invent a Vokter-only dialect,
the card conforms to the **A2A (Agent2Agent) Agent Card** schema — the
cross-vendor standard for agent capability discovery — so any A2A-aware peer
can parse it.

A2A required fields: name, description, version, url, skills (>= 1).
We additionally declare Vokter's sovereignty guarantees as an A2A capability
**extension** (capabilities.extensions), so a peer can read — in machine form —
that this agent acts only for its human, never pays or shares data without
explicit human approval, and is non-custodial.

Deliberately minimal outward surface: the card advertises exactly ONE honest
skill ('introduce') — identity/capability exchange, which is what is actually
implemented today. It does NOT expose the human's local tools (ask over private
documents, email, scheduling); those are driven by the human locally, and
advertising them to strangers' agents would be over-disclosure and attack
surface. New outward skills are added here only when they are real.

Built in the core and served via REST (/.well-known/agent-card.json); the Nostr
and MCP adapters only translate — no business logic lives in them.
"""
from agent_config import get_config
from config import VOKTER_VERSION, A2A_URL, A2A_TOKEN
from identity import get_nostr_npub

# Bump when the card schema / sovereignty extension semantics change.
SOVEREIGN_POLICY_EXT = "https://vokter.eu/a2a/ext/sovereign-policy/v1"


def build_agent_card() -> dict:
    """Return Vokter's public identity as an A2A-conformant Agent Card.

    Contains only public information (network identity, name, policy, the
    introduce skill). Safe to hand to any peer.
    """
    cfg  = get_config()
    name = cfg.get("agent_name", "Vokter")
    npub = get_nostr_npub()

    # Transports. A2A-over-HTTP is the cross-vendor lingua franca, but a
    # local-first Vokter behind NAT is not reachable on HTTP unless the human
    # exposes a port and sets VOKTER_A2A_URL. Nostr relays, by contrast, reach
    # it without exposing anything — so advertise HTTP only when it is really
    # reachable, and otherwise advertise Nostr as the live transport. We never
    # list an unreachable URL as an interface.
    a2a_url     = A2A_URL.strip()
    nostr_iface = {"url": f"nostr:{npub}", "transport": "nostr+nip17"}
    if a2a_url:
        primary_url, preferred = a2a_url, "JSONRPC"
        interfaces = [{"url": a2a_url, "transport": "JSONRPC"}, nostr_iface]
    else:
        primary_url, preferred = f"nostr:{npub}", "nostr+nip17"
        interfaces = [nostr_iface]

    card = {
        # A2A core (required) ------------------------------------------------
        "protocolVersion": "0.3.0",
        "name": name,
        "description": (
            f"{name} is a sovereign personal AI agent that runs entirely on its "
            "human's own machine. It acts solely on behalf of that human, makes "
            "no commitment and discloses no personal data without explicit, "
            "revocable approval."
        ),
        "version": VOKTER_VERSION,
        "url": primary_url,
        "preferredTransport": preferred,
        "additionalInterfaces": interfaces,
        "documentationUrl": "https://vokterai.com",
        "provider": {
            "organization": "Vokter",
            "url": "https://vokterai.com",
        },
        "defaultInputModes":  ["text/plain"],
        "defaultOutputModes": ["text/plain"],
        # Capabilities + sovereignty extension ------------------------------
        "capabilities": {
            "streaming": False,
            "pushNotifications": False,
            "extensions": [
                {
                    "uri": SOVEREIGN_POLICY_EXT,
                    "description": (
                        "Sovereignty guarantees this personal agent operates "
                        "under. A peer can rely on these without trusting Vokter "
                        "— the code is open and the keys never leave the device."
                    ),
                    "params": {
                        "actsFor": "human",
                        "dataSharing": "none-without-explicit-permission",
                        "localFirst": True,
                    },
                }
            ],
        },
        # Outward skills (curated, honest, minimal) -------------------------
        "skills": [
            {
                "id": "introduce",
                "name": "Introduce and exchange capabilities",
                "description": (
                    "Exchange identity and capability information with another "
                    "agent. This agent represents a human; it makes no "
                    "commitments and shares no personal data without that "
                    "human's explicit, revocable approval."
                ),
                "tags": ["personal-agent", "handshake", "sovereign", "nostr"],
                "examples": ["hello", '{"tool": "hello"}'],
            }
        ],
    }

    # When an HTTP endpoint is exposed with a bearer token, declare it so a
    # trusted caller knows how to authenticate for more than 'introduce'. The
    # 'introduce' handshake itself stays public (no top-level security req).
    if a2a_url and A2A_TOKEN:
        card["securitySchemes"] = {"bearer": {"type": "http", "scheme": "bearer"}}

    return card
