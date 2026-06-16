import email as email_lib
import email.header
import imaplib
import json
import re
from contextlib import closing

from fastapi import APIRouter, HTTPException

from config import (
    EMAIL_FOLDER, EMAIL_IMAP_HOST, EMAIL_IMAP_PORT,
    EMAIL_MAX_SYNC, EMAIL_PASSWORD, EMAIL_USER,
)
from db import get_db
from ingestion import chunk_text
from rag import embed

router = APIRouter()


def _decode_header(value: str) -> str:
    parts = email.header.decode_header(value or "")
    result = []
    for part, charset in parts:
        if isinstance(part, bytes):
            result.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            result.append(part)
    return " ".join(result)


def _strip_html(html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", text).strip()


def _extract_body(msg) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                raw = part.get_payload(decode=True)
                charset = part.get_content_charset() or "utf-8"
                return raw.decode(charset, errors="replace")
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                raw = part.get_payload(decode=True)
                charset = part.get_content_charset() or "utf-8"
                return _strip_html(raw.decode(charset, errors="replace"))
        return ""
    raw = msg.get_payload(decode=True)
    if not raw:
        return ""
    charset = msg.get_content_charset() or "utf-8"
    text = raw.decode(charset, errors="replace")
    return _strip_html(text) if msg.get_content_type() == "text/html" else text


@router.post("/api/email/sync")
async def sync_emails():
    if not (EMAIL_USER and EMAIL_PASSWORD and EMAIL_IMAP_HOST):
        raise HTTPException(400, "Email not configured. Set VOKTER_EMAIL_* env vars.")

    try:
        imap = imaplib.IMAP4_SSL(EMAIL_IMAP_HOST, EMAIL_IMAP_PORT)
        imap.login(EMAIL_USER, EMAIL_PASSWORD)
        imap.select(EMAIL_FOLDER, readonly=True)
    except Exception as exc:
        raise HTTPException(502, f"IMAP connection failed: {exc}")

    try:
        try:
            _, data = imap.search(None, "ALL")
            all_ids = data[0].split()
            to_fetch = all_ids[-EMAIL_MAX_SYNC:]
        except Exception as exc:
            raise HTTPException(502, f"IMAP search failed: {exc}")

        with closing(get_db()) as db:
            already_synced = {
                row[0] for row in db.execute("SELECT message_id FROM synced_emails").fetchall()
            }

        new_count = 0
        errors = 0

        for uid in reversed(to_fetch):  # newest first
            try:
                _, msg_data = imap.fetch(uid, "(RFC822)")
                if not isinstance(msg_data[0], tuple):
                    errors += 1
                    continue
                msg = email_lib.message_from_bytes(msg_data[0][1])

                message_id = msg.get("Message-ID", "").strip() or f"vokter-uid-{uid.decode()}"
                if message_id in already_synced:
                    continue

                subject = _decode_header(msg.get("Subject", "(no subject)"))
                sender  = _decode_header(msg.get("From", "unknown"))
                date    = msg.get("Date", "")
                body    = _extract_body(msg)

                if not body.strip():
                    continue

                full_text = f"From: {sender}\nDate: {date}\nSubject: {subject}\n\n{body}"
                chunks    = chunk_text(full_text)
                doc_name  = f"email::{message_id}"

                with closing(get_db()) as db:
                    for piece in chunks:
                        vector = await embed(piece)
                        db.execute(
                            "INSERT INTO chunks (doc, content, embedding) VALUES (?, ?, ?)",
                            (doc_name, piece, json.dumps(vector)),
                        )
                    db.execute(
                        "INSERT OR IGNORE INTO synced_emails "
                        "(message_id, subject, sender, date) VALUES (?, ?, ?, ?)",
                        (message_id, subject, sender, date),
                    )
                    db.commit()

                already_synced.add(message_id)
                new_count += 1

            except Exception:
                errors += 1
                continue

        return {"synced": new_count, "errors": errors, "total_known": len(already_synced)}
    finally:
        imap.logout()


@router.get("/api/email/status")
def email_status():
    configured = bool(EMAIL_USER and EMAIL_PASSWORD and EMAIL_IMAP_HOST)
    with closing(get_db()) as db:
        count = db.execute("SELECT COUNT(*) FROM synced_emails").fetchone()[0]
    return {"configured": configured, "synced_emails": count}


@router.delete("/api/email/all")
def delete_all_emails():
    with closing(get_db()) as db:
        chunks_removed = db.execute(
            "DELETE FROM chunks WHERE doc LIKE 'email::%'"
        ).rowcount
        emails_removed = db.execute("DELETE FROM synced_emails").rowcount
        db.commit()
    return {"emails_removed": emails_removed, "chunks_removed": chunks_removed}
