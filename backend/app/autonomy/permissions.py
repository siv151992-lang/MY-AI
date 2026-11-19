"""Permission gate: sensitive external actions wait for the user's explicit decision."""
from __future__ import annotations

import asyncio

from .models import Approval, now


class ApprovalTimeout(Exception):
    pass


class PermissionGate:
    def __init__(self, store, bus, timeout_s: float):
        self.store, self.bus, self.timeout_s = store, bus, timeout_s
        self._waiting: dict[str, asyncio.Future] = {}
        self._approvals: dict[str, Approval] = {}

    async def request(self, run_id: str, step_idx: int, tool: str, args: dict, summary: str) -> bool:
        """Blocks until the user approves/denies (or the request times out). Returns True if approved."""
        a = Approval(id="", run_id=run_id, step_idx=step_idx, tool=tool, args=args, summary=summary)
        await self.store.add_approval(a)
        if not a.id:
            a.id = f"{run_id}:{len(self._approvals) + 1}"
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._waiting[a.id], self._approvals[a.id] = fut, a
        await self.bus.publish(run_id, "approval_required", a.to_dict())
        try:
            approved = await asyncio.wait_for(fut, timeout=self.timeout_s)
        except asyncio.TimeoutError:
            a.status, a.decided_at = "expired", now()
            await self.store.update_approval(a)
            await self.bus.publish(run_id, "approval_decided", {"id": a.id, "status": "expired"})
            raise ApprovalTimeout(f"No decision on '{summary[:80]}' within {self.timeout_s / 60:.0f} minutes")
        finally:
            self._waiting.pop(a.id, None)
        return approved

    async def decide(self, run_id: str, approval_id: str, approved: bool) -> bool:
        fut = self._waiting.get(approval_id)
        a = self._approvals.get(approval_id)
        if not fut or fut.done() or not a or a.run_id != run_id:
            return False
        a.status, a.decided_at = ("approved" if approved else "denied"), now()
        await self.store.update_approval(a)
        await self.bus.publish(run_id, "approval_decided", {"id": a.id, "status": a.status})
        fut.set_result(approved)
        return True

    def cancel_run(self, run_id: str) -> None:
        for aid, fut in list(self._waiting.items()):
            if self._approvals[aid].run_id == run_id and not fut.done():
                fut.cancel()

    def pending_for(self, run_id: str) -> list[Approval]:
        return [self._approvals[a] for a, f in self._waiting.items()
                if self._approvals[a].run_id == run_id and not f.done()]
