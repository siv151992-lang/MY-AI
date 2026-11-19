"""Agent loop tests with a scripted provider (test double only — the app always uses real APIs)."""
import asyncio
import json

from app.agent.orchestrator import AgentConfig, run_agent
from app.agent.reasoning import ReasoningEngine
from app.agent.tools.base import Tool, ToolContext, ToolRegistry
from app.llm.formats import ProviderError, text_msg


class ScriptedLLM:
    """`turns` feed stream(); `completions` feed complete() (analyzer, planner, verifier) in call order."""

    def __init__(self, turns, completions=None):
        self.turns = list(turns)
        self.completions = list(completions or [])
        self.calls = []
        self.completion_systems = []

    async def stream(self, *, model, system, messages, tools=None, max_tokens=4096, temperature=None, reasoning=None):
        self.calls.append({"system": system, "messages": messages, "tools": tools, "reasoning": reasoning})
        turn = self.turns.pop(0)
        if isinstance(turn, Exception):
            raise turn
        for ev in turn:
            yield ev

    async def complete(self, *, model, system, messages, max_tokens=1024):
        self.completion_systems.append(system)
        return self.completions.pop(0)


def done(reason="end_turn"):
    return {"type": "done", "stop_reason": reason, "usage": {"input_tokens": 10, "output_tokens": 5}}


def analysis(**kw):
    base = {"task_types": ["analytical"], "complexity": "moderate", "goal": "g", "parts": ["p1"],
            "missing_info": [], "assumptions": [], "ambiguity": {"is_ambiguous": False}, "needs_tools": [],
            "verify": "none"}
    base.update(kw)
    return json.dumps(base)


def make_tools(log):
    async def add(args, ctx):
        log.append(args)
        return str(args["a"] + args["b"])
    return ToolRegistry([Tool("add", "add numbers", {"type": "object", "properties": {}, "required": ["a", "b"]}, add)])


def run(llm, text, cfg=None, history=None, last_asked=False):
    log = []
    engine = ReasoningEngine.default(llm, "util")

    async def go():
        return [ev async for ev in run_agent(
            llm=llm, engine=engine, cfg=cfg or AgentConfig(model="m"), build_system=lambda g: f"SYS\n{g}",
            history=history or [], user_message=text_msg("user", text), tools=make_tools(log),
            ctx=ToolContext("u", "c"), last_assistant_asked=last_asked)]
    return asyncio.run(go()), log


def tokens(events):
    return "".join(e["text"] for e in events if e["type"] == "token")


def test_small_talk_takes_fast_path_without_analysis_call():
    llm = ScriptedLLM([[{"type": "text", "text": "Hi! How can I help?"}, done()]])
    events, _ = run(llm, "hello!")
    assert llm.completion_systems == []          # no analyzer/planner/verifier calls
    assert not any(e["type"] == "analysis" for e in events)
    assert events[-1]["content"] == "Hi! How can I help?"
    assert llm.calls[0]["reasoning"]["effort"] == "none"


def test_tool_loop_executes_and_feeds_result_back():
    llm = ScriptedLLM([
        [{"type": "text", "text": "Computing."}, {"type": "tool_call", "id": "t1", "name": "add", "input": {"a": 2, "b": 3}}, done("tool_use")],
        [{"type": "text", "text": "The sum is 5."}, done()],
    ], completions=[analysis(task_types=["math"], complexity="simple", verify="none")])
    events, log = run(llm, "please calculate what 2 plus 3 equals for my report")
    assert log == [{"a": 2, "b": 3}]
    second = llm.calls[1]["messages"]
    assert second[-1]["content"][0] == {"type": "tool_result", "tool_call_id": "t1", "content": "5", "is_error": False}
    assert events[-1]["content"] == "Computing.\n\nThe sum is 5."
    assert "Mathematical reasoning" in llm.calls[0]["system"]          # domain strategy injected
    assert llm.calls[0]["reasoning"] == {"mode": "scratchpad", "effort": "low"}  # math: simple -> low


def test_multi_part_question_is_planned_with_full_coverage_and_verified():
    parts = ["What is pgvector?", "How does HNSW work?", "Which index should I use for 1M rows?"]
    draft = "pgvector is a Postgres extension. " * 10 + "HNSW builds a layered graph. " * 5
    fixed = "1. pgvector … 2. HNSW … 3. Use HNSW for 1M rows."
    llm = ScriptedLLM(turns=[[{"type": "text", "text": draft}, done()], [{"type": "text", "text": fixed}, done()]],
                      completions=[
                          analysis(task_types=["technical", "analytical"], complexity="complex", parts=parts, verify="strict"),
                          json.dumps({"subtasks": [{"goal": "Explain pgvector", "covers": [0]},
                                                   {"goal": "Explain HNSW", "covers": [1]}]}),  # forgets part 3
                          json.dumps({"ok": False, "parts_missing": ["Which index should I use for 1M rows?"], "issues": []}),
                      ])
    events, _ = run(llm, "Explain pgvector, how HNSW works, and which index I should use for a million rows")
    an = next(e for e in events if e["type"] == "analysis")
    assert an["parts"] == parts
    plan = next(e for e in events if e["type"] == "plan")["steps"]
    assert any("1M rows" in s for s in plan), "planner gap must be filled so every part is covered"
    system = llm.calls[0]["system"]
    assert "3. Which index should I use for 1M rows?" in system and "Technical reasoning" in system
    types = [e["type"] for e in events]
    assert types.index("verification") < types.index("reset")
    assert "Not answered: Which index" in llm.calls[1]["messages"][-1]["content"][0]["text"]
    final = events[-1]
    assert final["content"] == fixed and final["meta"]["revisions"] == 1
    assert final["meta"]["analysis"]["parts"] == parts


