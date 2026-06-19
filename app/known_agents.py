"""
Known-agents registry — the address book of agents Vokter has talked to.

Records every *authenticated* peer (Nostr senders, whose identity NIP-17 proves)
and every agent Vokter itself contacts outbound. Anonymous inbound HTTP callers
are NOT recorded — there is no verified identity to record, and inventing one
would be dishonest.

Stored locally and encrypted at rest (SQLCipher), under the human's control,
and fully deletable (forget_agent) — consistent with Vokter's real-deletion
principle. This is the foundation for trust decisions and, later, negotiation.
"""
import time
from contextlib import closing

from db import get_db


def record_interaction(
    agent_id: str,
    *,
    transport: str,
    direction: str,
    npub: str | None = None,
    name: str | None = None,
    card_json: str | None = None,
) -> None:
    """Upsert a peer. agent_id is the nostr hex pubkey or the a2a endpoint url.

    On repeat interaction: bumps last_seen + interaction count, merges any newly
    learned npub/name/card, and widens direction to 'both' when it flips.
    """
    now = time.time()
    with closing(get_db()) as db:
        db.execute(
            """INSERT INTO known_agents
                   (id, npub, name, transport, direction, card_json,
                    interactions, first_seen, last_seen)
               VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                   last_seen    = excluded.last_seen,
                   interactions = interactions + 1,
                   npub         = COALESCE(excluded.npub, npub),
                   name         = COALESCE(excluded.name, name),
                   card_json    = COALESCE(excluded.card_json, card_json),
                   direction    = CASE WHEN direction = excluded.direction
                                       THEN direction ELSE 'both' END
            """,
            (agent_id, npub, name, transport, direction, card_json, now, now),
        )
        db.commit()


def list_agents() -> list[dict]:
    """Return all known agents, most recently seen first."""
    with closing(get_db()) as db:
        rows = db.execute(
            """SELECT id, npub, name, transport, direction, card_json,
                      interactions, first_seen, last_seen
                 FROM known_agents
                ORDER BY last_seen DESC"""
        ).fetchall()
    return [
        {
            "id": r[0],
            "npub": r[1],
            "name": r[2],
            "transport": r[3],
            "direction": r[4],
            "has_card": r[5] is not None,
            "interactions": r[6],
            "first_seen": r[7],
            "last_seen": r[8],
        }
        for r in rows
    ]


def forget_agent(agent_id: str) -> bool:
    """Delete one agent from the registry. Returns True if a row was removed."""
    with closing(get_db()) as db:
        cur = db.execute("DELETE FROM known_agents WHERE id = ?", (agent_id,))
        db.commit()
        return cur.rowcount > 0
