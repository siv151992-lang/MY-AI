"""ReasoningEngine: composes the pluggable reasoning components.

    analyze  ->  clarify-or-proceed  ->  plan  ->  domain guidance + effort  ->  (answer)  ->  verify

Every component is an object with one async method, so any of them can be swapped (a rules-based analyzer, a
different verifier, a fine-tuned planner…) without touching the orchestrator. All LLM calls go through the
provider-neutral `complete()` interface, so the engine works with every configured provider.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Protocol

from ...llm.formats import ProviderError
from . import clarifier, strategies
from .analyzer import LLMAnalyzer
from .planner import LLMPlanner, needs_plan
from .privacy import CONCISE_EXPLANATION
from .types import Plan, TaskAnalysis, Verdict
from .verifier import LLMVerifier, should_verify

log = logging.getLogger("aria.reasoning")


class Analyzer(Protocol):
    async def analyze(self, user_text: str, *, history: list[dict], state_text: str = "", has_files: bool = False,
                      tool_names: list[str] | None = None) -> TaskAnalysis: ...


class Planner(Protocol):
    async def plan(self, analysis: TaskAnalysis, tool_names: list[str]) -> Plan: ...


class Verifier(Protocol):
    async def verify(self, analysis: TaskAnalysis, user_text: str, answer: str, evidence: list[str]) -> Verdict: ...


@dataclass
class Preparation:
    analysis: TaskAnalysis
    plan: Plan | None = None
    clarify: clarifier.ClarifyDecision | None = None
    effort: str = "none"
    guidance: str = ""          # appended to the system prompt
    events: list[dict] = field(default_factory=list)


class ReasoningEngine:
    def __init__(self, analyzer: Analyzer, planner: Planner, verifier: Verifier):
        self.analyzer, self.planner, self.verifier = analyzer, planner, verifier

    @classmethod
    def default(cls, utility_llm, utility_model: str) -> "ReasoningEngine":
        return cls(LLMAnalyzer(utility_llm, utility_model), LLMPlanner(utility_llm, utility_model),
                   LLMVerifier(utility_llm, utility_model))

    async def prepare(self, user_text: str, *, history: list[dict], state_text: str, has_files: bool,
                      tool_names: list[str], agent_mode: bool, last_assistant_asked: bool) -> Preparation:
        try:
            analysis = await self.analyzer.analyze(user_text, history=history, state_text=state_text,
                                                   has_files=has_files, tool_names=tool_names)
        except ProviderError as e:
            log.warning("analysis failed (%s); using defaults", e)
            analysis = TaskAnalysis(task_types=["analytical"], complexity="moderate", goal=user_text[:200],
                                    parts=[user_text[:300]], verify="light")
        if agent_mode and analysis.verify == "none" and not analysis.fast_path:
            analysis.verify = "light"
        prep = Preparation(analysis=analysis)

        decision = clarifier.decide(analysis, agent_mode=agent_mode, already_asked=last_assistant_asked)
        prep.clarify = decision
        if decision.ask:
            return prep

        if needs_plan(analysis, agent_mode):
            try:
                prep.plan = await self.planner.plan(analysis, tool_names)
            except ProviderError as e:
                log.warning("planning failed (%s); continuing without plan", e)

        prep.effort = strategies.effort_for(analysis.complexity, analysis.task_types)
        prep.guidance = self.guidance(analysis, prep.plan, decision)
        return prep

    @staticmethod
    def guidance(analysis: TaskAnalysis, plan: Plan | None, decision: clarifier.ClarifyDecision) -> str:
        blocks = []
        if not analysis.fast_path:
            if len(analysis.parts) > 1:
                blocks.append("The request has these parts — answer every one, in this order, clearly separated:\n"
                              + "\n".join(f"{i}. {p}" for i, p in enumerate(analysis.parts, 1)))
            dom = strategies.guidance_for(analysis.task_types)
            if dom:
                blocks.append(dom)
            if decision.proceed_note:
                blocks.append(decision.proceed_note)
            if plan and plan.subtasks:
                blocks.append("Execution plan (follow it; adapt if you learn something new):\n" + plan.render())
        blocks.append(CONCISE_EXPLANATION)
        return "\n\n".join(blocks)

    def should_verify(self, analysis: TaskAnalysis, used_tools: bool, answer: str) -> bool:
        return should_verify(analysis, used_tools, answer)

    async def verify(self, analysis: TaskAnalysis, user_text: str, answer: str, evidence: list[str]) -> Verdict:
        try:
            return await self.verifier.verify(analysis, user_text, answer, evidence)
        except ProviderError:
            return Verdict(ok=True, note="verifier unavailable")
