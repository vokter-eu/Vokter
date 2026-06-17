import ipaddress
import json
import re
import socket
from contextlib import closing
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from db import get_db
from identity import new_session_key
from ingestion import chunk_text
from rag import embed
from utils import strip_html

router = APIRouter()

_MAX_DOWNLOAD_BYTES = 2_000_000  # 2 MB raw limit per page
_TIMEOUT = 15.0


class BrowseRequest(BaseModel):
    url: str


class AllowRequest(BaseModel):
    pattern: str


def _is_private_host(host: str) -> bool:
    """Return True if host resolves to a loopback or private/link-local address."""
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        try:
            addr = ipaddress.ip_address(socket.gethostbyname(host))
        except OSError:
            return False
    return addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved


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

    if not _is_allowed(url):
        raise HTTPException(
            403, "URL not in allowlist. Add a permission pattern first."
        )

    # One ephemeral session key per browse request — stored locally, never sent out.
    session_id, _ = new_session_key(context=f"browse:{url}")

    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=_TIMEOUT,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; local-browser/1.0)",
                "Accept": "text/html,text/plain;q=0.9",
                "Accept-Language": "en,*;q=0.5",
            },
        ) as client:
            resp = await client.get(url)
    except httpx.TimeoutException:
        raise HTTPException(504, f"Request timed out after {_TIMEOUT}s.")
    except httpx.RequestError as exc:
        raise HTTPException(502, f"Could not reach URL: {exc}")

    # Re-validate the final URL in case redirects led to a different origin.
    final_url = str(resp.url)
    final_host = urlparse(final_url).hostname or ""
    if _is_private_host(final_host):
        raise HTTPException(403, "Redirect to a private/internal address is not allowed.")
    if not _is_allowed(final_url):
        raise HTTPException(403, f"Redirect led to non-allowlisted URL: {final_url}")

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
                (doc_name, piece, json.dumps(vector)),
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
def add_permission(req: AllowRequest):
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
def remove_permission(pattern: str):
    with closing(get_db()) as db:
        db.execute("DELETE FROM browse_allowlist WHERE pattern = ?", (pattern,))
        db.commit()
    return {"removed": pattern}
