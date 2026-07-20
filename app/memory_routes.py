"""Personal-memory API (Phase 1) — the transparency window.

GET    /api/memory        — everything Vokter knows about you (newest first)
POST   /api/memory        — add a fact  {content}
PATCH  /api/memory/{id}   — correct a fact  {content}
DELETE /api/memory/{id}   — forget one fact
DELETE /api/memory        — "forget everything about me" (real delete + VACUUM)

Loopback-only, like the rest of the local API. The user sees and controls ALL of
it — nothing is hidden.
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import memory

router = APIRouter()


class MemoryIn(BaseModel):
    content: str


@router.get("/api/memory")
def memory_list():
    return {"memory": memory.list_all()}


@router.post("/api/memory")
def memory_add(item: MemoryIn):
    content = item.content.strip()
    if not content:
        raise HTTPException(400, "empty memory")
    return memory.add(content, source="told")


@router.patch("/api/memory/{mem_id}")
def memory_edit(mem_id: int, item: MemoryIn):
    content = item.content.strip()
    if not content:
        raise HTTPException(400, "empty memory")
    if not memory.edit(mem_id, content):
        raise HTTPException(404, "no such memory")
    return {"ok": True, "id": mem_id, "content": content}


@router.delete("/api/memory/{mem_id}")
def memory_delete(mem_id: int):
    if not memory.delete(mem_id):
        raise HTTPException(404, "no such memory")
    return {"ok": True, "id": mem_id}


@router.delete("/api/memory")
def memory_forget_all():
    removed = memory.forget_all()
    return {"ok": True, "forgotten": removed}
