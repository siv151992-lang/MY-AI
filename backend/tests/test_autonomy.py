"""Autonomous runner scenarios (scripted model = test double; tools are real Tool objects)."""
import asyncio
import json

import pytest

from app.agent.tools.actions import make_create_file_tool
from app.agent.tools.base import Tool, ToolRegistry
from app.autonomy.manager import RunManager
from app.autonomy.planner import parse_steps
from app.autonomy.runner import AgentRunner
from app.autonomy.store import MemoryStore


def done(reason="end_turn"):
    return {"type": "done", "stop_reason": reason, "usage": {"input_tokens": 1, "output_tokens": 1}}


def call(name, args, cid=None):
    return {"type": "tool_call", "id": cid or f"c-{name}-{json.dumps(args, sort_keys=True)[:20]}", "name": name, "input": args}


def finish(status="done", summary="ok", error=""):
    return [call("complete_step", {"status": status, "summary": summary, "error": error}), done("tool_use")]


class ExecLLM:
    """Scripted executor model: a list of turns, each a list of stream events."""

    def __init__(self, turns):
        self.turns, self.calls = list(turns), []

    async def stream(self, *, model, system, messages, tools=None, max_tokens=4096, temperature=None, reasoning=None):
        self.calls.append(messages)
        for ev in self.turns.pop(0):
            yield ev


class UtilLLM:
    """Utility model answering by module (planner / replanner / remediation / verifier / summary)."""

    def __init__(self, **queues):
        self.q = {k: list(v) for k, v in queues.items()}

    async def complete(self, *, model, system, messages, max_tokens=1024):
        for key, marker in (("replan", "recovery module"), ("remediate", "remediation module"),
                            ("plan", "planning module"), ("verify", "You verify whether"), ("summary", "executive summary")):
            if marker in system:
                q = self.q.get(key) or []
                if not q:
                    raise AssertionError(f"unexpected {key} call")
                v = q.pop(0)
                if isinstance(v, Exception):
                    raise v
                return v if isinstance(v, str) else json.dumps(v)
        raise AssertionError("unknown utility call")


def plan(*steps):
    return {"steps": [{"title": t, "tool": tool, "success_criteria": "done"} for t, tool in steps]}


YES = {"achieved": "yes", "issues": [], "evidence": "tool log shows it"}


def registry(log, tmp_path=None):
    async def lookup(args, ctx):
        log.append(("lookup", args))
        if args.get("q") == "bad":
            raise ValueError("upstream 502")
        return f"result for {args['q']}"

    async def send(args, ctx):
        log.append(("send", args))
        return f"sent to {args['to']}"

    files = []

    async def save(user_id, file_id, filename, mime, size, path, text):
        files.append((filename, text))

    tools = [Tool("lookup", "Look things up", {"type": "object", "required": ["q"]}, lookup),
             Tool("send", "Send a message externally", {"type": "object", "required": ["to"]}, send,
                  requires_approval=True, describe=lambda a: f"Send to {a['to']}")]
    if tmp_path is not None:
        tools.append(make_create_file_tool(tmp_path, save))
    return ToolRegistry(tools), files


def make_manager(exec_llm, util, tools, **kw):
    store = MemoryStore()

    def factory(*, store, bus, gate, model_id):
        return AgentRunner(store=store, bus=bus, gate=gate, llm=exec_llm, model="m", utility_llm=util,
                           utility_model="u", tools=tools, backoff_base=0, **kw)
    return RunManager(store, factory, approval_timeout_s=5)


async def run_to_end(mgr, goal="do the thing", on_event=None):
    run = await mgr.start("user-1", goal, None)
    if on_event:
        q = mgr.bus.subscribe(run.id)

        async def watch():
            while True:
                ev = await q.get()
                await on_event(mgr, run, ev)
        watcher = asyncio.create_task(watch())
    await mgr.wait(run.id)
    if on_event:
        watcher.cancel()
    r = await mgr.store.get_run("user-1", run.id)
    return r, await mgr.store.list_steps(run.id), await mgr.store.list_actions(run.id), \
        [e["type"] for e in await mgr.store.list_events(run.id)], mgr


def go(coro):
    return asyncio.run(coro)


