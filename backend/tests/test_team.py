"""Multi-agent orchestration: planning rules, scheduling/parallelism, specialist grounding, conflicts, verification."""
import asyncio
import json

import pytest

from app.agent.tools.base import ToolContext, ToolRegistry
from app.team import orchestrator as orch_mod
from app.team import plan as planmod
from app.team.browser import BrowserSession, extract_links
from app.team.loop import ground_sources, run_specialist
from app.team.specialists import SPECS, capabilities, registry_for

from .team_fakes import TeamLLM, recording_pool

AGENTS = set(SPECS)


def test_plan_validation_repairs_or_rejects():
    raw = json.dumps({"tasks": [
        {"id": "a", "agent": "Research Agent", "instruction": "find x"},
        {"id": "b", "agent": "data analyst", "instruction": "analyse x", "depends_on": ["a", "c", "nope"]},
        {"id": "c", "agent": "coding", "instruction": "code x", "depends_on": ["b"]},   # cycle b <-> c
        {"id": "d", "agent": "wizard", "instruction": "magic"}]})
    tasks, warnings = planmod.parse_plan(raw, AGENTS)
    assert [(t.id, t.agent) for t in tasks] == [("T1", "research"), ("T2", "data_analyst"), ("T3", "coding")]
    assert any("unknown agent 'wizard'" in w for w in warnings) and any("circular" in w for w in warnings)
    assert any("unknown task 'nope'" in w for w in warnings)
    planmod.waves(tasks)  # no cycle left
    with pytest.raises(planmod.PlanError):
        planmod.parse_plan('{"tasks": []}', AGENTS)


def test_waves_group_independent_tasks():
    tasks, _ = planmod.parse_plan(json.dumps({"tasks": [
        {"agent": "research", "instruction": "research topic a"}, {"agent": "coding", "instruction": "analyse table b"},
        {"agent": "writing", "instruction": "write brief c", "depends_on": ["T1", "T2"]}]}), AGENTS)
    assert planmod.waves(tasks) == [["T1", "T2"], ["T3"]]


def test_capabilities_flag_unavailable_agents():
    pool, _ = recording_pool()
    pool.pop("web_search")
    text = capabilities({k: v for k, v in pool.items() if k not in ("fetch_url", "search_documents", "list_documents")})
    assert "research (Research Agent)" in text and "UNAVAILABLE" in text.split("research (")[1].split("\n")[0]


def test_browser_links_view_and_find():
    links = extract_links('<a href="/docs">Docs <b>v2</b></a><a href="#x">skip</a><a href="https://o.org/p">Other</a>', "https://ex.com/a/")
    assert links == [("Docs v2", "https://ex.com/docs"), ("Other", "https://o.org/p")]

    async def fake(url):
        return url, f"<title>T</title><p>{'word ' * 2000}needle here</p><a href='/next'>Next</a>", "text/html"
    s = BrowserSession(fake)
    page = asyncio.run(s.open("https://ex.com/"))
    assert "URL: https://ex.com/" in page and "read_page offset=" in page and "[0] Next → https://ex.com/next" in page
    assert "needle here" in s.find("needle") and "not found" in s.find("absent")


def test_sources_must_appear_in_tool_results():
    kept, dropped = ground_sources(["https://www.who.int/a/", "https://made.up/x", "sales.xlsx"], "URL: https://who.int/a\n[sales.xlsx · Sheet 1]")
    assert kept == ["https://www.who.int/a/", "sales.xlsx"] and dropped == ["https://made.up/x"]


class _Scripted:
    """Yields scripted turns for one specialist."""
    def __init__(self, turns):
        self.turns = list(turns)

    async def stream(self, **kw):
        for ev in self.turns.pop(0):
            yield ev
        yield {"type": "done", "stop_reason": "tool_use", "usage": {}}


def _run(spec_name, turns, pool=None):
    pool = pool or recording_pool(0)[0]
    events = []

    async def emit(t, d):
        events.append((t, d))
    out = asyncio.run(run_specialist(SPECS[spec_name], _Scripted(turns), "m", "do it", registry_for(SPECS[spec_name], pool),
                                     ToolContext("u", "r"), emit, max_calls=3))
    return out, events


def submit(**kw):
    return [{"type": "tool_call", "id": "s", "name": "submit_result", "input": {"status": "done", "result": "r", **kw}}]


def test_research_cannot_claim_done_without_evidence():
    out, _ = _run("research", [submit(), [{"type": "tool_call", "id": "w", "name": "web_search", "input": {"query": "q"}}],
                               submit(sources=["https://stats.test/pop", "https://fake.example"])])
    assert out.status == "done" and out.tool_calls == 1
    assert out.sources == ["https://stats.test/pop"] and "fake.example" in out.notes   # invented source removed


def test_done_without_tools_after_rejection_becomes_partial():
    out, _ = _run("research", [submit(), submit()])
    assert out.status == "partial" and "No tool evidence" in out.notes


