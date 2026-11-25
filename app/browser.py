import ipaddress
import re
import socket
from contextlib import closing
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel
from urllib.parse import urljoin

from chat import require_human
from db import get_db
from identity import new_session_key
from embedding import pack_embedding
from ingestion import chunk_text
from rag import embed
from utils import strip_html

router = APIRouter()

_MAX_DOWNLOAD_BYTES = 2_000_000  # 2 MB raw limit per page
_TIMEOUT = 15.0
_MAX_REDIRECTS = 5               # hops we follow, validating each before fetching (SSRF guard)


class BrowseRequest(BaseModel):
    url: str


class AllowRequest(BaseModel):
    pattern: str


def _internal_addr(addr) -> bool:
    return (addr.is_private or addr.is_loopback or addr.is_link_local
            or addr.is_reserved or addr.is_multicast or addr.is_unspecified)


def _is_private_host(host: str) -> bool:
    """Return True if host is, or resolves to, a non-public address.

    Fails closed: an unresolvable name returns True (blocked). Checks EVERY
    resolved address (getaddrinfo: IPv4 + IPv6), so a name with one public and
    one internal record is still blocked. NOTE: the lookup here and the one at
    fetch time are separate, so DNS rebinding can slip between them — this
    catches most SSRF, not all.
    """
    try:
        return _internal_addr(ipaddress.ip_address(host))
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return True  # can't resolve → fail closed
    for info in infos:
        try:
            if _internal_addr(ipaddress.ip_address(info[4][0])):
                return True
        except ValueError:
            continue
    return False


def _is_allowed(url: str) -> bool:
    with closing(get_db()) as db:
        patterns = [r[0] for r in db.execute("SELECT pattern FROM browse_allowlist").fetchall()]
    # Require a domain boundary after the pattern so that
    # "https://good.com" does NOT match "https://good.com.evil.com".
    return any(
        url.startswith(p) and url[len(p) : len(p) + 1] in ("", "/", "?", "#")
        for p in patterns
    )



@router.post("/api/browse")
async def browse(req: BrowseRequest):
    url = req.url.strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(400, "Only http and https URLs are allowed.")
    if not parsed.netloc:
        raise HTTPException(400, "Invalid URL.")

    # Route the allowlist decision through the capability gateway (browse is
    # context-agnostic — the allowlist is the guard). The SSRF-during-fetch checks
    # below stay here (they need the in-flight request).
    from safety import HUMAN, Decision, guard
    if guard("browse", url, context=HUMAN) != Decision.ALLOW:
        raise HTTPException(
            403, "URL not in allowlist. Add a permission pattern first."
        )

    # Block SSRF *before* the request is made, not only after redirects — an
    # allowlisted domain that resolves to an internal address (incl. DNS
    # rebinding) must never be reached at all.
    if _is_private_host(parsed.hostname or ""):
        raise HTTPException(403, "URL resolves to a private/internal address.")

    # One ephemeral session key per browse request — stored locally, never sent out.
    session_id, _ = new_session_key(context=f"browse:{url}")

    # Follow redirects MANUALLY, validating every hop BEFORE it is fetched (SSRF fix): httpx's
    # follow_redirects=True would fetch each intermediate target — including a 302 to
    # http://127.0.0.1:<port>/… — before any check runs, letting an allowlisted-but-compromised site
    # bounce the backend into its own loopback admin API. Each Location is resolved (relative→absolute),
    # must be http(s), must not resolve to a private/internal address, and must stay in the allowlist.
    try:
        async with httpx.AsyncClient(
            follow_redirects=False,
            timeout=_TIMEOUT,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; local-browser/1.0)",
                "Accept": "text/html,text/plain;q=0.9",
                "Accept-Language": "en,*;q=0.5",
            },
        ) as client:
            current = url
            for _ in range(_MAX_REDIRECTS + 1):
                resp = await client.get(current)
                if not resp.is_redirect:
                    break
                nxt = urljoin(current, resp.headers.get("location", ""))   # resolve relative redirects
                parsed_nxt = urlparse(nxt)
                if parsed_nxt.scheme not in ("http", "https"):
                    raise HTTPException(403, f"Redirect to a non-http(s) URL is not allowed: {nxt}")
                if _is_private_host(parsed_nxt.hostname or ""):
                    raise HTTPException(403, "Redirect to a private/internal address is not allowed.")
                if not _is_allowed(nxt):
                    raise HTTPException(403, f"Redirect led to non-allowlisted URL: {nxt}")
                current = nxt
            else:
                raise HTTPException(310, "Too many redirects.")
    except httpx.TimeoutException:
        raise HTTPException(504, f"Request timed out after {_TIMEOUT}s.")
    except httpx.RequestError as exc:
        raise HTTPException(502, f"Could not reach URL: {exc}")

    content_type = resp.headers.get("content-type", "")
    if "text" not in content_type:
        raise HTTPException(415, f"Unsupported content type: {content_type}")

    raw = resp.text[:_MAX_DOWNLOAD_BYTES]
    text = strip_html(raw) if "html" in content_type else re.sub(r"\s+", " ", raw).strip()

    if not text:
        raise HTTPException(422, "No text content found on that page.")

    doc_name = f"web::{resp.url}"
    chunks = chunk_text(text)

    with closing(get_db()) as db:
        db.execute("DELETE FROM chunks WHERE doc = ?", (doc_name,))
        for piece in chunks:
            vector = await embed(piece)
            db.execute(
                "INSERT INTO chunks (doc, content, embedding) VALUES (?, ?, ?)",
                (doc_name, piece, pack_embedding(vector)),
            )
        db.commit()

    return {"doc": doc_name, "chunks": len(chunks), "session_id": session_id}


@router.get("/api/browse/permissions")
def list_permissions():
    with closing(get_db()) as db:
        rows = db.execute(
            "SELECT pattern, added_at FROM browse_allowlist ORDER BY added_at DESC"
        ).fetchall()
    return [{"pattern": r[0], "added_at": r[1]} for r in rows]


@router.post("/api/browse/permissions")
def add_permission(req: AllowRequest, x_vokter_human_session: str | None = Header(default=None)):
    # Human-session gate (CRITICAL-1): the allowlist IS the predicate the browse gateway trusts, so an
    # unauthenticated caller must not be able to widen it (that would bless attacker URLs for ingestion).
    require_human(x_vokter_human_session)
    pattern = req.pattern.strip()
    if not pattern:
        raise HTTPException(400, "pattern is empty")
    if urlparse(pattern).scheme not in ("http", "https"):
        raise HTTPException(400, "Pattern must start with http:// or https://")
    with closing(get_db()) as db:
        db.execute(
            "INSERT OR IGNORE INTO browse_allowlist (pattern, added_at) VALUES (?, ?)",
            (pattern, datetime.now(timezone.utc).isoformat()),
        )
        db.commit()
    return {"pattern": pattern}


@router.delete("/api/browse/permissions/{pattern:path}")
def remove_permission(pattern: str, x_vokter_human_session: str | None = Header(default=None)):
    require_human(x_vokter_human_session)
    with closing(get_db()) as db:
        db.execute("DELETE FROM browse_allowlist WHERE pattern = ?", (pattern,))
        db.commit()
    return {"removed": pattern}
