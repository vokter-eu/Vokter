"""Tier-3 Phase 3 — outbound EMAIL: send from Vokter's OWN address under a per-task unlinkable alias.

OPT-IN, DEFAULT OFF, behind its OWN gate (TIER-8). The LAST Tier-3 secret. See
vokter-internal/TIER3_PHASE3_EMAIL.md. `email_connector.py` stays INBOUND-ONLY and is untouched — this is
a wholly new outbound surface.

The shape, mirroring login/pay/NWC:
  * guard("email.send") is CONFIRM-human-only / BLOCK peer/mcp/autonomous — a PLAIN CONFIRM/BLOCK action
    (there is NO autonomous email path, unlike NWC). Two-phase, NEVER auto-send.
  * The per-task alias is a key-layer-unlinkable L2 derivation (identity.email_alias_key) — Vokter's own
    address, NEVER the user's social npub. The alias↔task map (encrypted DB) is load-bearing: email is
    bidirectional, so an inbound REPLY routes back to its task via task_for_alias().
  * The body is built by INCLUSION from a disclosure whitelist (fails closed); anti-confabulation guards
    drafts (a mitigation — the STRUCTURAL bound is the human seeing exactly what leaves).
  * The transport (own-domain SMTP / self-hosted relay — pluggable, no hardcoded provider) is provisioned
    by the human; its credential lives in the keychain (service 'vokter-email'), NEVER to the model/logs,
    asserted absent in the TIER-8 leakage probe. The relay SEES the metadata (who/when) — surfaced honestly
    in CONNECT_CAVEAT (email is the least-sovereign Tier-3 secret; unlike a QR payment, something leaves).

T24 (inbound→outbound injection): the untrusted received-email corpus must NOT be able to drive a send.
This module never reads inbound content to populate a recipient/body, and email.send BLOCKs every non-human
caller — so a malicious inbound email can never silently trigger a send. The confirm gate is the structural
bound; asserted in the TIER-8 fire-test.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
from contextlib import closing

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

router = APIRouter()

_KC_SERVICE = "vokter-email"
_KC_NAME = "transport"


# ───────────────────────── the TIER-8 ship gate (SEPARATE from the other Tier-3 gates) ─────────────────────────
_GATE_ENV = "VOKTER_EMAIL_SEND_GATE"   # set to "passed" ONLY by the packaged TIER-8 fire-test, after both
                                       # halves (non-leakage + confirm-integrity + T24) are green on the
                                       # frozen .deb. Never rides login/pay/NWC green.


def email_gate_ok() -> bool:
    """True only once the TIER-8 fire-test has passed on THIS install. Default OFF → send refused (503)."""
    return os.environ.get(_GATE_ENV, "").strip().lower() == "passed"


# The honest floor — the connect UI MUST show this before a transport is provisioned (email is the least
# sovereign Tier-3 secret; the relay sees the metadata).
CONNECT_CAVEAT = (
    "Sending email needs a transport (your own domain's mail server, or a self-hosted relay). Unlike a QR "
    "payment where nothing leaves, the transport you configure SEES who you email and when — it custodies "
    "that metadata, and all of Vokter's aliases share your sending domain, so a recipient can tell two "
    "messages came from the same domain even though the alias per task differs. Vokter never sends from "
    "your personal identity, never auto-sends, and the credential stays in your OS keychain — but email is "
    "the least private of Vokter's actions. Prefer your own domain + mail server; avoid shared, "
    "reputation-poisoned anonymous-email domains (worse deliverability, and they void the per-task alias)."
)


# ───────────────────────── per-task unlinkable alias + the load-bearing reply map ─────────────────────────
def alias_localpart(task_id: str) -> str:
    """Deterministic, per-task, key-layer-unlinkable localpart — a base32 truncation of the L2 alias key
    (identity.email_alias_key). Deterministic within a task (replies route back); unlinkable across tasks."""
    from identity import email_alias_key
    raw = email_alias_key(task_id)
    return base64.b32encode(raw).decode("ascii").rstrip("=").lower()[:12]


def _domain() -> str:
    """Vokter's sending domain, from the provisioned transport (own domain / relay). Empty → unconfigured."""
    cfg = _transport.get()
    return (cfg or {}).get("from_domain", "").strip().lower()