def test_costly_ambiguity_asks_instead_of_guessing():
    llm = ScriptedLLM(turns=[], completions=[analysis(
        complexity="moderate", parts=["convert the report"],
        ambiguity={"is_ambiguous": True, "interpretations": ["Convert to PDF", "Translate to Tamil"],
                   "chosen": "Convert to PDF", "ask": True, "question": "Do you want the report converted to PDF, or translated?"},
        assumptions=["Keep the original layout"])])
    events, _ = run(llm, "can you convert the quarterly report for the Brazil team please")
    assert llm.calls == []  # main model never called
    text = tokens(events)
    assert text.startswith("Do you want the report converted to PDF")
    assert "- Translate to Tamil" in text and "Keep the original layout" in text
    assert events[-1]["meta"]["clarification"] is True


def test_does_not_ask_twice_in_a_row():
    amb = analysis(ambiguity={"is_ambiguous": True, "interpretations": ["A", "B"], "chosen": "A", "ask": True,
                              "question": "A or B?"})
    llm = ScriptedLLM(turns=[[{"type": "text", "text": "Going with A: done."}, done()]], completions=[amb])
    events, _ = run(llm, "just do whatever makes sense with the report thing", last_asked=True)
    assert events[-1]["content"] == "Going with A: done."
    assert "proceed with this interpretation" in llm.calls[0]["system"] and "A" in llm.calls[0]["system"]


def test_scratchpad_reasoning_is_never_shown_or_stored():
    chunks = ["<scratch", "pad>Let x = 12. 12*7 = 84, che", "ck: 84/7=12 ✓</scratch", "pad>\nThe answer is **84**."]
    llm = ScriptedLLM([[*({"type": "text", "text": c} for c in chunks), done()]],
                      completions=[analysis(task_types=["math"], complexity="moderate", verify="none")])
    events, _ = run(llm, "if a box holds 12 apples how many apples are in 7 boxes")
    visible = tokens(events)
    assert visible == "The answer is **84**."
    assert "scratchpad" not in events[-1]["content"] and "check" not in events[-1]["content"]
    assert any(e["type"] == "status" and e["text"] == "Thinking" for e in events)
    assert "<scratchpad>" in llm.calls[0]["system"]  # instruction given to non-native models


def test_native_thinking_blocks_are_private_but_returned_in_tool_loop():
    thinking = {"type": "thinking", "thinking": "secret plan", "signature": "sig=="}
    llm = ScriptedLLM([
        [{"type": "thinking_start"}, {"type": "thinking_block", "part": thinking},
         {"type": "tool_call", "id": "t1", "name": "add", "input": {"a": 1, "b": 2}}, done("tool_use")],
        [{"type": "text", "text": "It is 3."}, done()],
    ], completions=[analysis(task_types=["math"], complexity="complex", verify="none"),
                    json.dumps({"subtasks": [{"goal": "add", "method": "run_python", "covers": [0]}]})])
    events, _ = run(llm, "work out one plus two using the add tool and explain", cfg=AgentConfig(model="m", reasoning_mode="anthropic_thinking"))
    assert "secret plan" not in json.dumps(events)
    assert llm.calls[0]["reasoning"] == {"mode": "anthropic_thinking", "effort": "high"}
    assert "<scratchpad>" not in llm.calls[0]["system"]
    assistant_turn = llm.calls[1]["messages"][-2]
    assert assistant_turn["content"][0] == thinking and assistant_turn["content"][1]["type"] == "tool_call"


def test_analyzer_failure_degrades_gracefully():
    class Broken(ScriptedLLM):
        async def complete(self, **kw):
            raise ProviderError("utility model down")
    llm = Broken([[{"type": "text", "text": "Answer anyway."}, done()]])
    events, _ = run(llm, "compare three approaches to caching in a Spring Boot service and pick one")
    assert events[-1]["type"] == "final" and events[-1]["content"] == "Answer anyway."


def test_provider_error_yields_error_event_and_no_final():
    llm = ScriptedLLM([ProviderError("Provider returned 401: invalid x-api-key", 401)])
    events, _ = run(llm, "hi")
    assert events[-1]["type"] == "error" and "401" in events[-1]["message"]
    assert not any(e["type"] == "final" for e in events)


def test_step_budget_forces_final_answer():
    loop = [{"type": "tool_call", "id": "t", "name": "add", "input": {"a": 1, "b": 1}}, done("tool_use")]
    llm = ScriptedLLM([loop, loop, [{"type": "text", "text": "Best effort answer"}, done()]],
                      completions=[analysis(complexity="simple")])
    events, log = run(llm, "go", cfg=AgentConfig(model="m", max_steps=2))
    assert len(log) == 2
    assert events[-1]["content"].endswith("Best effort answer")
    assert "tool-use limit" in llm.calls[2]["messages"][-1]["content"][0]["text"]