def test_happy_path_real_file_created_and_report_from_data(tmp_path):
    log = []
    tools, files = registry(log, tmp_path)
    exec_llm = ExecLLM([
        [call("lookup", {"q": "sales"}), done("tool_use")], finish(summary="Found sales data"),
        [call("create_file", {"filename": "summary.md", "content": "# Sales\nUp 12%"}), done("tool_use")],
        finish(summary="Wrote summary.md"),
    ])
    util = UtilLLM(plan=[plan(("Look up sales", "lookup"), ("Write summary file", "create_file"))],
                   verify=[YES], summary=["Sales were looked up and summary.md was created."])
    run, steps, actions, events, _ = go(run_to_end(make_manager(exec_llm, util, tools)))
    assert run.status == "completed"
    assert [s.status for s in steps] == ["done", "done"]
    assert log == [("lookup", {"q": "sales"})]
    assert files == [("summary.md", "# Sales\nUp 12%")]
    assert (tmp_path / "user-1").exists() and len(list((tmp_path / "user-1").iterdir())) == 1  # real file on disk
    assert [a.tool for a in actions] == ["lookup", "create_file"] and all(a.ok for a in actions)
    for section in ("## Steps", "## Actions performed", "`create_file`", "## Files created", "summary.md",
                    "## Verification", "Sales were looked up"):
        assert section in run.report
    for t in ("plan", "step_started", "tool_start", "tool_end", "step_done", "verification", "report"):
        assert t in events


def test_sensitive_action_waits_for_approval_then_runs():
    log = []
    tools, _ = registry(log)
    exec_llm = ExecLLM([[call("send", {"to": "boss@x.com"}), done("tool_use")], finish(summary="Sent")])
    util = UtilLLM(plan=[plan(("Send update", "send"))], verify=[YES], summary=["Sent."])
    seen = {}

    async def approve(mgr, run, ev):
        if ev["type"] == "approval_required":
            seen["summary"] = ev["data"]["summary"]
            assert log == []  # nothing executed before the decision
            await mgr.decide("user-1", run.id, ev["data"]["id"], True)

    run, steps, actions, events, mgr = go(run_to_end(make_manager(exec_llm, util, tools), on_event=approve))
    assert seen["summary"] == "Send to boss@x.com"
    assert log == [("send", {"to": "boss@x.com"})]
    assert actions[0].approval == "approved" and actions[0].ok
    assert run.status == "completed" and "you approved" in run.report
    assert "awaiting_approval" in json.dumps(go(mgr.store.list_events(run.id)))


def test_denied_action_is_never_executed_and_run_reports_it():
    log = []
    tools, _ = registry(log)
    exec_llm = ExecLLM([[call("send", {"to": "all@x.com"}), done("tool_use")],
                        finish("failed", "Could not send", "User denied permission")])
    util = UtilLLM(plan=[plan(("Send announcement", "send"))],
                   verify=[{"achieved": "no", "issues": ["Announcement not sent"], "evidence": "denied"}],
                   summary=["Not sent: permission denied."])

    async def deny(mgr, run, ev):
        if ev["type"] == "approval_required":
            await mgr.decide("user-1", run.id, ev["data"]["id"], False)

    run, steps, actions, _, _ = go(run_to_end(make_manager(exec_llm, util, tools, step_retries=0, max_replans=0),
                                              on_event=deny))
    assert log == []
    assert actions[0].approval == "denied" and not actions[0].ok
    assert steps[0].status == "failed" and run.status == "failed"
    assert "**denied**" in run.report and "Errors and recovery" in run.report
    # the model was told explicitly not to retry
    assert "DENIED" in json.dumps(exec_llm.calls[-1])


def test_grounding_guard_rejects_done_without_a_real_action():
    log = []
    tools, _ = registry(log)
    exec_llm = ExecLLM([
        finish(summary="I looked it up (I promise)"),             # claims done, no tool used -> rejected
        [call("lookup", {"q": "facts"}), done("tool_use")], finish(summary="Looked up facts"),
    ])
    util = UtilLLM(plan=[plan(("Look up facts", "lookup"))], verify=[YES], summary=["Done."])
    run, steps, actions, events, _ = go(run_to_end(make_manager(exec_llm, util, tools)))
    assert "warning" in events
    assert steps[0].status == "done" and steps[0].attempts == 1 and log == [("lookup", {"q": "facts"})]
    assert "Rejected completion" in json.dumps(exec_llm.calls[1])


