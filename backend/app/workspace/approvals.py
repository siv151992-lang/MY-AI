"""Approval gate: destructive or publishing actions requested by the AI wait for the user's decision."""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Awaitable, Callable

Emit = Callable[[dict], Awaitable[None] | None]


@dataclass
class Approval:
    id: str
    workspace_id: str
    user_id: str
    kind: str                 # delete | discard | restore | commit | command_deletes
    summary: str
    details: dict = field(default_factory=dict)
    status: str = "pending"   # pending | approved | denied | expired
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def public(self) -> dict:
        d = asdict(self)
        d.pop("user_id")
        return d


class ApprovalGate:
    def __init__(self, timeout_s: float = 600):
        self.timeout_s = timeout_s
        self._pending: dict[str, tuple[Approval, asyncio.Future]] = {}

    async def request(self, *, workspace_id: str, user_id: str, kind: str, summary: str, details: dict,
                      emit: Emit | None) -> bool:
        a = Approval(str(uuid.uuid4()), workspace_id, user_id, kind, summary, details)
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[a.id] = (a, fut)
        await _call(emit, {"type": "approval_required", "approval": a.public()})
        try:
            ok = await asyncio.wait_for(asyncio.shield(fut), timeout=self.timeout_s)
        except asyncio.TimeoutError:
            a.status = "expired"
            ok = False
        finally:
            self._pending.pop(a.id, None)
        if a.status == "pending":
            a.status = "approved" if ok else "denied"
        await _call(emit, {"type": "approval_decided", "id": a.id, "status": a.status})
        return ok

    def decide(self, user_id: str, approval_id: str, approved: bool) -> Approval | None:
        item = self._pending.get(approval_id)
        if not item or item[0].user_id != user_id or item[1].done():
            return None
        a, fut = item
        a.status = "approved" if approved else "denied"
        fut.set_result(approved)
        return a

    def pending(self, workspace_id: str, user_id: str) -> list[dict]:
        return [a.public() for a, f in self._pending.values()
                if a.workspace_id == workspace_id and a.user_id == user_id and not f.done()]

    def cancel_workspace(self, workspace_id: str) -> None:
        for a, f in list(self._pending.values()):
            if a.workspace_id == workspace_id and not f.done():
                a.status = "denied"
                f.set_result(False)


async def _call(emit: Emit | None, ev: dict) -> None:
    if emit is None:
        return
    r = emit(ev)
    if asyncio.iscoroutine(r):
        await r


_gate: ApprovalGate | None = None


def gate() -> ApprovalGate:
    global _gate
    if _gate is None:
        from ..config import get_settings
        _gate = ApprovalGate(get_settings().workspace_approval_timeout_min * 60)
    return _gate
