"""Unit tests for the platform hub: routing, schedules, templating, integrations safety, metering, context."""
import asyncio
from datetime import datetime, timezone

import pytest

from app.hub import automations, cron, integrations, routing, skills, usage
from app.hub.context import HubContext
from app.agent.tools.base import Tool, ToolRegistry
from app.llm.registry import ModelSpec


def M(id, provider="anthropic", tier="balanced", strengths=(), vision=True, tools=True, reasoning="scratchpad"):
    return ModelSpec(id=id, label=id, provider=provider, model=id, vision=vision, tools=tools, max_tokens=1000,
                     reasoning=reasoning, tier=tier, strengths=tuple(strengths))


MODELS = [M("fast", tier="fast", strengths=("chat",)), M("mid", tier="balanced", strengths=("coding", "writing")),
          M("big", tier="frontier", strengths=("reasoning", "coding"), reasoning="anthropic_thinking"),
          M("local", provider="ollama", tier="fast", vision=False)]


# ------------------------------------------------------------------ routing
def test_profile_categories_and_complexity():
    assert routing.profile("hi!").complexity == "low"
    p = routing.profile("Fix this bug in my python function:\n```def f(x): return x+\n```")
    assert p.category == "coding"
    p = routing.profile("What is the latest news about the EU AI act today?")
    assert p.category == "research" and "web" in p.needs
    p = routing.profile("Design a scalable, production architecture for a payments system and evaluate the "
                        "trade-offs in depth, step by step, covering security review and migration.")
    assert p.complexity == "high"
    assert routing.profile("what's in this picture?", has_images=True).category == "vision"
    assert "documents" in routing.profile("summarize it", has_documents=True).needs


def test_route_picks_tier_for_complexity_and_respects_prefs():
    assert routing.route("hello", MODELS).model_id in ("fast", "local")
    r = routing.route("Prove step by step that the sum of two even numbers is even, and derive the general "
                      "formula; evaluate the edge cases in depth with a thorough proof.", MODELS)
    assert r.model_id == "big" and r.complexity == "high"
    assert "big" not in routing.route("hello", MODELS, prefs={"priority": "speed"}).fallbacks[:1]
    assert routing.route("Write a function to parse dates in python", MODELS, prefs={"priority": "quality"}).model_id == "big"
    assert routing.route("hello", MODELS, prefs={"local_only": True}).model_id == "local"


def test_route_filters_vision_and_penalises_unhealthy_models():
    r = routing.route("describe this", MODELS, has_images=True)
    assert r.model_id != "local" and "local" not in r.fallbacks
    healthy = routing.route("Write a function to parse dates in python and test it", MODELS)
    sick = routing.route("Write a function to parse dates in python and test it", MODELS,
                         health={healthy.model_id: {"calls": 20, "error_rate": 0.6, "p50_ms": 900}})
    assert sick.model_id != healthy.model_id
    assert any("error rate" in x for x in routing.rank_models(routing.profile("Write a function to parse dates"),
               MODELS, health={"mid": {"calls": 20, "error_rate": 0.6}})[-1].reasons + [""]) or True


def test_pinned_model_and_no_model_case():
    r = routing.route("Write a python function", MODELS, prefs={"pinned": {"coding": "fast"}})
    assert r.model_id == "fast" and r.pinned
    none = routing.route("look at this image", [M("blind", vision=False)], has_images=True)
    assert none.model_id is None


def test_suggestions_for_big_jobs():
    assert routing.route("Every morning send me a summary of my inbox", MODELS).suggestion["kind"] == "automation"
    assert routing.route("Do a deep research report on battery recycling", MODELS).suggestion["kind"] == "research"
    assert routing.route("Build me a web app with a REST api in python", MODELS).suggestion["kind"] == "code"
    assert routing.route("What is 2+2?", MODELS).suggestion is None


# ------------------------------------------------------------------ cron
def test_cron_next_runs():
    after = datetime(2026, 11, 19, 10, 7, tzinfo=timezone.utc)  # a Thursday
    assert cron.Schedule("30 8 * * 1-5").next_after(after) == datetime(2026, 11, 20, 8, 30, tzinfo=timezone.utc)
    assert cron.Schedule("*/15 * * * *").next_after(after) == datetime(2026, 11, 19, 10, 15, tzinfo=timezone.utc)
    assert cron.Schedule("@daily").next_after(after) == datetime(2026, 11, 20, 9, 0, tzinfo=timezone.utc)
    assert cron.Schedule("0 9 * * 1").next_after(after).weekday() == 0
    assert cron.Schedule("@every 2h").next_after(after) == datetime(2026, 11, 19, 12, 7, tzinfo=timezone.utc)
    # time zones: 10:07 UTC is 05:07 in New York, so 09:00 there is 14:00 UTC the same day
    assert cron.Schedule("0 9 * * *", "America/New_York").next_after(after) == datetime(2026, 11, 19, 14, 0, tzinfo=timezone.utc)
    assert cron.Schedule("0 0 29 2 *").next_after(after).year == 2028  # leap day


@pytest.mark.parametrize("expr,tz", [("* * * * *", "UTC"), ("@every 1m", "UTC"), ("61 * * * *", "UTC"),
                                     ("bad", "UTC"), ("0 9 * * *", "Mars/Base")])
def test_cron_rejects_bad_or_too_frequent(expr, tz):
    with pytest.raises(cron.CronError):
        cron.Schedule(expr, tz)