def test_tool_error_retry_then_success():
    log = []
    tools, _ = registry(log)
    exec_llm = ExecLLM([
        [call("lookup", {"q": "bad"}), done("tool_use")], finish("failed", "upstream failed", "upstream 502"),
        [call("lookup", {"q": "good"}), done("tool_use")], finish(summary="Got it from the mirror"),
    ])
    util = UtilLLM(plan=[plan(("Fetch data", "lookup"))], verify=[YES], summary=["Recovered."])
    run, steps, actions, events, _ = go(run_to_end(make_manager(exec_llm, util, tools)))
    assert run.status == "completed" and steps[0].attempts == 2
    assert "step_error" in events
    assert [a.ok for a in actions] == [False, True]
    assert "retried with a different approach" in run.report
    assert "upstream 502" in json.dumps(exec_llm.calls[2])   # second attempt sees the first error


def test_step_failure_triggers_replan_with_alternative():
    log = []
    tools, _ = registry(log)
    exec_llm = ExecLLM([
        finish("failed", "source down", "API offline"),                  # step 1 attempt 1 (retries=0)
        [call("lookup", {"q": "backup"}), done("tool_use")], finish(summary="Used backup source"),
    ])
    util = UtilLLM(plan=[plan(("Query primary API", "lookup"), ("Old final step", "none"))],
                   replan=[plan(("Query backup source", "lookup"))], verify=[YES], summary=["Recovered via backup."])
    run, steps, actions, events, _ = go(run_to_end(make_manager(exec_llm, util, tools, step_retries=0)))
    assert "replanning" in events
    assert [(s.title, s.status, s.origin) for s in steps] == [
        ("Query primary API", "failed", "plan"), ("Query backup source", "done", "replan")]
    assert run.status == "completed_with_issues" or run.status == "completed"
    assert "re-planned 1 new step" in run.report


def test_partial_verification_triggers_one_remediation_pass():
    log = []
    tools, _ = registry(log)
    exec_llm = ExecLLM([
        [call("lookup", {"q": "a"}), done("tool_use")], finish(summary="part A"),
        [call("lookup", {"q": "b"}), done("tool_use")], finish(summary="part B"),
    ])
    util = UtilLLM(plan=[plan(("Do part A", "lookup"))],
                   verify=[{"achieved": "partial", "issues": ["Part B missing"], "evidence": ""}, YES],
                   remediate=[plan(("Do part B", "lookup"))], summary=["Both parts done."])
    run, steps, _, events, _ = go(run_to_end(make_manager(exec_llm, util, tools)))
    assert [s.title for s in steps] == ["Do part A", "Do part B"] and steps[1].origin == "remediation"
    assert events.count("verification") == 2 and run.status == "completed"


def test_cancel_while_waiting_for_approval():
    log = []
    tools, _ = registry(log)
    exec_llm = ExecLLM([[call("send", {"to": "x@y.z"}), done("tool_use")]])
    util = UtilLLM(plan=[plan(("Send", "send"), ("After", "none"))], summary=["Cancelled before sending."])

    async def cancel(mgr, run, ev):
        if ev["type"] == "approval_required":
            await mgr.cancel("user-1", run.id)

    run, steps, _, events, _ = go(run_to_end(make_manager(exec_llm, util, tools), on_event=cancel))
    assert run.status == "cancelled" and log == []
    assert [s.status for s in steps] == ["skipped", "skipped"]
    assert "⏹️ Cancelled" in run.report and events[-1] == "status"


def test_report_survives_summary_failure_and_planner_drops_unknown_tools():
    steps = parse_steps(json.dumps({"steps": [{"title": "Hack the mainframe", "tool": "teleport"},
                                              {"title": "Look", "tool": "lookup"}]}), ["lookup"], 0, "plan", 5)
    assert [s.tool for s in steps] == ["none", "lookup"]
    log = []
    tools, _ = registry(log)
    exec_llm = ExecLLM([finish(summary="Explained the answer")])
    util = UtilLLM(plan=[plan(("Explain", "none"))], verify=[YES], summary=[RuntimeError("model down")])
    run, *_ = go(run_to_end(make_manager(exec_llm, util, tools)))
    assert run.status == "completed" and "## Steps" in run.report and "## Summary" not in run.report


def test_concurrency_limit_and_ownership():
    log = []
    tools, _ = registry(log)
    mgr = make_manager(ExecLLM([]), UtilLLM(), tools)
    mgr.max_concurrent = 0
    from app.autonomy.manager import TooManyRuns
    with pytest.raises(TooManyRuns):
        go(mgr.start("user-1", "goal here", None))
    assert go(mgr.cancel("someone-else", "nope")) is False
