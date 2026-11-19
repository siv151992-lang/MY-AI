"""Usage metering: every model call is recorded (who, which feature, model, tokens, latency, errors).

Attribution uses context variables, so a feature only has to call `scope(user_id, feature)` once before it starts
its work (asyncio tasks created afterwards inherit the context)."""
from __future__ import annotations

import asyncio
import contextvars
import logging
import time
from contextlib import contextmanager
from typing import AsyncIterator

log = logging.getLogger("aria.usage")

_ctx: contextvars.ContextVar[dict] = contextvars.ContextVar("aria_usage", default={})
_pending: set[asyncio.Task] = set()

FEATURES = ("chat", "workspace", "agent", "research", "team", "skill", "custom_agent", "automation", "documents",
            "memory", "router", "other")


def set_scope(user_id: str | None, feature: str) -> contextvars.Token:
    return _ctx.set({"user_id": str(user_id) if user_id else None, "feature": feature})


@contextmanager
def scope(user_id: str | None, feature: str):
    token = set_scope(user_id, feature)
    try:
        yield
    finally:
        _ctx.reset(token)


def current() -> dict:
    return _ctx.get()


def _model_id(provider: str, model: str) -> str:
    from ..config import get_settings
    try:
        for m in get_settings().model_catalog():
            if m["provider"] == provider and m["model"] == model:
                return m["id"]
    except Exception:  # noqa: BLE001
        pass
    return f"{provider}:{model}"


async def _insert(row: dict) -> None:
    from .. import db
    try:
        await db.pool().execute(
            "INSERT INTO llm_calls (user_id, feature, provider, model_id, input_tokens, output_tokens, latency_ms, "
            "ttft_ms, ok, error) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)",
            row["user_id"], row["feature"], row["provider"], row["model_id"], row["input_tokens"],
            row["output_tokens"], row["latency_ms"], row["ttft_ms"], row["ok"], row["error"][:300])
    except Exception:  # noqa: BLE001 - metering must never break a model call (e.g. database not connected in tests)
        log.debug("could not record llm call", exc_info=True)


def record(row: dict) -> None:
    try:
        t = asyncio.get_running_loop().create_task(_insert(row))
    except RuntimeError:
        return
    _pending.add(t)
    t.add_done_callback(_pending.discard)


async def flush() -> None:
    """Wait for pending inserts (tests, shutdown)."""
    if _pending:
        await asyncio.gather(*list(_pending), return_exceptions=True)


class MeteredProvider:
    """Wraps an LLM provider and records each streamed call. Everything else is delegated to the wrapped provider."""

    def __init__(self, inner):
        self.inner = inner
        self.name = inner.name

    def __getattr__(self, item):
        return getattr(self.inner, item)

    async def stream(self, *, model: str, **kw) -> AsyncIterator[dict]:
        ctx = current()
        t0 = time.monotonic()
        ttft = None
        usage = {"input_tokens": 0, "output_tokens": 0}
        ok, err = False, ""
        try:
            async for ev in self.inner.stream(model=model, **kw):
                if ttft is None and ev.get("type") in ("text", "tool_call", "thinking"):
                    ttft = int((time.monotonic() - t0) * 1000)
                if ev.get("type") == "done":
                    usage = ev.get("usage") or usage
                    ok = True
                yield ev
            ok = True
        except asyncio.CancelledError:
            err = "cancelled"
            raise
        except GeneratorExit:
            err = "closed by caller"
            ok = True  # the consumer stopped early (e.g. user pressed stop) - not a provider failure
            raise
        except Exception as e:  # noqa: BLE001
            err = f"{type(e).__name__}: {e}"
            raise
        finally:
            record({"user_id": ctx.get("user_id"), "feature": ctx.get("feature") or "other", "provider": self.name,
                    "model_id": _model_id(self.name, model), "input_tokens": int(usage.get("input_tokens") or 0),
                    "output_tokens": int(usage.get("output_tokens") or 0),
                    "latency_ms": int((time.monotonic() - t0) * 1000), "ttft_ms": ttft, "ok": ok and not err.startswith(("cancel",)),
                    "error": err})

    async def complete(self, *, model: str, system: str, messages: list[dict], max_tokens: int = 1024) -> str:
        out = []
        async for ev in self.stream(model=model, system=system, messages=messages, max_tokens=max_tokens, temperature=0.2):
            if ev["type"] == "text":
                out.append(ev["text"])
        return "".join(out)


# ------------------------------------------------------------------ analytics
def _prices() -> dict[str, tuple[float, float]]:
    """Optional USD prices per million tokens from models.json ("price_in"/"price_out"). Never guessed."""
    from ..config import get_settings
    out = {}
    for m in get_settings().model_catalog():
        if m.get("price_in") is not None and m.get("price_out") is not None:
            out[m["id"]] = (float(m["price_in"]), float(m["price_out"]))
    return out


