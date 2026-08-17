"""
Agent personalisation API.

GET    /api/config          — returns current settings (with defaults for unset keys)
PATCH  /api/config          — update one or more settings; returns full config after save
GET    /api/models          — installed models + active/default/engine_url (for the picker)
POST   /api/models/pull     — pull a model into the resolved engine, streaming SSE progress
POST   /api/config/avatar   — upload avatar image (jpg/png/webp/gif)
GET    /api/config/avatar   — serve current avatar image
DELETE /api/config/avatar   — remove avatar
"""
import json
import os
import shutil

import httpx
from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from agent_config import DEFAULTS, get_config, set_config
from config import DATA_DIR, CHAT_MODEL
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
    """Chat models installed in the RESOLVED engine (bundled by default, or the user's
    engine_url override), for the Model & tone picker and the chat model badge.

    Proxies the engine's model list (Ollama /api/tags) — the loopback UI can't reach
    the engine directly under its same-origin CSP. Embedding models are filtered out.
    `active` is the model chat.py will actually use (cfg.chat_model or the env default)
    computed IDENTICALLY so the badge can never disagree with real resolution; `default`
    is that env fallback's real name (shown as "(current default)" instead of an abstract
    "Default"). `engine_url` lets the UI show whether an external engine is in effect.
    On any engine error the model list is empty but active/default/engine_url still return
    so the UI degrades gracefully."""
    cfg = get_config()
    default = CHAT_MODEL
    active = (cfg.get("chat_model") or "").strip() or default
    engine_url = (cfg.get("engine_url") or "").strip()
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{resolve_base_url()}/api/tags")
            r.raise_for_status()
            data = r.json()
        names = [m.get("name", "") for m in data.get("models", []) if m.get("name")]
        models = sorted(n for n in names if "embed" not in n.lower())  # drop embedding models
    except Exception:
        models = []
    return {"models": models, "active": active, "default": default, "engine_url": engine_url}


class PullRequest(BaseModel):
    name: str


@router.post("/api/models/pull")
async def pull_model(req: PullRequest):
    """Pull a model into the RESOLVED engine (bundled by default) and stream progress as
    SSE, so a non-technical user never needs a terminal. Same-origin (the page fetches
    this endpoint under connect-src 'self'); the loading-screen IPC is startup-only and
    doesn't fit here.

    Progress mirrors desktop/model_pull.py's two non-obvious rules WITHOUT importing it
    (that module isn't on the web backend's dev path): aggregate completed/total across
    every layer SEEN, and clamp the percent MONOTONIC so a new layer never bounces the bar
    backward. The pull client uses NO timeout — a multi-GB pull has manifest/verify gaps
    far longer than httpx's 5s default, which would otherwise kill the stream mid-download."""
    name = (req.name or "").strip()
    if not name:
        raise HTTPException(400, "model name required")
    base = resolve_base_url()

    def _sse(d: dict) -> str:
        return f"data: {json.dumps(d)}\n\n"

    async def gen():
        seen: dict[str, tuple[int, int]] = {}   # digest → (completed, total) across layers
        last_pct = 0.0
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(None)) as client:
                async with client.stream("POST", f"{base}/api/pull",
                                         json={"name": name, "stream": True}) as r:
                    if r.status_code != 200:
                        await r.aread()
                        yield _sse({"error": f"engine returned {r.status_code} for '{name}'"})
                        return
                    async for line in r.aiter_lines():
                        if not line.strip():
                            continue
                        try:
                            o = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if o.get("error"):
                            yield _sse({"error": str(o["error"])})
                            return
                        status = o.get("status", "")
                        digest = o.get("digest")
                        if digest and o.get("total"):
                            seen[digest] = (int(o.get("completed", 0)), int(o.get("total", 0)))
                        comp = sum(c for c, _ in seen.values())
                        tot = sum(t for _, t in seen.values())
                        pct = (comp / tot * 100.0) if tot else last_pct
                        if pct < last_pct:            # monotonic: a new layer must not rewind the bar
                            pct = last_pct
                        last_pct = pct
                        if status == "success":
                            yield _sse({"status": "success", "percent": 100.0, "done": True})
                            return
                        yield _sse({"status": status, "completed": comp, "total": tot,
                                    "percent": round(pct, 1), "indeterminate": tot == 0})
        except Exception:
            yield _sse({"error": "pull failed — check the model name, disk space and your connection"})
            return
        yield _sse({"percent": 100.0, "done": True})   # stream ended without an explicit 'success'

    return StreamingResponse(gen(), media_type="text/event-stream")


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
