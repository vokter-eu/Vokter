"""
Agent personalisation API.

GET  /api/config        — returns current settings (with defaults for unset keys)
PATCH /api/config       — update one or more settings; returns full config after save
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from agent_config import DEFAULTS, get_config, set_config

router = APIRouter()

_VALID_TONE = {"formal", "neutral", "friendly"}
_VALID_MODE = {"productive", "conversational"}


class ConfigPatch(BaseModel):
    agent_name:  str | None = None
    tone:        str | None = None
    mode:        str | None = None
    language:    str | None = None
    chat_model:  str | None = None
    embed_model: str | None = None
    max_history: int | None = None
    rag_chunks:  int | None = None


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

    if patch.max_history is not None:
        if not 2 <= patch.max_history <= 200:
            raise HTTPException(400, "max_history must be between 2 and 200")
        updates["max_history"] = str(patch.max_history)

    if patch.rag_chunks is not None:
        if not 1 <= patch.rag_chunks <= 20:
            raise HTTPException(400, "rag_chunks must be between 1 and 20")
        updates["rag_chunks"] = str(patch.rag_chunks)

    if updates:
        set_config(updates)
    return get_config()
