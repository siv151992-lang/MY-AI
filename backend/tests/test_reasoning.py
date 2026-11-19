import asyncio
import json
import random

from app.agent.reasoning import clarifier, strategies
from app.agent.reasoning.analyzer import LLMAnalyzer, fast_path, parse_analysis
from app.agent.reasoning.context import normalize_state, render_state, update_state
from app.agent.reasoning.planner import ensure_coverage, needs_plan, parse_plan
from app.agent.reasoning.privacy import ScratchpadFilter, strip_private
from app.agent.reasoning.types import Ambiguity, Plan, Subtask, TaskAnalysis
from app.agent.reasoning.verifier import DOMAIN_CHECKS, LLMVerifier, parse_verdict, should_verify
from app.llm.formats import AnthropicStreamParser, text_msg, to_anthropic, to_openai


class Echo:
    def __init__(self, reply):
        self.reply, self.prompts = reply, []

    async def complete(self, *, model, system, messages, max_tokens=1024):
        self.prompts.append((system, messages[0]["content"][0]["text"]))
        return self.reply


# ---------------- analyzer ----------------
def test_fast_path_only_for_trivial_messages():
    assert fast_path("thanks!", False).goal == "small talk"
    assert fast_path("What is the capital of Peru?", False).task_types == ["factual"]
    assert fast_path("what is 17% of 2350", False) is None            # math -> full analysis
    assert fast_path("What is X? And why is Y?", False) is None        # multi-part -> full analysis
    assert fast_path("convert the quarterly report please", False) is None  # instructions -> full analysis
    assert fast_path("hello", has_files=True) is None                  # files -> full analysis


def test_parse_analysis_full_and_defaults():
    a = parse_analysis(json.dumps({
        "task_types": ["math", "bogus"], "complexity": "complex", "goal": "g", "parts": ["a", "b"],
        "missing_info": ["the loan term"], "assumptions": ["monthly compounding"],
        "ambiguity": {"is_ambiguous": True, "interpretations": ["x", "y"], "chosen": "x", "ask": False, "question": None},
        "needs_tools": ["run_python", "teleport"], "verify": "strict"}), "q")
    assert a.task_types == ["math"] and a.parts == ["a", "b"] and a.needs_tools == ["run_python"]
    assert a.ambiguity.is_ambiguous and not a.ambiguity.ask and a.ambiguity.chosen == "x"
    junk = parse_analysis("I cannot comply", "original question")
    assert junk.parts == ["original question"] and junk.verify == "light"
    # "ask" without a question, or a single interpretation, is not actionable ambiguity
    b = parse_analysis(json.dumps({"ambiguity": {"is_ambiguous": True, "interpretations": ["only one"], "ask": True}}), "q")
    assert not b.ambiguity.is_ambiguous and not b.ambiguity.ask


def test_analyzer_sends_context_state_and_tools():
    llm = Echo(json.dumps({"task_types": ["technical"], "parts": ["p"]}))
    hist = [text_msg("user", "We use Spring Boot 3"), text_msg("assistant", "Noted")]
    a = asyncio.run(LLMAnalyzer(llm, "m").analyze("now add caching to the same service and benchmark it",
                                                  history=hist, state_text="Constraints:\n- Java 21",
                                                  tool_names=["run_python"]))
    prompt = llm.prompts[0][1]
    assert "Spring Boot 3" in prompt and "Java 21" in prompt and "run_python" in prompt
    assert a.task_types == ["technical"]


# ---------------- clarifier ----------------
def test_clarifier_policy():
    blocking = TaskAnalysis(complexity="moderate", missing_info=["the CSV file you mentioned"])
    d = clarifier.decide(blocking)
    assert d.ask and "the CSV file you mentioned" in d.message
    assert not clarifier.decide(blocking, already_asked=True).ask       # never ask twice in a row
    simple = TaskAnalysis(complexity="simple", missing_info=["exact city"])
    assert not clarifier.decide(simple).ask                             # simple tasks: answer + flag
    amb = TaskAnalysis(complexity="moderate", assumptions=["metric units"],
                       ambiguity=Ambiguity(True, ["mean", "median"], "mean", ask=False))
    d2 = clarifier.decide(amb)
    assert not d2.ask and "mean" in d2.proceed_note and "metric units" in d2.proceed_note


# ---------------- planner ----------------
def test_plan_parsing_and_coverage_repair():
    a = TaskAnalysis(complexity="complex", parts=["A", "B", "C"])
    p = parse_plan(json.dumps({"subtasks": [{"goal": "do A", "covers": [0, 99]}, {"goal": "wrap up", "covers": [1]}]}), 3)
    assert p.subtasks[0].covers == [0]                                   # out-of-range index dropped
    fixed = ensure_coverage(p, a)
    assert [s.goal for s in fixed.subtasks] == ["do A", "Answer: C", "wrap up"]
    assert [s.id for s in fixed.subtasks] == [1, 2, 3]
    assert parse_plan("1. first\n2) second", 0).steps() == ["first", "second"]
    empty = ensure_coverage(Plan(), a)
    assert len(empty.subtasks) == 3
    assert "via run_python" in Plan([Subtask(1, "compute", "run_python", done_when="number found")]).render()


def test_needs_plan():
    assert needs_plan(TaskAnalysis(complexity="complex"), agent_mode=False)
    assert needs_plan(TaskAnalysis(complexity="moderate", parts=["a", "b", "c"]), agent_mode=False)
    assert not needs_plan(TaskAnalysis(complexity="moderate", parts=["a"]), agent_mode=False)
    assert needs_plan(TaskAnalysis(complexity="simple"), agent_mode=True)
    assert not needs_plan(TaskAnalysis(fast_path=True), agent_mode=True)


