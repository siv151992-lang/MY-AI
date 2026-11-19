"""Domain reasoning strategies: how to think well for each kind of task.

Each strategy is plain guidance injected into the system prompt for the task types the analyzer detected,
so it works identically with every LLM provider. Register new domains with `register_strategy`.
"""
from __future__ import annotations

STRATEGIES: dict[str, str] = {
    "math": """Mathematical reasoning:
- Restate the quantities, units and what is being asked. Define variables.
- Solve step by step. For any non-trivial arithmetic, algebra, statistics or unit conversion, compute it with run_python rather than mentally, and use the computed values.
- Sanity-check the result: order of magnitude, units, edge cases, and (when possible) solve a second way or substitute back.
- Present the method briefly, then the result with units. Round sensibly and say so.""",

    "logic": """Logical reasoning:
- Identify the premises and exactly what must be concluded. Separate given facts from assumptions.
- Work through the possibilities systematically (cases, elimination, truth tables, contrapositive). For puzzles with a search space, enumerate it with run_python when practical.
- Check the conclusion against every premise; look for counterexamples before claiming something must be true.
- Explain the key inference steps that justify the conclusion, not every dead end.""",

    "analytical": """Analytical reasoning:
- Establish the criteria that matter for the user's goal before evaluating options.
- Evaluate each option against the same criteria with evidence; distinguish facts, estimates and opinions.
- Weigh trade-offs explicitly and note what would change the conclusion.
- Finish with a clear recommendation or conclusion tied to the user's situation, plus key caveats.""",

    "technical": """Technical reasoning:
- Pin down the environment and constraints (language, versions, platform, existing code). State what you assume.
- For bugs: reproduce or trace the cause from the evidence before proposing a fix; explain the root cause.
- For code: write complete, idiomatic, runnable code; test it with run_python where the language allows, including an edge case; handle errors.
- Mention security, performance or compatibility implications when they are real.""",

    "factual": """Factual reasoning:
- Distinguish what is well established from what may have changed; use web_search for anything current, niche or high-stakes.
- Cross-check important claims against more than one source when possible; never invent citations.
- Answer directly first, then supporting detail; say how confident you are when the evidence is thin.""",

    "creative": """Creative work:
- Honour the requested form, length, audience and tone; fill unspecified choices with tasteful defaults.
- Prefer specific, vivid details over generic phrasing.""",

    "conversational": "",
}

EFFORT_BY_COMPLEXITY = {"simple": "none", "moderate": "low", "complex": "high"}


def register_strategy(task_type: str, guidance: str) -> None:
    STRATEGIES[task_type] = guidance


def guidance_for(task_types: list[str]) -> str:
    seen, blocks = set(), []
    for t in task_types:
        g = STRATEGIES.get(t, "")
        if g and t not in seen:
            seen.add(t)
            blocks.append(g)
    return "\n\n".join(blocks)


def effort_for(complexity: str, task_types: list[str]) -> str:
    effort = EFFORT_BY_COMPLEXITY.get(complexity, "low")
    # math/logic always benefit from deliberate reasoning: bump one level (never above high)
    if any(t in ("math", "logic") for t in task_types):
        effort = {"none": "low", "low": "medium"}.get(effort, effort)
    return effort