async def summary(user_id: str | None, days: int = 30) -> dict:
    """Usage for one user (or everyone when user_id is None) over the last `days` days."""
    from .. import db
    days = max(1, min(int(days), 365))
    where, args = ("created_at > now() - make_interval(days => $1)", [days])
    if user_id:
        where += " AND user_id = $2"
        args.append(user_id)
    p = db.pool()
    tot = await p.fetchrow(
        f"SELECT count(*) calls, coalesce(sum(input_tokens),0) tin, coalesce(sum(output_tokens),0) tout, "
        f"count(*) FILTER (WHERE NOT ok) errors, "
        f"percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms) p50, "
        f"percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) p95, "
        f"percentile_cont(0.5) WITHIN GROUP (ORDER BY ttft_ms) ttft50 FROM llm_calls WHERE {where}", *args)
    by_model = await p.fetch(
        f"SELECT model_id, provider, count(*) calls, sum(input_tokens) tin, sum(output_tokens) tout, "
        f"count(*) FILTER (WHERE NOT ok) errors, percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms) p50, "
        f"percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) p95, "
        f"percentile_cont(0.5) WITHIN GROUP (ORDER BY ttft_ms) ttft50 "
        f"FROM llm_calls WHERE {where} GROUP BY 1,2 ORDER BY calls DESC", *args)
    by_feature = await p.fetch(
        f"SELECT feature, count(*) calls, sum(input_tokens + output_tokens) tokens, count(*) FILTER (WHERE NOT ok) errors "
        f"FROM llm_calls WHERE {where} GROUP BY 1 ORDER BY calls DESC", *args)
    by_day = await p.fetch(
        f"SELECT date_trunc('day', created_at)::date AS day, count(*) calls, sum(input_tokens) tin, "
        f"sum(output_tokens) tout, count(*) FILTER (WHERE NOT ok) errors FROM llm_calls WHERE {where} "
        f"GROUP BY 1 ORDER BY 1", *args)
    prices = _prices()

    def cost(model_id: str, tin: int, tout: int) -> float | None:
        if model_id not in prices:
            return None
        pin, pout = prices[model_id]
        return round(tin / 1e6 * pin + tout / 1e6 * pout, 4)

    models = []
    total_cost, cost_known = 0.0, True
    for r in by_model:
        c = cost(r["model_id"], r["tin"] or 0, r["tout"] or 0)
        if c is None:
            cost_known = False
        else:
            total_cost += c
        models.append({"model_id": r["model_id"], "provider": r["provider"], "calls": r["calls"],
                       "input_tokens": r["tin"] or 0, "output_tokens": r["tout"] or 0, "errors": r["errors"],
                       "error_rate": round(r["errors"] / r["calls"], 3) if r["calls"] else 0,
                       "p50_ms": _i(r["p50"]), "p95_ms": _i(r["p95"]), "ttft_p50_ms": _i(r["ttft50"]), "cost_usd": c})
    return {
        "days": days,
        "totals": {"calls": tot["calls"], "input_tokens": tot["tin"], "output_tokens": tot["tout"],
                   "errors": tot["errors"], "error_rate": round(tot["errors"] / tot["calls"], 3) if tot["calls"] else 0,
                   "p50_ms": _i(tot["p50"]), "p95_ms": _i(tot["p95"]), "ttft_p50_ms": _i(tot["ttft50"]),
                   "cost_usd": round(total_cost, 4) if models and cost_known else None,
                   "cost_note": None if (not models or cost_known) else
                   "Cost is shown only for models with price_in/price_out set in models.json."},
        "by_model": models,
        "by_feature": [{"feature": r["feature"], "calls": r["calls"], "tokens": r["tokens"] or 0, "errors": r["errors"]}
                       for r in by_feature],
        "by_day": [{"day": r["day"].isoformat(), "calls": r["calls"], "input_tokens": r["tin"] or 0,
                    "output_tokens": r["tout"] or 0, "errors": r["errors"]} for r in by_day],
    }


def _i(v) -> int | None:
    return None if v is None else int(v)


_health_cache: tuple[float, dict] = (0.0, {})


async def model_health(window_hours: int = 24) -> dict[str, dict]:
    """Recent reliability/speed per model (all users), cached for a minute. Used by the router."""
    global _health_cache
    from .. import db
    now = time.monotonic()
    if now - _health_cache[0] < 60:
        return _health_cache[1]
    try:
        rows = await db.pool().fetch(
            "SELECT model_id, count(*) calls, count(*) FILTER (WHERE NOT ok) errors, "
            "percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms) p50 FROM llm_calls "
            "WHERE created_at > now() - make_interval(hours => $1) AND error <> 'cancelled' GROUP BY 1", window_hours)
    except Exception:  # noqa: BLE001
        return {}
    data = {r["model_id"]: {"calls": r["calls"], "error_rate": r["errors"] / r["calls"] if r["calls"] else 0.0,
                            "p50_ms": _i(r["p50"])} for r in rows}
    _health_cache = (now, data)
    return data


def reset_health_cache() -> None:
    global _health_cache
    _health_cache = (0.0, {})