def ensure_alias(task_id: str, counterparty: str = "") -> str:
    """Return the per-task alias (localpart@domain), creating + recording the alias↔task map row if new.
    Vokter's OWN address — never the user's social npub. Requires a provisioned transport (for the domain)."""
    domain = _domain()
    if not domain:
        raise HTTPException(503, "email transport not configured (no sending domain)")
    alias = f"{alias_localpart(task_id)}@{domain}"
    from db import get_db
    with closing(get_db()) as db:
        row = db.execute("SELECT task_id FROM email_aliases WHERE alias = ?", (alias,)).fetchone()
        if row is None:
            from datetime import datetime, timezone
            db.execute(
                "INSERT INTO email_aliases (alias, task_id, counterparty, created_at) VALUES (?, ?, ?, ?)",
                (alias, task_id, counterparty, datetime.now(timezone.utc).isoformat()),
            )
            db.commit()
    return alias


def task_for_alias(alias: str) -> str | None:
    """Reply-routing lookup (bidirectional email): the task an inbound reply to `alias` belongs to. This is
    the hook `email_connector.py` (inbound) would consult to thread a reply back to its task context —
    kept HERE so the inbound module stays untouched."""
    from db import get_db
    with closing(get_db()) as db:
        row = db.execute("SELECT task_id FROM email_aliases WHERE alias = ?", ((alias or "").strip().lower(),)).fetchone()
    return row[0] if row else None


# ───────────────────────── disclosure-whitelist body builder (INCLUSION, fails closed) ─────────────────────────
# Field classes that NEVER leave in an email body even if a template lists them (fail-closed, §3).
_BLOCKED_FIELD_CLASSES = {"identity", "health", "secret", "npub", "nsec", "password", "master_key"}


def build_body(disclosure: dict, fields: list[str]) -> str:
    """Assemble the body by INCLUSION of ONLY the whitelisted `fields` present in `disclosure`, in order.
    Anything not named is structurally absent (no 'forgot to redact' leak). Blocked field-classes are
    dropped even if whitelisted. The model never free-queries memory — `disclosure` is the human-authored
    payload. Anti-confabulation (drafts): the drafter may not invent fields beyond this payload; the
    STRUCTURAL bound remains the human seeing the final body in the confirm sheet."""
    lines = []
    for key in (fields or []):
        k = str(key).strip()
        if not k or k.lower() in _BLOCKED_FIELD_CLASSES:
            continue                       # fail-closed: blocked class never included
        if k in disclosure and disclosure[k] not in (None, ""):
            lines.append(f"{k}: {disclosure[k]}")
    return "\n".join(lines)


# ───────────────────────── confirm-integrity token (what's sent == what the human confirmed) ─────────────────────────
_CONFIRM_KEY = secrets.token_bytes(32)    # per-process; a phase-1 token can't be forged or replayed across restarts


def _confirm_token(alias: str, to: str, subject: str, body: str) -> str:
    # ⛔ BLOCKING — FIX BEFORE WIRING ATTACHMENTS: this token binds ONLY (alias, to, subject, body).
    # Attachments are NOT supported in this slice. The moment an `attachments` field is added to
    # EmailSendRequest / the sender, it MUST be folded in here (and in _verify_confirm_token) in the SAME
    # change — otherwise a phase-2 attachment-swap passes confirm-integrity (human approves A, phase-2 sends
    # B, token still matches). No attachments ship until they are in this token. See TIER3_PHASE3_EMAIL.md §3a.
    mac = hmac.new(_CONFIRM_KEY, b"\x00".join(x.encode() for x in (alias, to, subject, body)), hashlib.sha256)
    return mac.hexdigest()


def _verify_confirm_token(token: str, alias: str, to: str, subject: str, body: str) -> bool:
    return bool(token) and hmac.compare_digest(token, _confirm_token(alias, to, subject, body))


