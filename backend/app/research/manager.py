"""Starts, tracks and cancels research runs (one asyncio task per run), publishing progress events."""
from __future__ import annotations

import asyncio
import logging

from ..autonomy.bus import EventBus
from . import store
from .engine import DEPTHS, Researcher, ResearchFailed
from .web import fetch_page, make_search

log = logging.getLogger("aria.research")


class ResearchUnavailable(Exception):
    pass


class TooManyRuns(Exception):
    pass


class ResearchManager:
    def __init__(self, max_per_user: int = 2):
        self.bus = EventBus(store.EventStore())
        self.tasks: dict[str, asyncio.Task] = {}
        self.owners: dict[str, str] = {}
        self.max_per_user = max_per_user
        # Injection points (tests replace these)
        self.search_factory = lambda: _settings_search()
        self.fetch = fetch_page
        self.models = _models

    def available(self) -> tuple[bool, str]:
        if self.search_factory() is None:
            return False, ("Deep research needs a web search provider. Set TAVILY_API_KEY or BRAVE_API_KEY on the "
                           "server; without one, Aria cannot search the web and will not pretend to.")
        try:
            self.models(None)
        except RuntimeError as e:
            return False, str(e)
        return True, ""

    def is_active(self, run_id: str) -> bool:
        t = self.tasks.get(run_id)
        return bool(t and not t.done())

    async def recover_after_restart(self) -> None:
        n = await store.mark_interrupted()
        if n:
            log.warning("marked %d unfinished research run(s) as failed after restart", n)

    async def start(self, user_id: str, question: str, depth: str, model_id: str | None) -> dict:
        ok, why = self.available()
        if not ok:
            raise ResearchUnavailable(why)
        if sum(1 for r, t in self.tasks.items() if self.owners.get(r) == user_id and not t.done()) >= self.max_per_user:
            raise TooManyRuns(f"You already have {self.max_per_user} research runs in progress.")
        run = await store.create_run(user_id, question.strip(), depth if depth in DEPTHS else "standard", model_id)
        await self.bus.publish(run["id"], "created", {"question": run["question"], "depth": run["depth"]})
        self.owners[run["id"]] = user_id
        from ..hub import usage
        with usage.scope(user_id, "research"):
            self.tasks[run["id"]] = asyncio.create_task(self._run(run))
        return run

    async def _run(self, run: dict) -> None:
        rid = run["id"]

        async def publish(type_: str, data: dict) -> None:
            await self.bus.publish(rid, type_, data)
        try:
            (wl, wm), (ul, um) = self.models(run.get("model_id"))
            r = Researcher(run=run, writer_llm=wl, writer_model=wm, utility_llm=ul, utility_model=um,
                           search=self.search_factory(), fetch=self.fetch, publish=publish)
            await r.execute()
            await store.update_run(rid, status="completed")
            await publish("status", {"status": "completed", "final": True})
        except ResearchFailed as e:
            await store.update_run(rid, status="failed", error=str(e))
            await publish("status", {"status": "failed", "error": str(e), "final": True})
        except asyncio.CancelledError:
            await store.update_run(rid, status="cancelled", error="Cancelled by you.")
            await publish("status", {"status": "cancelled", "final": True})
        except Exception as e:  # noqa: BLE001
            log.exception("research run %s crashed", rid)
            msg = f"The research run stopped because of an internal error ({type(e).__name__}). No report was produced."
            await store.update_run(rid, status="failed", error=msg)
            await publish("status", {"status": "failed", "error": msg, "final": True})
        finally:
            self.tasks.pop(rid, None)

    def cancel(self, run_id: str) -> bool:
        t = self.tasks.get(run_id)
        if not t or t.done():
            return False
        t.cancel()
        return True


def _settings_search():
    from ..config import get_settings
    s = get_settings()
    return make_search(s.tavily_api_key, s.brave_api_key)


def _models(model_id: str | None):
    from ..llm import registry
    spec = registry.get_model(model_id)
    uspec = registry.utility_model()
    return (registry.provider_for(spec), spec.model), (registry.provider_for(uspec), uspec.model)


_manager: ResearchManager | None = None


def get_manager() -> ResearchManager:
    global _manager
    if _manager is None:
        _manager = ResearchManager()
    return _manager
