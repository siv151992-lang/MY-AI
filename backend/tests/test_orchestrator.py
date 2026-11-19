"""Agent loop tests with a scripted provider (test double only — the app always uses real APIs)."""
import asyncio
import json

from app.agent.orchestrator import AgentConfig, run_agent
from app.agent.tools.base import Tool, ToolContext, ToolRegistry
from app.llm.formats import ProviderError, text_msg


class ScriptedLLM:
    def __init__(self, turns, completions=None):
        self.turns = list(turns)
        self.completions = list(completions or [])
        self.calls = []

    async def stream(self, *, model, system, messages, tools=None, max_tokens=4096, temperature=None):
        self.calls.append({"system": system, "messages": messages, "tools": tools})
        turn = self.turns.pop(0)
        if isinstance(turn, Exception):
            raise turn
        for ev in turn:
            yield ev

    async def complete(self, *, model, system, messages, max_tokens=1024):
        return self.completions.pop(0)


def done(reason="end_turn"):
    return {"type": "done", "stop_reason": reason, "usage": {"input_tokens": 10, "output_tokens": 5}}


def make_tools(log):
    async def add(args, ctx):
        log.append(args)
        return str(args["a"] + args["b"])
    return ToolRegistry([Tool("add", "add numbers", {"type": "object", "properties": {}, "required": ["a", "b"]}, add)])


def collect(gen):
    async def go():
        return [ev async for ev in gen]
    return asyncio.run(go())


def run(llm, text, cfg=None, tools=None, utility=None):
    log = []
    events = collect(run_agent(llm=llm, utility_llm=utility, cfg=cfg or AgentConfig(model="m"),
                               build_system=lambda plan: f"SYS plan={plan}", history=[],
                               user_message=text_msg("user", text), tools=tools or make_tools(log),
                               ctx=ToolContext("u", "c")))
    return events, log


def test_simple_answer_streams_tokens_and_final():
    llm = ScriptedLLM([[{"type": "text", "text": "Hello "}, {"type": "text", "text": "there"}, done()]])
    events, _ = run(llm, "hi")
    assert [e["text"] for e in events if e["type"] == "token"] == ["Hello ", "there"]
    final = events[-1]
    assert final["type"] == "final" and final["content"] == "Hello there"
    assert final["meta"]["plan"] is None and final["meta"]["tools"] == []


def test_tool_loop_executes_and_feeds_result_back():
    llm = ScriptedLLM([
        [{"type": "text", "text": "Computing."}, {"type": "tool_call", "id": "t1", "name": "add", "input": {"a": 2, "b": 3}}, done("tool_use")],
        [{"type": "text", "text": "The sum is 5."}, done()],
    ])
    events, log = run(llm, "what is 2+3")
    assert log == [{"a": 2, "b": 3}]
    types = [e["type"] for e in events]
    assert types.index("tool_start") < types.index("tool_end") < types.index("final")
    second_call_msgs = llm.calls[1]["messages"]
    assert second_call_msgs[-1]["content"][0] == {"type": "tool_result", "tool_call_id": "t1", "content": "5", "is_error": False}
    assert second_call_msgs[-2]["content"][1]["type"] == "tool_call"
    assert events[-1]["content"] == "Computing.\n\nThe sum is 5."
    assert events[-1]["meta"]["usage"] == {"input_tokens": 20, "output_tokens": 10}


def test_tool_errors_are_reported_to_model_not_raised():
    llm = ScriptedLLM([
        [{"type": "tool_call", "id": "t1", "name": "nope", "input": {}}, done("tool_use")],
        [{"type": "text", "text": "Sorry, that tool does not exist."}, done()],
    ])
    events, _ = run(llm, "x")
    end = next(e for e in events if e["type"] == "tool_end")
    assert end["is_error"] and "Unknown tool" in end["output"]
    assert events[-1]["type"] == "final"


def test_agent_mode_plans_then_verifies_and_revises():
    long_answer = "Draft answer. " * 30
    llm = ScriptedLLM(turns=[
        [{"type": "tool_call", "id": "t1", "name": "add", "input": {"a": 1, "b": 1}}, done("tool_use")],
        [{"type": "text", "text": long_answer}, done()],
        [{"type": "text", "text": "Corrected answer: 1+1 = 2."}, done()],
    ], completions=[
        json.dumps({"steps": ["Compute with add", "Answer"]}),
        '```json\n{"ok": false, "issues": ["Says 3 but tool returned 2"]}\n```',
    ])
    events, _ = run(llm, "add one and one", cfg=AgentConfig(model="m", agent_mode=True, max_revisions=1))
    types = [e["type"] for e in events]
    assert types[0:2] == ["status", "plan"]
    assert next(e for e in events if e["type"] == "plan")["steps"] == ["Compute with add", "Answer"]
    assert "reset" in types and types.index("verification") < types.index("reset")
    assert "plan=['Compute with add', 'Answer']" in llm.calls[0]["system"]
    final = events[-1]
    assert final["content"] == "Corrected answer: 1+1 = 2."
    assert final["meta"]["revisions"] == 1
    # revision prompt carries the reviewer's issues
    assert "Says 3 but tool returned 2" in llm.calls[2]["messages"][-1]["content"][0]["text"]


def test_provider_error_yields_error_event_and_no_final():
    llm = ScriptedLLM([ProviderError("Provider returned 401: invalid x-api-key", 401)])
    events, _ = run(llm, "hi")
    assert events[-1]["type"] == "error" and "401" in events[-1]["message"]
    assert not any(e["type"] == "final" for e in events)


def test_step_budget_forces_final_answer():
    loop = [{"type": "tool_call", "id": "t", "name": "add", "input": {"a": 1, "b": 1}}, done("tool_use")]
    llm = ScriptedLLM([loop, loop, [{"type": "text", "text": "Best effort answer"}, done()]])
    events, log = run(llm, "x", cfg=AgentConfig(model="m", max_steps=2))
    assert len(log) == 2
    assert events[-1]["content"].endswith("Best effort answer")
    assert "tool-use limit" in llm.calls[2]["messages"][-1]["content"][0]["text"]
