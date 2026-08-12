"""
Vokter MCP server — Phase 6 interoperability.

Wraps Vokter's existing REST tools as an MCP server so any MCP-capable
host (Claude Desktop, Cursor, Continue, etc.) can use them directly.

Usage — stdio (Claude Desktop):
  docker exec -i vokter-app python mcp_server.py

Usage — HTTP/SSE (web clients):
  docker exec -it vokter-app python mcp_server.py --transport sse

Claude Desktop config  (~/.claude/claude_desktop_config.json):
  {
    "mcpServers": {
      "vokter": {
        "command": "docker",
        "args": ["exec", "-i", "vokter-app", "python", "mcp_server.py"]
      }
    }
  }

Architecture note: this file is a protocol adapter only.
All business logic stays in the FastAPI app it calls via HTTP.
"""
import json
import sys

import httpx
from mcp.server.fastmcp import FastMCP

from auth import admin_headers

_BASE    = "http://localhost:8080"
_TIMEOUT = httpx.Timeout(connect=5.0, read=300.0, write=10.0, pool=5.0)
# This adapter authenticates to the local admin API (H1 gate) with the admin
# token, present in the container env.
_HEADERS = admin_headers()

mcp = FastMCP(
    "Vokter",
    instructions=(
        "Vokter is a sovereign local AI agent running on the user's machine. "
        "It stores and queries the user's private documents and emails locally. "
        "Use 'ask' to query knowledge, 'browse' to learn from a web page, and "
        "'plan' for multi-step research goals."
    ),
)


async def _get(path: str) -> dict:
    async with httpx.AsyncClient(timeout=_TIMEOUT, headers=_HEADERS) as client:
        r = await client.get(f"{_BASE}{path}")
        r.raise_for_status()
        return r.json()


async def _post(path: str, body: dict) -> dict:
    async with httpx.AsyncClient(timeout=_TIMEOUT, headers=_HEADERS) as client:
        r = await client.post(f"{_BASE}{path}", json=body)
        r.raise_for_status()
        return r.json()


@mcp.tool()
async def browse(url: str) -> str:
    """Fetch a public web page and store it in Vokter's local memory.
    The page can then be queried with the 'ask' tool."""
    d = await _post("/api/browse", {"url": url})
    return f"Stored {d['chunks']} chunks from {d['doc']}."


@mcp.tool()
async def ask(question: str, conversation_id: str = "") -> str:
    """Query Vokter's local knowledge base (ingested documents and web pages).

    Pass the same conversation_id across calls to maintain conversation context.
    Returns the answer plus the source documents it was drawn from."""
    body: dict = {"question": question}
    if conversation_id:
        body["conversation_id"] = conversation_id
    d = await _post("/api/ask", body)
    sources = ", ".join(d.get("sources") or [])
    answer  = d["answer"]
    if sources:
        answer += f"\n\nSources: {sources}"
    return answer


@mcp.tool()
async def plan(goal: str) -> str:
    """Give Vokter a multi-step research goal.

    Vokter will automatically browse relevant pages, query its memory,
    and synthesise a final answer. Best for research tasks that need
    several information sources. Returns the synthesised answer."""
    answer = ""
    async with httpx.AsyncClient(timeout=_TIMEOUT, headers=_HEADERS) as client:
        async with client.stream("POST", f"{_BASE}/api/plan", json={"goal": goal}) as r:
            r.raise_for_status()
            buf = ""
            async for chunk in r.aiter_text():
                buf += chunk
                lines = buf.split("\n")
                buf   = lines.pop()
                for line in lines:
                    if not line.startswith("data: "):
                        continue
                    try:
                        ev = json.loads(line[6:])
                    except json.JSONDecodeError:
                        continue
                    if ev.get("type") == "done":
                        answer = ev.get("answer", "")
    return answer or "Plan completed — no answer synthesised."


@mcp.tool()
async def schedule_list() -> str:
    """List all scheduled recurring tasks and their status."""
    tasks = await _get("/api/schedule")
    if not tasks:
        return "No scheduled tasks."
    lines = []
    for t in tasks:
        status   = "enabled" if t["enabled"] else "disabled"
        mins     = t["interval_seconds"] // 60
        interval = f"{mins}m" if mins < 60 else f"{mins // 60}h"
        lines.append(f"- [{status}] {t['name']} every {interval} — {t['goal']}")
    return "\n".join(lines)


@mcp.tool()
async def schedule_create(name: str, goal: str, interval: str) -> str:
    """Create a recurring scheduled task.

    interval: '30m', '2h', '1d' (minimum 5m).
    Vokter will run the goal automatically on this schedule."""
    d = await _post("/api/schedule", {"name": name, "goal": goal, "interval": interval})
    return f"Created task '{d['name']}' (ID: {d['id']}) — runs every {interval}."


if __name__ == "__main__":
    transport = "stdio"
    if "--transport" in sys.argv:
        idx = sys.argv.index("--transport")
        if idx + 1 < len(sys.argv):
            transport = sys.argv[idx + 1]
    mcp.run(transport=transport)
