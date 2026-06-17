"""
CRUD API for scheduled tasks.

Interval format accepted: "5m", "2h", "1d" (min 5 minutes).
The scheduler loop in scheduler.py picks up enabled tasks every 60 seconds.
"""
import time
import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, field_validator

from db import get_db

router = APIRouter()

_UNITS = {"m": 60, "h": 3600, "d": 86400}
_MIN_SECONDS = 300  # 5 minutes


def _parse_interval(s: str) -> int:
    s = s.strip().lower()
    if not s or s[-1] not in _UNITS or not s[:-1].isdigit():
        raise ValueError(f"Invalid interval {s!r}. Use e.g. '30m', '2h', '1d'")
    seconds = int(s[:-1]) * _UNITS[s[-1]]
    if seconds < _MIN_SECONDS:
        raise ValueError("Minimum interval is 5 minutes (5m)")
    return seconds


class CreateTask(BaseModel):
    name: str
    goal: str
    interval: str

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("name is required")
        return v.strip()

    @field_validator("goal")
    @classmethod
    def goal_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("goal is required")
        return v.strip()

    @field_validator("interval")
    @classmethod
    def interval_valid(cls, v: str) -> str:
        _parse_interval(v)  # raises ValueError if invalid
        return v.strip()


class PatchTask(BaseModel):
    enabled: bool | None = None
    name: str | None = None
    goal: str | None = None
    interval: str | None = None


def _row_to_task(row) -> dict:
    task_id, name, goal, interval_seconds, next_run, last_run, enabled, created_at = row
    return {
        "id": task_id,
        "name": name,
        "goal": goal,
        "interval_seconds": interval_seconds,
        "next_run": next_run,
        "last_run": last_run,
        "enabled": bool(enabled),
        "created_at": created_at,
    }


def _run_row(row) -> dict:
    run_id, task_id, started_at, finished_at, status, output = row
    return {
        "id": run_id,
        "task_id": task_id,
        "started_at": started_at,
        "finished_at": finished_at,
        "status": status,
        "output": output,
    }


@router.post("/api/schedule", status_code=201)
def create_task(req: CreateTask):
    task_id = str(uuid.uuid4())
    now = time.time()
    interval_seconds = _parse_interval(req.interval)
    with get_db() as db:
        db.execute(
            "INSERT INTO scheduled_tasks(id,name,goal,interval_seconds,next_run,enabled,created_at)"
            " VALUES(?,?,?,?,?,1,?)",
            (task_id, req.name, req.goal, interval_seconds, now, now),
        )
        db.commit()
    return {"id": task_id, "name": req.name, "interval_seconds": interval_seconds}


@router.get("/api/schedule")
def list_tasks():
    with get_db() as db:
        rows = db.execute(
            "SELECT id,name,goal,interval_seconds,next_run,last_run,enabled,created_at"
            " FROM scheduled_tasks ORDER BY created_at DESC"
        ).fetchall()
    return [_row_to_task(r) for r in rows]


@router.get("/api/schedule/{task_id}")
def get_task(task_id: str):
    with get_db() as db:
        row = db.execute(
            "SELECT id,name,goal,interval_seconds,next_run,last_run,enabled,created_at"
            " FROM scheduled_tasks WHERE id=?",
            (task_id,),
        ).fetchone()
    if not row:
        raise HTTPException(404, "Task not found")
    return _row_to_task(row)


@router.patch("/api/schedule/{task_id}")
def patch_task(task_id: str, req: PatchTask):
    with get_db() as db:
        row = db.execute(
            "SELECT id,name,goal,interval_seconds,next_run,last_run,enabled,created_at"
            " FROM scheduled_tasks WHERE id=?",
            (task_id,),
        ).fetchone()
        if not row:
            raise HTTPException(404, "Task not found")

        updates = {}
        if req.enabled is not None:
            updates["enabled"] = int(req.enabled)
        if req.name is not None:
            if not req.name.strip():
                raise HTTPException(400, "name cannot be empty")
            updates["name"] = req.name.strip()
        if req.goal is not None:
            if not req.goal.strip():
                raise HTTPException(400, "goal cannot be empty")
            updates["goal"] = req.goal.strip()
        if req.interval is not None:
            interval_seconds = _parse_interval(req.interval)
            updates["interval_seconds"] = interval_seconds
            # reschedule immediately when interval changes
            updates["next_run"] = time.time()

        if not updates:
            raise HTTPException(400, "No fields to update")

        set_clause = ", ".join(f"{k}=?" for k in updates)
        db.execute(
            f"UPDATE scheduled_tasks SET {set_clause} WHERE id=?",
            (*updates.values(), task_id),
        )
        db.commit()

    return get_task(task_id)


@router.delete("/api/schedule/{task_id}", status_code=204)
def delete_task(task_id: str):
    with get_db() as db:
        cur = db.execute("DELETE FROM scheduled_tasks WHERE id=?", (task_id,))
        db.commit()
    if cur.rowcount == 0:
        raise HTTPException(404, "Task not found")


@router.get("/api/schedule/{task_id}/runs")
def task_runs(
    task_id: str,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
):
    with get_db() as db:
        exists = db.execute(
            "SELECT 1 FROM scheduled_tasks WHERE id=?", (task_id,)
        ).fetchone()
        if not exists:
            raise HTTPException(404, "Task not found")
        rows = db.execute(
            "SELECT id,task_id,started_at,finished_at,status,output"
            " FROM task_runs WHERE task_id=? ORDER BY started_at DESC LIMIT ?",
            (task_id, limit),
        ).fetchall()
    return [_run_row(r) for r in rows]


@router.get("/api/schedule/runs/recent")
def recent_runs(
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
    since: float | None = None,
):
    """Returns most-recent runs across all tasks (for the notification badge)."""
    with get_db() as db:
        if since is not None:
            rows = db.execute(
                "SELECT id,task_id,started_at,finished_at,status,output"
                " FROM task_runs WHERE finished_at>? ORDER BY finished_at DESC LIMIT ?",
                (since, limit),
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT id,task_id,started_at,finished_at,status,output"
                " FROM task_runs ORDER BY started_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
    return [_run_row(r) for r in rows]