def test_writing_agent_may_finish_without_tools_and_agent_must_submit():
    assert _run("writing", [submit()])[0].status == "done"
    out, events = _run("coding", [[{"type": "text", "text": "here is code"}], [{"type": "text", "text": "done!"}]])
    assert out.status == "failed" and "without calling submit_result" in out.error
    assert ("agent_note", {"text": "here is code"}) in events


def _orchestrator(llm, pool, monkeypatch, max_parallel=3):
    async def noop(*a, **k):
        return None
    for fn in ("update_run", "save_tasks", "update_task"):
        monkeypatch.setattr(orch_mod.store, fn, noop)
    events = []

    async def publish(t, d):
        events.append((t, d))
    o = orch_mod.Orchestrator(run={"id": "r1", "goal": "Brief on Testland population"}, user_id="u", llm=llm, model="m",
                              utility_llm=llm, utility_model="u", tool_pool=lambda: pool, publish=publish, max_parallel=max_parallel)
    return o, events


def test_parallel_when_safe_and_side_effects_run_alone(monkeypatch):
    pool, log = recording_pool(0.15)
    o, events = _orchestrator(TeamLLM(), pool, monkeypatch)
    final, status = asyncio.run(o.execute())
    spans = {name: (s, e) for name, s, e in log}
    overlap = lambda a, b: spans[a][0] < spans[b][1] and spans[b][0] < spans[a][1]  # noqa: E731
    assert overlap("web_search", "analyze_table") and overlap("web_search", "run_python")      # independent: parallel
    assert spans["generate_document"][0] >= max(spans["web_search"][1], spans["analyze_table"][1])  # waits for deps
    assert not any(overlap("generate_document", n) for n in spans if n != "generate_document")  # side effects: alone
    assert [t.status for t in o.tasks] == ["done"] * 4
    # conflict T1 vs T2 was adjudicated by the Verification Agent with a tool
    assert o.conflicts[0]["status"] == "resolved" and o.conflicts[0]["resolved_by"] == "verification"
    assert [p["task"] for p in o.conflicts[0]["positions"]] == ["T1", "T2"]          # unknown T99 dropped
    # verification failed once, the answer was revised, then passed
    assert status == "completed" and final.endswith("Revised.")
    v = [d for t, d in events if t == "verification"]
    assert [x["ok"] for x in v] == [False, True]
    working = [(d["agent"], d.get("title")) for t, d in events if t == "agent_status" and d["state"] == "working"]
    assert ("research", "Find the population") in working and ("writing", "Write the brief") in working
    assert any(t == "agent_tool_start" and d["agent"] == "research" and d["task"] == "T1" for t, d in events)
    t1 = o.tasks[0]
    assert t1.sources == ["https://stats.test/pop"] and "invented.example" in t1.notes


def test_max_parallel_is_respected(monkeypatch):
    pool, log = recording_pool(0.1)
    llm = TeamLLM(plan={"tasks": [{"agent": "research", "instruction": "research topic a"}, {"agent": "data_analyst", "instruction": "analyse table b"},
                                  {"agent": "coding", "instruction": "write code c"}]}, conflict=False,
                  verdicts=[{"ok": True, "issues": [], "checked": ["x"]}])
    o, _ = _orchestrator(llm, pool, monkeypatch, max_parallel=1)
    asyncio.run(o.execute())
    spans = sorted((s, e) for n, s, e in log if n in ("web_search", "analyze_table", "run_python"))
    assert all(a[1] <= b[0] for a, b in zip(spans, spans[1:]))  # strictly sequential


def test_failed_task_is_retried_then_disclosed(monkeypatch):
    pool, _ = recording_pool(0)
    llm = TeamLLM(fail_agent="Coding Agent", conflict=False, verdicts=[{"ok": False, "issues": ["Coding part missing"], "checked": []},
                                                                       {"ok": False, "issues": ["still missing"], "checked": []}])
    o, events = _orchestrator(llm, pool, monkeypatch)
    final, status = asyncio.run(o.execute())
    coding = next(t for t in o.tasks if t.agent == "coding")
    assert coding.status == "failed" and coding.attempts == 2
    assert any(t == "task_status" and d["status"] == "retrying" for t, d in events)
    assert "FAILED" in llm.synth_prompts[0]                     # the synthesizer is told, so it can disclose it
    assert status == "completed_with_issues"                    # verification never passed


def test_all_tasks_failing_fails_the_run(monkeypatch):
    pool, _ = recording_pool(0)
    llm = TeamLLM(plan={"tasks": [{"agent": "coding", "instruction": "write code c"}]}, fail_agent="Coding Agent")
    o, _ = _orchestrator(llm, pool, monkeypatch)
    with pytest.raises(RuntimeError, match="Every task failed"):
        asyncio.run(o.execute())
