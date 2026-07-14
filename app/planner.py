"""
Task planner — Phase 2.

Accepts a natural-language goal, asks Ollama to decompose it into steps
(browse / ask), executes them in order using existing Vokter tools, then
synthesises a final answer via RAG.

Progress is streamed back to the client as Server-Sent Events so the UI
can show each step as it happens.
"""
import json
from typing import AsyncGenerator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agent_config import get_config
from browser import BrowseRequest, browse as do_browse
from engine import ENGINE, ChatRequest
from rag import retrieve

router = APIRouter()

_MAX_STEPS = 6

_PLAN_SYSTEM = (
    "You are a task planner for a local AI agent called Vokter. "
    "Given a goal, output a JSON execution plan.\n\n"
    "Available tools:\n"
    '  "browse": fetch and memorize a web page (args: {"url": "https://..."})\n'
    '  "ask":    query the agent\'s local document and web memory (args: {"question": "..."})\n\n'
    f"Rules: maximum {_MAX_STEPS} steps. Output ONLY valid JSON — no text outside it.\n\n"
    "Format:\n"
    "{\n"
    '  "steps": [\n'
    '    {"tool": "browse", "args": {"url": "https://..."}, "reason": "brief reason"},\n'
    '    {"tool": "ask",    "args": {"question": "..."},    "reason": "brief reason"}\n'
    "  ],\n"
    '  "summary_question": "final question to synthesise all findings into the answer"\n'
    "}"
)


class PlanRequest(BaseModel):
    goal: str


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data)}\n\n"


async def _make_plan(goal: str) -> dict:
    content = await ENGINE.chat(ChatRequest(
        messages=[
            {"role": "system", "content": _PLAN_SYSTEM},
            {"role": "user",   "content": f"Goal: {goal}"},
        ],
        json_mode=True, timeout=60,
    ))
    try:
        return json.loads(content)
    except json.JSONDecodeError as exc:
        raise HTTPException(500, f"Planner produced invalid JSON: {exc}")


async def _execute(goal: str) -> AsyncGenerator[str, None]:
    yield _sse({"type": "status", "text": "Planning…"})

    try:
        plan = await _make_plan(goal)
    except HTTPException as exc:
        yield _sse({"type": "error", "text": exc.detail})
        return

    steps = plan.get("steps") or []
    if not isinstance(steps, list) or not steps:
        yield _sse({"type": "error", "text": "Planner returned no steps."})
        return
    steps = steps[:_MAX_STEPS]

    valid_steps = [s for s in steps if isinstance(s, dict)]
    if not valid_steps:
        yield _sse({"type": "error", "text": "Planner returned no valid steps."})
        return

    yield _sse({
        "type": "plan",
        "steps": [{"tool": s.get("tool", "?"), "reason": s.get("reason", "")} for s in valid_steps],
    })

    for i, step in enumerate(valid_steps):
        tool   = step.get("tool", "")
        args   = step.get("args") or {}
        reason = step.get("reason", "")

        yield _sse({"type": "step_start", "index": i, "tool": tool, "reason": reason})

        try:
            if tool == "browse":
                url = (args.get("url") or "").strip()
                if not url:
                    yield _sse({"type": "step_error", "index": i, "text": "Missing URL."})
                    continue
                result = await do_browse(BrowseRequest(url=url))
                yield _sse({
                    "type": "step_done", "index": i,
                    "text": f"Browsed {result['doc']} — {result['chunks']} chunks stored.",
                })

            elif tool == "ask":
                question = (args.get("question") or "").strip()
                if not question:
                    yield _sse({"type": "step_error", "index": i, "text": "Missing question."})
                    continue
                cfg = get_config()
                scored = await retrieve(question, top_k=int(cfg.get("rag_chunks") or 4))
                yield _sse({
                    "type": "step_done", "index": i,
                    "text": f'Queried memory: {len(scored)} relevant chunk(s) for "{question}".',
                })

            else:
                yield _sse({"type": "step_error", "index": i, "text": f"Unknown tool '{tool}'."})

        except HTTPException as exc:
            yield _sse({"type": "step_error", "index": i, "text": exc.detail})
        except Exception as exc:
            yield _sse({"type": "step_error", "index": i, "text": str(exc)})

    # Final synthesis via RAG
    yield _sse({"type": "status", "text": "Synthesising answer…"})
    summary_q = (plan.get("summary_question") or goal).strip()
    cfg = get_config()
    scored = await retrieve(summary_q, top_k=int(cfg.get("rag_chunks") or 4))

    if not scored:
        yield _sse({"type": "done", "answer": "I couldn't find enough information to answer the goal."})
        return

    context = "\n\n---\n\n".join(f"[{doc}]\n{content}" for _, doc, content in scored)
    try:
        answer = await ENGINE.chat(ChatRequest(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are Vokter, the user's personal AI guardian. "
                        "Answer the goal using ONLY the provided context. "
                        "Be direct and concise. Answer in the language of the goal."
                    ),
                },
                {"role": "user", "content": f"Context:\n{context}\n\nGoal: {goal}"},
            ],
            context_size=8192, timeout=120,
        ))
    except HTTPException as exc:
        yield _sse({"type": "error", "text": exc.detail})
        return
    yield _sse({"type": "done", "answer": answer})


@router.post("/api/plan")
async def plan(req: PlanRequest):
    if not req.goal.strip():
        raise HTTPException(400, "goal is empty")
    return StreamingResponse(
        _execute(req.goal),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
