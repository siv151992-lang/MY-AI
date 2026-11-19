"""Tool engine: a registry of typed tools the model may call."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Union


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
    # Permission level: True / a predicate on args => the action has external side effects and needs the
    # user's explicit approval before it runs (autonomous agent). False => safe (read-only or sandboxed).
    requires_approval: Union[bool, Callable[[dict], bool]] = False
    describe: Callable[[dict], str] | None = None  # human-readable summary shown on the approval card

    def spec(self) -> dict:
        return {"name": self.name, "description": self.description, "parameters": self.parameters}

    def needs_approval(self, args: dict) -> bool:
        r = self.requires_approval
        return bool(r(args)) if callable(r) else bool(r)

    def summary(self, args: dict) -> str:
        if self.describe:
            try:
                return self.describe(args)
            except Exception:  # noqa: BLE001
                pass
        return f"{self.name}({', '.join(f'{k}={str(v)[:60]!r}' for k, v in args.items())})"


class ToolRegistry:
    def __init__(self, tools: list[Tool] | None = None):
        self._tools: dict[str, Tool] = {t.name: t for t in tools or []}

    def add(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def specs(self) -> list[dict]:
        return [t.spec() for t in self._tools.values()]

    def names(self) -> list[str]:
        return list(self._tools)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def catalog(self) -> list[dict]:
        """Tool list for planners: name, description and whether it needs approval."""
        return [{"name": t.name, "description": t.description.split(". ")[0],
                 "needs_approval": t.requires_approval is True or callable(t.requires_approval)}
                for t in self._tools.values()]

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
