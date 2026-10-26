"""Backup & recovery API — sovereign, human-only. Set up recovery / export / restore-validate.

Crypto lives in backup.py (proven round-trip). Model = rekey-copy-only: the LIVE DB is never modified
(read with its passphrase; only the backup COPY is re-keyed to a fresh raw key). The recovery phrase
is shown ONCE and never persisted; only (salt, KEK) are stored locally — both derivable from the
phrase, so losing the machine loses nothing. Loopback + the P2 human-session gate, like memory.
"""
import base64
import json
import os
from contextlib import closing

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

import backup
from chat import is_local_human_session
from config import DB_KEY, DB_PATH
from db import get_db

router = APIRouter()


def _require_human(mark: str | None) -> None:
    if not is_local_human_session(mark):
        raise HTTPException(403, "backup is available only to the local human session")


def _set_cfg(k: str, v: str) -> None:
    with closing(get_db()) as db:
        db.execute("INSERT OR REPLACE INTO agent_config(key, value) VALUES(?,?)", (k, v))
        db.commit()


def _get_cfg(k: str) -> str | None:
    with closing(get_db()) as db:
        r = db.execute("SELECT value FROM agent_config WHERE key=?", (k,)).fetchone()
    return r[0] if r else None


class SetupIn(BaseModel):
    words: int = 12          # 12 (default) or 24 — both fully secure, 24 is just longer


@router.post("/api/backup/setup")
def backup_setup(req: SetupIn, x_vokter_human_session: str | None = Header(default=None)):
    """Generate a recovery phrase (shown ONCE) and store (salt, KEK) locally. The phrase itself is
    never persisted — the caller must show it and get a written-down confirmation before finishing."""
    _require_human(x_vokter_human_session)
    if req.words not in (12, 24):
        raise HTTPException(400, "words must be 12 or 24")
    mnemonic, salt, kek = backup.setup_recovery(req.words)
    _set_cfg("backup_salt", base64.b64encode(salt).decode())
    _set_cfg("backup_kek", base64.b64encode(kek).decode())
    _set_cfg("backup_recovery_ready", "0")          # not confirmed until the user attests they wrote it
    return {"mnemonic": mnemonic, "words": req.words}


@router.post("/api/backup/confirm-written")
def backup_confirm(x_vokter_human_session: str | None = Header(default=None)):
    """The user attests they wrote the phrase down — recovery is now set up. (We cannot verify the
    paper; this gate just prevents 'finishing' setup without the explicit acknowledgement.)"""
    _require_human(x_vokter_human_session)
    if not (_get_cfg("backup_salt") and _get_cfg("backup_kek")):
        raise HTTPException(400, "set up recovery first")
    _set_cfg("backup_recovery_ready", "1")
    return {"ok": True}


@router.get("/api/backup/status")
def backup_status(x_vokter_human_session: str | None = Header(default=None)):
    _require_human(x_vokter_human_session)
    return {"recovery_ready": _get_cfg("backup_recovery_ready") == "1"}


class ExportIn(BaseModel):
    out_path: str            # a file in the user's own folder (they sync it themselves — no cloud creds here)


@router.post("/api/backup/export")
def backup_export(req: ExportIn, x_vokter_human_session: str | None = Header(default=None)):
    """Write a consistent encrypted snapshot (VACUUM INTO) + manifest, re-keying only the COPY; verify
    it opens before returning. The live DB is untouched. The user stores the file wherever they like
    (USB / their own synced cloud folder) — Vokter holds no cloud credentials."""
    _require_human(x_vokter_human_session)
    salt_b, kek_b = _get_cfg("backup_salt"), _get_cfg("backup_kek")
    if not (salt_b and kek_b):
        raise HTTPException(400, "set up recovery first")
    if not DB_KEY:
        raise HTTPException(400, "this Vokter has no encryption key to back up")
    out = os.path.abspath(os.path.expanduser(req.out_path))
    try:
        manifest = backup.export_backup(DB_PATH, DB_KEY, out,
                                        base64.b64decode(kek_b), base64.b64decode(salt_b))
    except backup.BackupError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "backup_path": out, "created_at": manifest["created_at"]}


class RestoreIn(BaseModel):
    backup_path: str
    phrase: str


@router.post("/api/backup/restore-validate")
def backup_restore_validate(req: RestoreIn, x_vokter_human_session: str | None = Header(default=None)):
    """Validate that the recovery phrase opens this backup (reconstruct K + open the DB), WITHOUT
    touching the live install. Placing the restored DB + seeding the keychain + relaunching into it is
    the desktop orchestrator's job (it owns key/DB setup at boot); this endpoint is the crypto gate the
    Restore UI calls first. A wrong phrase → a clean 400 'does not match this backup'."""
    _require_human(x_vokter_human_session)
    bp = os.path.abspath(os.path.expanduser(req.backup_path))
    mpath = bp + ".manifest.json"
    if not (os.path.exists(bp) and os.path.exists(mpath)):
        raise HTTPException(400, "backup file or its manifest not found")
    try:
        manifest = json.load(open(mpath))
        backup.restore_key(bp, manifest, req.phrase.split())     # raises BackupError on a wrong phrase
    except backup.BackupError as e:
        raise HTTPException(400, str(e))
    except Exception:
        raise HTTPException(400, "could not read the backup manifest")
    return {"ok": True, "valid": True, "created_at": manifest.get("created_at"),
            "schema_version": manifest.get("schema_version")}