# ───────────────────────── the transport (pluggable; credential in the keychain) ─────────────────────────
class KeychainTransport:
    """The provisioned SMTP/submission transport — own-domain mail server or self-hosted relay. The config
    (incl. the password) lives in the OS keychain (service 'vokter-email'); status() returns NON-SECRET
    fields only, never the password. Pluggable: no provider is hardcoded."""

    def _kc(self):
        from oauth_broker import import_keychain
        return import_keychain()

    def put(self, cfg: dict) -> None:
        self._kc().set_key(json.dumps(cfg), service=_KC_SERVICE, name=_KC_NAME)

    def get(self) -> dict | None:
        """INTERNAL — includes the password. Never surfaced to a response/model/log."""
        raw = self._kc().get_key(service=_KC_SERVICE, name=_KC_NAME)
        try:
            return json.loads(raw) if raw else None
        except Exception:
            return None

    def status(self) -> dict:
        cfg = self.get() or {}
        return {"configured": bool(cfg), "host": cfg.get("host", ""), "port": cfg.get("port", 0),
                "username": cfg.get("username", ""), "from_domain": cfg.get("from_domain", "")}  # NO password

    def delete(self) -> bool:
        return self._kc().delete_key(service=_KC_SERVICE, name=_KC_NAME)


class InMemoryTransport:
    """Test double — provision a transport without a keychain. Never used in production."""
    def __init__(self):
        self._cfg: dict | None = None
    def put(self, cfg): self._cfg = cfg
    def get(self): return self._cfg
    def status(self):
        c = self._cfg or {}
        return {"configured": bool(c), "host": c.get("host", ""), "port": c.get("port", 0),
                "username": c.get("username", ""), "from_domain": c.get("from_domain", "")}
    def delete(self):
        had = self._cfg is not None; self._cfg = None; return had


_transport = KeychainTransport()


def _smtp_transport(cfg: dict, from_alias: str, to: str, subject: str, body: str) -> tuple[bool, str]:
    """Submit one message over the provisioned transport (smtplib). Reads the credential ONLY to hand it to
    the server; returns STATUS ONLY — never the password/credential. Patched by the hermetic TIER-8
    fire-test with a mock SMTP sink (no network, no real recipient)."""
    import smtplib
    from email.message import EmailMessage
    msg = EmailMessage()
    msg["From"] = from_alias; msg["To"] = to; msg["Subject"] = subject
    msg.set_content(body)
    try:
        with smtplib.SMTP(cfg["host"], int(cfg.get("port", 587)), timeout=30) as s:
            if cfg.get("starttls", True):
                s.starttls()
            if cfg.get("username"):
                s.login(cfg["username"], cfg.get("password", ""))
            s.send_message(msg)
        return True, "sent"
    except Exception:
        return False, "failed"   # status only — never surface the exception (no credential, no detail leak)


def _deliver(from_alias: str, to: str, subject: str, body: str) -> tuple[bool, str]:
    """Read the transport config (secret) only to hand to the transport, then drop the reference."""
    cfg = _transport.get()
    if not cfg:
        return False, "no_transport"
    try:
        return _smtp_transport(cfg, from_alias, to, subject, body)
    finally:
        del cfg   # minimize the credential's lifetime (a copy may transit smtplib)


# ───────────────────────── the gated two-phase route (the email.send action) ─────────────────────────
class EmailSendRequest(BaseModel):
    task_id: str                       # which task this send belongs to (→ the per-task unlinkable alias).
    to: str                            # the recipient address (human-entered; shown in the confirm sheet).
    subject: str = ""
    disclosure: dict = {}              # human-authored field values (NOT a model query over memory).
    fields: list[str] = []             # the whitelist: ONLY these field keys may appear in the body.
    counterparty: str = ""             # optional label for the alias↔task map.
    confirm: bool = False              # the human's explicit per-send approval (MANDATORY — never auto-send).
    confirm_token: str = ""            # echoed from the phase-1 428 — binds phase-2 to the confirmed message.


