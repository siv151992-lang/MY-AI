"""Tool engine: a registry of typed tools the model may call."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable


@dataclass
class ToolContext:
    user_id: str
    conversation_id: str
    file_ids: list[str] = field(default_factory=list)


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict  # JSON Schema
    run: Callable[[dict, ToolContext], Awaitable[str]]
    timeout: float = 60.0

    def spec(self) -> dict:
        return {"name": self.name, "description": self.description, "parameters": self.parameters}


class ToolRegistry:
    def __init__(self, tools: list[Tool] | None = None):
        self._tools: dict[str, Tool] = {t.name: t for t in tools or []}

    def add(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def specs(self) -> list[dict]:
        return [t.spec() for t in self._tools.values()]

    def names(self) -> list[str]:
        return list(self._tools)

    async def execute(self, name: str, args: dict, ctx: ToolContext, max_chars: int = 12_000) -> tuple[str, bool]:
        """Returns (output, is_error). Never raises: errors are reported back to the model so it can recover."""
        tool = self._tools.get(name)
        if tool is None:
            return f"Unknown tool '{name}'. Available: {', '.join(self._tools)}", True
        missing = [k for k in tool.parameters.get("required", []) if k not in args]
        if missing:
            return f"Missing required argument(s): {', '.join(missing)}", True
        try:
            out = await asyncio.wait_for(tool.run(args, ctx), timeout=tool.timeout)
        except asyncio.TimeoutError:
            return f"Tool '{name}' timed out after {tool.timeout:.0f}s", True
        except Exception as e:  # noqa: BLE001 - surfaced to the model
            return f"Tool '{name}' failed: {type(e).__name__}: {e}", True
        if len(out) > max_chars:
            out = out[:max_chars] + f"\n…[truncated {len(out) - max_chars} chars]"
        return out, False
