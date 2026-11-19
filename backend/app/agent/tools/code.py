"""Coding engine tool: run Python in the isolated sandbox service."""
from __future__ import annotations

from .base import Tool, ToolContext


def format_run_result(r: dict) -> str:
    parts = [f"exit_code: {r.get('exit_code')}" + ("  (timed out)" if r.get("timed_out") else "")]
    if r.get("stdout"):
        parts.append("stdout:\n" + r["stdout"])
    if r.get("stderr"):
        parts.append("stderr:\n" + r["stderr"])
    if not r.get("stdout") and not r.get("stderr"):
        parts.append("(no output — print() the values you want to see)")
    return "\n".join(parts)


def make_code_tool(sandbox_url: str, token: str | None) -> Tool:
    import httpx

    async def run_python(args: dict, ctx: ToolContext) -> str:
        headers = {"authorization": f"Bearer {token}"} if token else {}
        async with httpx.AsyncClient(timeout=70) as c:
            r = await c.post(f"{sandbox_url.rstrip('/')}/run", headers=headers,
                             json={"code": args["code"], "timeout": min(int(args.get("timeout", 30)), 60)})
            r.raise_for_status()
            return format_run_result(r.json())

    return Tool(
        name="run_python",
        description="Execute Python 3 code in an isolated sandbox (no network, no access to the user's data, "
                    "standard library + numpy/pandas/matplotlib if installed). Use for math, data analysis, and to "
                    "test code. Print results explicitly. Each call is a fresh process.",
        parameters={"type": "object", "properties": {
            "code": {"type": "string", "description": "Complete Python program"},
            "timeout": {"type": "integer", "description": "Seconds, max 60"}}, "required": ["code"]},
        run=run_python, timeout=75)