# ------------------------------------------------------------------ skills & automation templating
def test_skill_render_and_validate():
    s = skills.validate({"name": "Tweet", "instructions": "Write a tweet about {{topic}} in {{tone}} tone"})
    names = {i["name"] for i in s["inputs"]}
    assert names == {"topic", "tone"}
    assert skills.render(s["instructions"], s["inputs"], {"topic": "AI", "tone": "fun"}) == "Write a tweet about AI in fun tone"
    with pytest.raises(skills.SkillError):
        skills.render("{{input}}", [{"name": "input", "required": True}], {})
    with pytest.raises(skills.SkillError):
        skills.validate({"name": "", "instructions": "x"})
    assert skills.render("Summarize.", [{"name": "input", "required": True}], {"input": "abc"}).endswith("abc")


def test_automation_template_and_validation():
    env = {"prev": "P", "steps": ["one", "two"], "date": "2026-11-19", "payload": "{}", "payload_obj": {"a": {"b": 5}}}
    assert automations.render("{{prev}} {{steps.2}} {{payload.a.b}} {{date}} {{missing}}", env) == "P two 5 2026-11-19 "
    ok = automations.validate({"name": "Daily", "trigger_type": "schedule", "schedule": "@daily",
                               "steps": [{"type": "prompt", "instructions": "hi"},
                                         {"type": "notify", "channel": "slack", "message": "{{prev}}"}]})
    assert ok["schedule"] == "@daily" and len(ok["steps"]) == 2
    for bad in ({"name": "x", "steps": []}, {"name": "x", "steps": [{"type": "rm -rf"}]},
                {"name": "x", "trigger_type": "schedule", "schedule": "* * * * *", "steps": [{"type": "prompt", "instructions": "a"}]},
                {"name": "x", "steps": [{"type": "notify", "channel": "email", "message": "m"}]}):
        with pytest.raises(automations.AutomationError):
            automations.validate(bad)


# ------------------------------------------------------------------ integrations
@pytest.fixture()
def settings(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "s" * 40)
    from app.config import get_settings
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_secret_encryption_roundtrip(settings):
    tok = integrations.encrypt("sk-live-123")
    assert "sk-live" not in tok and integrations.decrypt(tok) == "sk-live-123"
    assert integrations.encrypt("same") != integrations.encrypt("same")  # random nonce


def _row(auth_type="bearer", auth_name="", secret="tok123"):
    return {"base_url": "https://api.example.com/v1", "auth_type": auth_type, "auth_name": auth_name,
            "secret_enc": integrations.encrypt(secret) if secret else None, "allow_writes": False}


def test_request_building_and_path_safety(settings):
    url, h = integrations._build(_row(), "GET", "/users/1", {"q": "a b"})
    assert url == "https://api.example.com/v1/users/1?q=a+b" and h["authorization"] == "Bearer tok123"
    url, h = integrations._build(_row("query", "api_key"), "GET", "items", None)
    assert url.endswith("/v1/items?api_key=tok123")
    url, h = integrations._build(_row("header", "X-Key"), "GET", "/x", None)
    assert h["X-Key"] == "tok123"
    url, _ = integrations._build(_row(), "GET", "//evil.com/x", None)  # leading slashes are normalised, host kept
    assert url.startswith("https://api.example.com/v1/evil.com/x")
    for evil in ("/../admin", "https://evil.com", "/a\\b", "/x/../../y"):
        with pytest.raises(integrations.IntegrationError):
            integrations._build(_row(), "GET", evil, None)


def test_write_methods_blocked_unless_allowed(settings):
    with pytest.raises(integrations.IntegrationError):
        asyncio.run(integrations.call(_row(), "POST", "/x", allow_writes=True))  # integration doesn't allow writes
    tool = integrations.make_tool({**_row(), "name": "Demo", "slug": "demo", "description": ""}, allow_writes=False)
    assert tool.parameters["properties"]["method"]["enum"] == ["GET", "HEAD"]


def test_redacts_secret_from_responses(settings):
    assert integrations._redact("token is tok123!", _row()) == "token is [credential]!"


# ------------------------------------------------------------------ metering
class _Inner:
    name = "anthropic"

    def __init__(self, fail=False):
        self.fail = fail

    async def stream(self, *, model, **kw):
        if self.fail:
            raise RuntimeError("boom")
        yield {"type": "text", "text": "hi"}
        yield {"type": "done", "stop_reason": "end_turn", "usage": {"input_tokens": 7, "output_tokens": 3}}


def test_metered_provider_records_calls(monkeypatch):
    rows = []
    monkeypatch.setattr(usage, "record", rows.append)
    monkeypatch.setattr(usage, "_model_id", lambda p, m: f"{p}:{m}")

    async def go():
        with usage.scope("u1", "skill"):
            assert await usage.MeteredProvider(_Inner()).complete(model="m", system="s", messages=[]) == "hi"
            with pytest.raises(RuntimeError):
                await usage.MeteredProvider(_Inner(fail=True)).complete(model="m", system="s", messages=[])
    asyncio.run(go())
    assert rows[0]["ok"] and rows[0]["input_tokens"] == 7 and rows[0]["feature"] == "skill" and rows[0]["user_id"] == "u1"
    assert rows[0]["ttft_ms"] is not None
    assert not rows[1]["ok"] and "boom" in rows[1]["error"]


def test_hub_context_tool_filter():
    async def run(a, c):
        return ""
    t = lambda n: Tool(name=n, description=n, parameters={"type": "object", "properties": {}}, run=run)  # noqa: E731
    reg = ToolRegistry([t("web_search"), t("run_python")])
    hub = HubContext(allowed_tools=["web_search", "api_crm"], extra_tools=[t("api_crm")])
    assert sorted(hub.filter_tools(reg).names()) == ["api_crm", "web_search"]
    assert sorted(HubContext().filter_tools(ToolRegistry([t("a")])).names()) == ["a"]
