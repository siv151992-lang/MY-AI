"""Scripted model + recording tools for multi-agent tests (no network, deterministic)."""
import asyncio
import json
import re
import time

from app.agent.tools.base import Tool

PLAN = {"tasks": [
    {"id": "T1", "agent": "research", "title": "Find the population", "instruction": "Find the population of Testland"},
    {"id": "T2", "agent": "data_analyst", "title": "Compute growth", "instruction": "Compute population growth from the sheet"},
    {"id": "T3", "agent": "coding", "title": "Write a helper", "instruction": "Write a function to format numbers"},
    {"id": "T4", "agent": "writing", "title": "Write the brief", "instruction": "Write a short brief", "depends_on": ["T1", "T2"]},
]}


class TeamLLM:
    def __init__(self, plan=None, verdicts=None, conflict=True, fail_agent=None):
        self.plan = plan or PLAN
        self.verdicts = list(verdicts or [{"ok": False, "issues": ["Population figure lacks a source"], "checked": ["web_search"]},
                                          {"ok": True, "issues": [], "checked": ["fetch_url: confirmed 5.2 million"]}])
        self.conflict, self.fail_agent = conflict, fail_agent
        self.synth_prompts = []

    async def complete(self, *, model, system, messages, max_tokens=1000, **_):
        body = messages[-1]["content"][0]["text"]
        if system.startswith("You are the Planning Agent"):
            return json.dumps(self.plan)
        if system.startswith("Several AI agents"):
            if not self.conflict:
                return '{"conflicts": []}'
            return json.dumps({"conflicts": [{"topic": "population", "needs_check": True, "positions": [
                {"task": "T1", "claim": "5.2 million"}, {"task": "T2", "claim": "4.9 million"}, {"task": "T99", "claim": "x"}]}]})
        if system.startswith("You are the orchestrator"):
            self.synth_prompts.append(body)
            return "Testland has 5.2 million people (source: https://stats.test/pop)." + (" Revised." if "Problems found" in body else "")
        return "{}"

    async def stream(self, *, model, system, messages, tools=None, max_tokens=4096, temperature=None, reasoning=None):
        label = re.search(r"You are the (.+?), one specialist", system).group(1)
        names = [t["name"] for t in tools or []]
        control = "submit_verdict" if "submit_verdict" in names else "submit_result"
        used = sum(1 for m in messages if m["role"] == "user" and any(p["type"] == "tool_result" for p in m["content"]))
        tool_for = {"Research Agent": ("web_search", {"query": "Testland population"}),
                    "Data Analyst Agent": ("analyze_table", {"document_id": "d1", "question": "growth"}),
                    "Coding Agent": ("run_python", {"code": "print(1)"}),
                    "Writing Agent": ("generate_document", {"prompt": "brief"}),
                    "Verification Agent": ("fetch_url", {"url": "https://stats.test/pop"})}
        if label == self.fail_agent:
            yield {"type": "text", "text": "I give up."}
            yield {"type": "done", "stop_reason": "end_turn", "usage": {}}
            return
        if used == 0 and len(names) > 1:
            name, args = tool_for[label]
            yield {"type": "tool_call", "id": f"c-{label}", "name": name, "input": args}
        elif control == "submit_verdict":
            v = self.verdicts.pop(0) if self.verdicts else {"ok": True, "issues": [], "checked": []}
            yield {"type": "tool_call", "id": "v", "name": "submit_verdict", "input": v}
        else:
            result = {"Research Agent": "Population is 5.2 million.", "Data Analyst Agent": "Population is 4.9 million, +2%.",
                      "Coding Agent": "```python\ndef fmt(n): return f'{n:,}'\n```", "Writing Agent": "Brief written.",
                      "Verification Agent": "Resolution: 5.2 million is correct (official statistics page)."}[label]
            yield {"type": "tool_call", "id": "s", "name": "submit_result", "input": {
                "status": "done", "result": result, "confidence": "high",
                "sources": ["https://stats.test/pop", "https://invented.example/fake"] if label == "Research Agent" else []}}
        yield {"type": "done", "stop_reason": "tool_use", "usage": {}}


def recording_pool(delay: float = 0.15):
    """Tools that sleep and record (name, start, end) so tests can check what ran in parallel."""
    log: list[tuple[str, float, float]] = []

    def make(name: str, output: str) -> Tool:
        async def run(args, ctx):
            start = time.monotonic()
            await asyncio.sleep(delay)
            log.append((name, start, time.monotonic()))
            return output
        return Tool(name, name, {"type": "object", "properties": {}}, run, timeout=10)

    pool = {n: make(n, o) for n, o in {
        "web_search": "[1] Stats\nURL: https://stats.test/pop\nPopulation 5.2 million",
        "fetch_url": "Title: Stats\nURL: https://stats.test/pop\nThe population is 5.2 million.",
        "analyze_table": "growth 2%", "run_python": "exit_code: 0\nstdout:\n1", "generate_document": "Created brief.docx",
        "search_documents": "none", "list_documents": "none", "read_document": "none"}.items()}
    return pool, log