@router.post("/api/email/send")
def email_send(req: EmailSendRequest, x_vokter_human_session: str | None = Header(default=None)):
    """Send an email from Vokter's per-task unlinkable alias, WITHOUT ever auto-sending. Two phases:

      Phase 1 (confirm=false) — CONSENT. enforce_http BLOCKs non-human (403) BEFORE anything. Resolve the
        per-task alias, build the body by INCLUSION from the disclosure whitelist, and return a 428 with
        {recipient, alias, subject, body, attachments, confirm_token} for the human to review in full.
      Phase 2 (confirm=true) — the human approved THIS message. Re-derive alias + body, verify the message
        is BYTE-IDENTICAL to what was confirmed (confirm-integrity, via the token), and submit over the
        provisioned transport. A substituted recipient/body → 422, nothing sent.

    GateNotPassed → 503 (TIER-8 not green on this install). CONFIRM-human-only; there is no autonomous path.
    """
    from chat import is_local_human_session
    from safety import HUMAN, enforce_http

    # Context is derived locally from the human-session header — a peer/MCP caller can never present one, and
    # email.send BLOCKs every non-human context anyway (plain CONFIRM/BLOCK; no mandate path). This is the
    # T24 structural bound: nothing but the local human can drive a send.
    context = HUMAN if is_local_human_session(x_vokter_human_session) else "autonomous"
    target = f"email to {req.to} (task {req.task_id})"   # gateway-recorded; recipient is not a secret

    if not req.confirm:
        try:
            enforce_http("email.send", target, context=context, confirmed=False)
        except HTTPException as e:
            if e.status_code != 428:
                raise                                   # non-human → 403 propagates; nothing drafted (T24)
        if not email_gate_ok():
            raise HTTPException(503, "email send unavailable: the TIER-8 self-test has not passed on this install")
        alias = ensure_alias(req.task_id, req.counterparty)
        body = build_body(req.disclosure, req.fields)
        token = _confirm_token(alias, req.to, req.subject, body)
        raise HTTPException(428, detail={
            "error": "confirmation_required", "action": "email.send",
            "recipient": req.to, "alias": alias, "subject": req.subject, "body": body,
            "attachments": [], "confirm_token": token})

    # Phase 2 — confirmed. The human approved the exact message the phase-1 428 displayed.
    enforce_http("email.send", target, context=context, confirmed=True)        # non-human → 403; never auto-send
    if not email_gate_ok():
        raise HTTPException(503, "email send unavailable: the TIER-8 self-test has not passed on this install")
    alias = ensure_alias(req.task_id, req.counterparty)
    body = build_body(req.disclosure, req.fields)
    # ── CONFIRM-INTEGRITY (code, never the model): a substituted recipient/body no longer matches the token ──
    if not _verify_confirm_token(req.confirm_token, alias, req.to, req.subject, body):
        raise HTTPException(422, "REJECTED: recipient/body do not match what you confirmed (confirm-integrity)")
    sent, status = _deliver(alias, req.to, req.subject, body)
    if not sent:
        raise HTTPException(502, f"transport declined or unreachable ({status})")
    return {"status": "sent", "alias": alias, "recipient": req.to, "subject": req.subject}


# ───────────────────────── transport provisioning (HUMAN-only — opt-in enable/disable) ─────────────────────────
class EmailTransportRequest(BaseModel):
    host: str
    port: int = 587
    username: str = ""
    password: str = ""             # stored in the keychain, NEVER echoed back.
    from_domain: str               # Vokter's sending domain (own domain / relay domain).
    starttls: bool = True


def _require_human(x_vokter_human_session: str | None) -> None:
    from chat import is_local_human_session
    if not is_local_human_session(x_vokter_human_session):
        raise HTTPException(403, "configuring the email transport is owner-only (local human session)")


@router.post("/api/email/transport/connect")
def transport_connect(req: EmailTransportRequest, x_vokter_human_session: str | None = Header(default=None)):
    """Opt in: store the outbound transport (own-domain SMTP / self-hosted relay). HUMAN-only. The password
    goes to the keychain and is NEVER echoed; the response carries the non-secret status + the honest caveat."""
    _require_human(x_vokter_human_session)
    if not req.host.strip() or not req.from_domain.strip():
        raise HTTPException(400, "host and from_domain are required")
    _transport.put({"host": req.host.strip(), "port": int(req.port), "username": req.username.strip(),
                    "password": req.password, "from_domain": req.from_domain.strip().lower(),
                    "starttls": bool(req.starttls)})
    return {"status": "connected", "transport": _transport.status(), "caveat": CONNECT_CAVEAT}


@router.post("/api/email/transport/revoke")
def transport_revoke(x_vokter_human_session: str | None = Header(default=None)):
    """One-action removal of the stored transport credential. HUMAN-only."""
    _require_human(x_vokter_human_session)
    ok = _transport.delete()
    return {"status": "revoked" if ok else "not_found", "caveat": CONNECT_CAVEAT}


@router.get("/api/email/transport/status")
def transport_status(x_vokter_human_session: str | None = Header(default=None)):
    """Non-secret transport status (never the password). HUMAN-only."""
    _require_human(x_vokter_human_session)
    return {"transport": _transport.status(), "caveat": CONNECT_CAVEAT}
