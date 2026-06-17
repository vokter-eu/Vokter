"""
Background scheduler for autonomous tasks.

Checks every 60 seconds for tasks whose next_run is due, fires them
through the existing planner pipeline, and stores the result in task_runs.
Never raises — a scheduler crash would silently stop all future tasks.
"""
import asyncio
import json
import logging
import time
import uuid

from db import get_db

log = logging.getLogger("vokter.scheduler")


async def _run_task(task_id: str, goal: str, interval_seconds: int) -> None:
    run_id = str(uuid.uuid4())
    started_at = time.time()

    with get_db() as db:
        db.execute(
            "INSERT INTO task_runs(id, task_id, started_at, status, output)"
            " VALUES(?,?,?,'running','')",
            (run_id, task_id, started_at),
        )
        # Advance next_run immediately so a slow task doesn't re-trigger.
        db.execute(
            "UPDATE scheduled_tasks SET last_run=?, next_run=? WHERE id=?",
            (started_at, started_at + interval_seconds, task_id),
        )
        db.commit()

    answer = ""
    status = "done"
    try:
        from planner import _execute  # local import — avoids circular at module load
        async for sse_line in _execute(goal):
            if not sse_line.startswith("data: "):
                continue
            try:
                ev = json.loads(sse_line[6:])
            except json.JSONDecodeError:
                continue
            if ev.get("type") == "done":
                answer = ev.get("answer", "")
            elif ev.get("type") == "error":
                answer = ev.get("text", "")
                status = "error"
    except Exception as exc:
        log.exception("Task %s run %s failed", task_id, run_id)
        answer = str(exc)
        status = "error"

    with get_db() as db:
        db.execute(
            "UPDATE task_runs SET finished_at=?, status=?, output=? WHERE id=?",
            (time.time(), status, answer, run_id),
        )
        db.commit()

    log.info("Task %s run %s finished: %s", task_id, run_id, status)


async def _tick() -> None:
    now = time.time()
    with get_db() as db:
        rows = db.execute(
            "SELECT id, goal, interval_seconds FROM scheduled_tasks"
            " WHERE enabled=1 AND next_run<=?",
            (now,),
        ).fetchall()
    for task_id, goal, interval_seconds in rows:
        asyncio.create_task(_run_task(task_id, goal, interval_seconds))


async def scheduler_loop() -> None:
    """Runs forever. Call once at app startup via asyncio.create_task."""
    log.info("Scheduler started")
    while True:
        try:
            await _tick()
        except Exception:
            log.exception("Scheduler tick failed")
        await asyncio.sleep(60)
