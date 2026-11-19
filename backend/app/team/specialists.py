"""The specialist agents: what each is for, how it is prompted, and which tools it may use."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ..agent.tools.base import Tool, ToolRegistry

COMMON = """You are the {label}, one specialist in a team of AI agents working on a user's goal. The orchestrator \
gave you ONE task. Do that task well and nothing else.
- Work only from evidence you actually obtain with your tools (or from the inputs you were given). Never claim you \
read, ran, checked or found something unless a tool result in this conversation shows it.
- If you cannot complete the task, say exactly what is missing; a partial, honest result beats a confident guess.
- Finish by calling {control} exactly once."""

ROLE = {
    "research": "Find and read reliable, preferably primary sources on the web and in the user's documents. Search, then "
                "open the most promising results with fetch_url before relying on them. In your result, attribute every "
                "fact to its source URL or document, note disagreements between sources, and list the URLs you used in "
                "`sources`.",
    "coding": "Write correct, idiomatic, complete code for the task. Run it with run_python (tests or a small demo) before "
              "presenting it, read errors, fix them and re-run. Put the final code in fenced blocks in your result and "
              "say how you verified it (the command and its output).",
    "data_analyst": "Analyse data: the user's spreadsheets/CSVs/documents (list_documents, analyze_table, read_document) or "
                    "data provided in the task (run_python with pandas). Compute numbers with code rather than estimating, "
                    "show the key figures, state assumptions and data limitations.",
    "writing": "Write clear, well-structured prose (reports, summaries, emails, documentation) from the inputs you were "
               "given. Preserve facts, numbers and citations exactly as provided; never add facts that aren't in the "
               "inputs. Use generate_document only when the task asks for a file.",
    "browser": "Navigate specific websites like a person would: open pages, read them, search within them and follow links "
               "to reach the information the task asks for. Report what you found with the exact page URLs.",
    "verification": "Check other agents' work critically and independently: re-derive numbers with run_python, re-check "
                    "important facts against sources (fetch_url / web_search / documents), and look for contradictions, "
                    "unsupported claims and missing parts. Be specific about what you checked and how.",
}

LABEL = {"research": "Research Agent", "coding": "Coding Agent", "data_analyst": "Data Analyst Agent",
         "writing": "Writing Agent", "planning": "Planning Agent", "browser": "Browser Agent",
         "verification": "Verification Agent"}
DESCRIPTION = {
    "research": "web search + reading pages + searching the user's documents; returns sourced findings",
    "coding": "writes and runs code (Python sandbox), debugs, explains code",
    "data_analyst": "analyses spreadsheets/CSVs/tables and computes statistics with pandas",
    "writing": "drafts and edits prose; can create DOCX/PDF/XLSX/PPTX/Markdown files in the user's documents",
    "browser": "navigates specific websites: open pages, follow links, find text on a page",
    "verification": "independently checks facts, numbers and code from other agents",
}


@dataclass
class AgentSpec:
    name: str
    tools: list[str]               # names of tools this agent may use (from the available pool)
    side_effects: bool = False     # creates/changes things outside the sandbox → never run in parallel
    requires_tools: bool = False   # a "done" result without any successful tool call is rejected

    @property
    def label(self) -> str:
        return LABEL[self.name]

    def system(self, control: str = "submit_result") -> str:
        return COMMON.format(label=self.label, control=control) + "\n\nYour speciality: " + ROLE[self.name]


SPECS: dict[str, AgentSpec] = {
    "research": AgentSpec("research", ["web_search", "fetch_url", "search_documents", "list_documents"], requires_tools=True),
    "coding": AgentSpec("coding", ["run_python"]),
    "data_analyst": AgentSpec("data_analyst", ["list_documents", "read_document", "search_documents", "analyze_table", "run_python"],
                              requires_tools=True),
    "writing": AgentSpec("writing", ["search_documents", "read_document", "generate_document"], side_effects=True),
    "browser": AgentSpec("browser", ["open_page", "read_page", "find_in_page", "follow_link"], requires_tools=True),
    "verification": AgentSpec("verification", ["web_search", "fetch_url", "run_python", "search_documents", "read_document"]),
}
DELEGATES = [n for n in SPECS]  # agents the planner may assign (the planner itself plans)


def registry_for(spec: AgentSpec, pool: dict[str, Tool]) -> ToolRegistry:
    return ToolRegistry([pool[n] for n in spec.tools if n in pool])


def capabilities(pool: dict[str, Tool]) -> str:
    """Agent roster for the planner, including what is unavailable on this server."""
    lines = []
    for name in DELEGATES:
        spec = SPECS[name]
        have = [t for t in spec.tools if t in pool]
        missing = [t for t in spec.tools if t not in pool]
        line = f"- {name} ({LABEL[name]}): {DESCRIPTION[name]}"
        if missing:
            line += f" [not available here: {', '.join(missing)}]"
        if spec.requires_tools and not have:
            line += " [UNAVAILABLE: none of its tools are configured — do not assign it]"
        lines.append(line)
    return "\n".join(lines)


ToolPoolFactory = Callable[[], dict[str, Tool]]
