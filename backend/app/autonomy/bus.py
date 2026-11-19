"""Event bus: every run event is persisted (for replay/history) and pushed live to subscribers (SSE)."""
from __future__ import annotations

import asyncio
from collections import defaultdict


class EventBus:
    def __init__(self, store):
        self.store = store
        self._subs: dict[str, set[asyncio.Queue]] = defaultdict(set)

    async def publish(self, run_id: str, type_: str, data: dict | None = None) -> dict:
        ev = await self.store.add_event(run_id, type_, data or {})
        for q in list(self._subs.get(run_id, ())):
            q.put_nowait(ev)
        return ev

    def subscribe(self, run_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subs[run_id].add(q)
        return q

    def unsubscribe(self, run_id: str, q: asyncio.Queue) -> None:
        self._subs[run_id].discard(q)
        if not self._subs[run_id]:
            self._subs.pop(run_id, None)
