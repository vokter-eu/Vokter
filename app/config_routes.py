"""
Agent personalisation API.

GET    /api/config          — returns current settings (with defaults for unset keys)
PATCH  /api/config          — update one or more settings; returns full config after save
GET    /api/models          — chat models installed in the local engine (for the picker)
POST   /api/config/avatar   — upload avatar image (jpg/png/webp/gif)
GET    /api/config/avatar   — serve current avatar image
DELETE /api/config/avatar   — remove avatar
"""
import os
import shutil

import httpx
from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from agent_config import DEFAULTS, get_config, set_config
from config import DATA_DIR
from engine import resolve_base_url

router = APIRouter()

_VALID_TONE = {"formal", "neutral", "friendly"}
_VALID_MODE = {"productive", "conversational"}
_AVATAR_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}


class ConfigPatch(BaseModel):
    agent_name:  str | None = None
    tone:        str | None = None
    mode:        str | None = None
    language:    str | None = None
    chat_model:  str | None = None
    embed_model: str | None = None
    engine_url:  str | None = None
    max_history: int | None = None
    rag_chunks:  int | None = None
    onboarded:   bool | None = None


@router.get("/api/config")
def config_get():
    return get_config()


@router.patch("/api/config")
def config_patch(patch: ConfigPatch):
    updates: dict[str, str] = {}

    if patch.agent_name is not None:
        name = patch.agent_name.strip()
        if not name:
            raise HTTPException(400, "agent_name cannot be empty")
        updates["agent_name"] = name

    if patch.tone is not None:
        if patch.tone not in _VALID_TONE:
            raise HTTPException(400, f"tone must be one of: {', '.join(sorted(_VALID_TONE))}")
        updates["tone"] = patch.tone

    if patch.mode is not None:
        if patch.mode not in _VALID_MODE:
            raise HTTPException(400, f"mode must be one of: {', '.join(sorted(_VALID_MODE))}")
        updates["mode"] = patch.mode

    if patch.language is not None:
        updates["language"] = patch.language.strip() or "auto"

    if patch.chat_model is not None:
        updates["chat_model"] = patch.chat_model.strip()

    if patch.embed_model is not None:
        updates["embed_model"] = patch.embed_model.strip()

    if patch.engine_url is not None:
        # Empty → back to the bundled sovereign engine. A value must be an http(s) URL
        # (this is the one setting that redirects WHERE the user's data goes, so reject
        # anything that isn't plainly an engine endpoint — file://, javascript:, etc.).
        # Trailing slash trimmed so it composes with the adapter's f"{base}/api/…".
        url = patch.engine_url.strip()
        if url and not url.startswith(("http://", "https://")):
            raise HTTPException(400, "engine_url must be an http(s) URL, "
                                     "or empty to use the bundled engine")
        updates["engine_url"] = url.rstrip("/")

    if patch.max_history is not None:
        if not 2 <= patch.max_history <= 200:
            raise HTTPException(400, "max_history must be between 2 and 200")
        updates["max_history"] = str(patch.max_history)

    if patch.rag_chunks is not None:
        if not 1 <= patch.rag_chunks <= 20:
            raise HTTPException(400, "rag_chunks must be between 1 and 20")
        updates["rag_chunks"] = str(patch.rag_chunks)

    if patch.onboarded is not None:
        updates["onboarded"] = "1" if patch.onboarded else "0"

    if updates:
        set_config(updates)
    return get_config()


@router.get("/api/models")
async def list_models():
    """Chat models installed in the local engine, for the Model & tone picker.

    Proxies the engine's model list (Ollama /api/tags) — the loopback UI can't reach
    the engine directly under its same-origin CSP. Embedding models are filtered out
    so only chat-capable models appear. Returns {"models": [...]} sorted; an empty
    list on any error so the picker degrades gracefully (falls back to the default)."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{resolve_base_url()}/api/tags")
            r.raise_for_status()
            data = r.json()
    except Exception:
        return {"models": []}
    names = [m.get("name", "") for m in data.get("models", []) if m.get("name")]
    chat = [n for n in names if "embed" not in n.lower()]  # drop embedding models
    return {"models": sorted(chat)}


def _avatar_path() -> str | None:
    for ext in _AVATAR_EXTS:
        p = os.path.join(DATA_DIR, f"avatar{ext}")
        if os.path.exists(p):
            return p
    return None


@router.post("/api/config/avatar")
async def avatar_upload(file: UploadFile = File(...)):
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in _AVATAR_EXTS:
        raise HTTPException(400, f"Unsupported format. Use: jpg, png, webp, gif")
    os.makedirs(DATA_DIR, exist_ok=True)
    # Remove previous avatar (any extension)
    for old_ext in _AVATAR_EXTS:
        old = os.path.join(DATA_DIR, f"avatar{old_ext}")
        if os.path.exists(old):
            os.remove(old)
    dest = os.path.join(DATA_DIR, f"avatar{ext}")
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)
    return {"ok": True, "url": "/api/config/avatar"}


@router.api_route("/api/config/avatar", methods=["GET", "HEAD"])
def avatar_get():
    path = _avatar_path()
    if not path:
        raise HTTPException(404, "No avatar set")
    return FileResponse(path)


@router.delete("/api/config/avatar")
def avatar_delete():
    path = _avatar_path()
    if not path:
        raise HTTPException(404, "No avatar to delete")
    os.remove(path)
    return {"ok": True}
