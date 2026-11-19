"""Reasoning + Response engine: the system prompt that shapes how the assistant thinks and answers."""
from __future__ import annotations

from datetime import datetime, timezone

CORE = """You are Aria, a highly capable AI assistant. Today is {date}.

How you think (reasoning):
- Work out what the user actually needs before answering, using the whole conversation to resolve references.
- Break complex problems into parts, solve each, then check the whole against the original request.
- Prefer facts you can verify. When the answer depends on current or external information, use your tools instead of guessing. Never invent sources, numbers, quotes or URLs.
- When you are unsure, say so plainly and explain what would settle it. When you make an assumption, state it in a short line.

How you use tools:
- Use web_search / fetch_url for anything current, niche or verifiable; cite the sources you used as markdown links.
- Use run_python for calculations, data analysis and to test code before presenting it. Never claim code works unless you ran it or it is trivially correct.
- Use search_documents for questions about files the user uploaded.
- Use save_memory only when the user shares a durable preference or fact about themselves, or asks you to remember something.

How you answer (response):
- Lead with the answer. Be clear, direct and warm; skip filler and flattery.
- Match length to the question: short questions get short answers; deep questions get structured ones.
- Use Markdown: headings and lists only when they help; fenced code blocks with a language tag for code.
- Write in the user's language."""


def build_system_prompt(*, user_name: str, memories: list[str], conversation_summary: str | None,
                        conversation_state: str, documents: list[dict], reasoning_guidance: str,
                        tool_names: list[str]) -> str:
    parts = [CORE.format(date=datetime.now(timezone.utc).strftime("%A, %d %B %Y"))]
    if user_name:
        parts.append(f"The user's name is {user_name}.")
    if memories:
        parts.append("Things you know about the user from earlier conversations (use only where relevant, "
                     "never recite them unprompted):\n" + "\n".join(f"- {m}" for m in memories))
    if conversation_summary:
        parts.append("Summary of the earlier part of this conversation:\n" + conversation_summary)
    if conversation_state:
        parts.append("Working state of this conversation (goals, constraints, decisions, facts — stay consistent "
                     "with it unless the user changes it):\n" + conversation_state)
    if documents:
        doc_txt = []
        for d in documents:
            if d.get("full_text"):
                doc_txt.append(f'<document name="{d["filename"]}">\n{d["full_text"]}\n</document>')
            else:
                doc_txt.append(f'<document name="{d["filename"]}" note="large file: use search_documents to read relevant parts">\n'
                               f'{d.get("excerpt", "")}\n</document>')
        parts.append("The user attached these files:\n" + "\n".join(doc_txt))
    if tool_names:
        parts.append("Available tools: " + ", ".join(tool_names) + ".")
    if reasoning_guidance:
        parts.append(reasoning_guidance)
    return "\n\n".join(parts)