# ---------------- strategies ----------------
def test_strategy_guidance_and_effort():
    g = strategies.guidance_for(["math", "technical", "math"])
    assert g.count("Mathematical reasoning") == 1 and "Technical reasoning" in g
    assert strategies.effort_for("simple", ["conversational"]) == "none"
    assert strategies.effort_for("moderate", ["logic"]) == "medium"
    assert strategies.effort_for("simple", ["math"]) == "low"
    assert strategies.effort_for("complex", ["creative"]) == "high"
    strategies.register_strategy("legal", "Legal reasoning: cite the statute.")
    assert "cite the statute" in strategies.guidance_for(["legal"])


# ---------------- verifier ----------------
def test_verifier_parsing_and_policy():
    v = parse_verdict(json.dumps({"ok": True, "parts_missing": ["part 2"], "issues": []}), ["p1", "part 2"])
    assert not v.ok and v.all_issues() == ["Not answered: part 2"]     # "ok" with missing parts is not ok
    assert parse_verdict("???", []).ok
    strict = TaskAnalysis(verify="strict")
    assert should_verify(strict, False, "x" * 100)
    assert not should_verify(TaskAnalysis(verify="none"), False, "x" * 1000)
    assert should_verify(TaskAnalysis(verify="none"), True, "x" * 1000)
    assert not should_verify(strict, True, "ok")


def test_verifier_uses_domain_checks():
    llm = Echo(json.dumps({"ok": False, "issues": ["Verify the key calculations with run_python"]}))
    v = asyncio.run(LLMVerifier(llm, "m").verify(TaskAnalysis(task_types=["math"], parts=["p"]), "q", "draft", []))
    assert DOMAIN_CHECKS["math"] in llm.prompts[0][0] and not v.ok


# ---------------- conversation state ----------------
def test_state_normalization_and_render():
    s = normalize_state({"goals": "ship v2", "decisions": ["Use HNSW", "use hnsw", ""] + [f"d{i}" for i in range(12)],
                         "bogus": ["x"]})
    assert s["goals"] == ["ship v2"] and len(s["decisions"]) == 8 and "bogus" not in s
    text = render_state(s)
    assert text.startswith("Goals:\n- ship v2") and "Decisions:" in text
    assert render_state(None) == ""


def test_state_update_keeps_old_state_on_bad_reply():
    old = {"goals": ["g"]}
    assert asyncio.run(update_state(Echo("nope"), "m", old, "u", "a"))["goals"] == ["g"]
    new = asyncio.run(update_state(Echo(json.dumps({"goals": ["g"], "decisions": ["chose Postgres"]})), "m", old, "u", "a"))
    assert new["decisions"] == ["chose Postgres"]


# ---------------- private reasoning ----------------
def _filter_all(text, sizes):
    f, out, i = ScratchpadFilter(), [], 0
    while i < len(text):
        n = sizes[len(out) % len(sizes)]
        out.append(f.feed(text[i:i + n]))
        i += n
    return "".join(out) + f.flush()


def test_scratchpad_filter_any_chunking():
    text = "Intro. <scratchpad>secret 1 < 2 <scratch</scratchpad>\nVisible <b>bold</b> a<thinking>more secret</thinking> end <"
    expected = "Intro. Visible <b>bold</b> a end <"
    rng = random.Random(7)
    for _ in range(200):
        sizes = [rng.randint(1, 9) for _ in range(5)]
        assert _filter_all(text, sizes) == expected
    unterminated = _filter_all("Answer<scratchpad>never closed", [3])
    assert unterminated == "Answer"
    assert strip_private("a<scratchpad>x</scratchpad>b<thinking>y") == "ab"


def test_anthropic_thinking_stream_and_roundtrip():
    lines = [
        'data: {"type":"content_block_start","index":0,"content_block":{"type":"thinking","thinking":""}}',
        'data: {"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":"Let me "}}',
        'data: {"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":"think."}}',
        'data: {"type":"content_block_delta","index":0,"delta":{"type":"signature_delta","signature":"EqQB"}}',
        'data: {"type":"content_block_stop","index":0}',
        'data: {"type":"content_block_start","index":1,"content_block":{"type":"redacted_thinking","data":"enc"}}',
        'data: {"type":"content_block_stop","index":1}',
        'data: {"type":"content_block_start","index":2,"content_block":{"type":"text","text":""}}',
        'data: {"type":"content_block_delta","index":2,"delta":{"type":"text_delta","text":"Done."}}',
        'data: {"type":"content_block_stop","index":2}',
    ]
    p = AnthropicStreamParser()
    evs = [e for ln in lines for e in p.feed(ln)]
    assert [e["type"] for e in evs] == ["thinking_start", "thinking_block", "thinking_start", "thinking_block", "text"]
    part = evs[1]["part"]
    assert part == {"type": "thinking", "thinking": "Let me think.", "signature": "EqQB"}
    msg = [{"role": "assistant", "content": [part, evs[3]["part"], {"type": "text", "text": "Done."}]}]
    blocks = to_anthropic(msg)[0]["content"]
    assert blocks[0]["type"] == "thinking" and blocks[0]["signature"] == "EqQB"
    assert blocks[1] == {"type": "redacted_thinking", "data": "enc"}
    # other providers never receive private reasoning
    assert to_openai("", msg) == [{"role": "assistant", "content": "Done."}]
