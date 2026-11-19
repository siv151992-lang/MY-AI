"""Short-term memory and automatic context management for long conversations.

Invariant per conversation: messages[:summarized_upto] are represented by the rolling summary (and by
episodic segments); messages[summarized_upto:] are sent verbatim as the working window.

Compaction uses hysteresis: nothing happens while the window fits the budget; once it overflows, the
oldest turns are compacted down to `low_water` x budget in one go, so summarisation runs in batches
rather than on every turn. Each compacted chunk becomes a searchable segment, and the segments are folded
into the rolling summary, which is itself re-compressed when it grows too long.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..llm.formats import text_msg

SEGMENT_SYSTEM = """Summarise this part of a conversation between a user and an AI assistant so it can be recalled \
later. Keep the specifics: goals, decisions and their reasons, names, numbers, code/file names, results and open \
questions. Write compact prose or bullets, under 200 words. Do not add anything that was not said."""

ROLLING_SYSTEM = """You maintain the running summary of a long conversation so the assistant can continue it \
seamlessly. Merge the new section summaries into the existing summary. Keep: the user's goals, decisions made, \
key facts, numbers, code/file names and open questions; drop details that were superseded. Stay under {words} \
words. Reply with the updated summary only."""

SUMMARY_MAX_TOKENS = 900
SEGMENT_MAX_TOKENS = 6000
SEGMENT_MAX_MESSAGES = 16


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def message_tokens(m: dict) -> int:
    total = 4
    for p in m["content"]:
        if p["type"] == "text":
            total += estimate_tokens(p["text"])
        elif p["type"] == "image":
            total += 1500
        else:
            total += estimate_tokens(str(p.get("content") or p.get("input") or ""))
    return total


def message_text(m: dict) -> str:
    return " ".join(p.get("text", "") for p in m["content"] if p["type"] == "text")


def split_history(messages: list[dict], budget: int, keep_min: int = 4) -> int:
    """Index where a window of recent messages that fits `budget` tokens starts (never on an assistant turn)."""
    used = 0
    idx = len(messages)
    for i in range(len(messages) - 1, -1, -1):
        cost = message_tokens(messages[i])
        if used + cost > budget and len(messages) - i > keep_min:
            break
        used += cost
        idx = i
    while idx < len(messages) and messages[idx]["role"] != "user":
        idx += 1
    return idx


@dataclass
class WindowPlan:
    start: int                           # first verbatim message
    compact: tuple[int, int] | None      # range to fold into the summary now, if any


def plan_window(messages: list[dict], summarized_upto: int, budget: int, low_water: float = 0.6) -> WindowPlan:
    upto = max(0, min(summarized_upto, len(messages)))
    window_cost = sum(message_tokens(m) for m in messages[upto:])
    if window_cost <= budget:
        return WindowPlan(upto, None)
    start = max(upto, split_history(messages, int(budget * low_water)))
    if start <= upto:  # the recent turns alone exceed the target: keep the minimum window
        start = max(upto, split_history(messages, budget))
    return WindowPlan(start, (upto, start) if start > upto else None)


def chunk_ranges(messages: list[dict], start: int, end: int, max_tokens: int = SEGMENT_MAX_TOKENS,
                 max_messages: int = SEGMENT_MAX_MESSAGES) -> list[tuple[int, int]]:
    """Split [start, end) into segments that begin on user turns where possible."""
    ranges: list[tuple[int, int]] = []
    i = start
    while i < end:
        j, used = i, 0
        while j < end:
            cost = message_tokens(messages[j])
            if j > i and (used + cost > max_tokens or j - i >= max_messages):
                break
            used += cost
            j += 1
        # prefer to cut right before a user turn
        if j < end:
            k = j
            while k > i + 1 and messages[k]["role"] != "user":
                k -= 1
            if k > i + 1:
                j = k
        ranges.append((i, j))
        i = j
    return ranges


@dataclass
class Budget:
    """Token allocation for everything retrieved into the prompt besides the system core."""
    history: int
    memories: int = 1200
    recall: int = 1600
    summary: int = SUMMARY_MAX_TOKENS


def pack(items: list[dict], budget_tokens: int, key: str = "content") -> list[dict]:
    """Keep items in order while they fit the budget."""
    out, used = [], 0
    for it in items:
        cost = estimate_tokens(it[key]) + 4
        if used + cost > budget_tokens:
            continue
        out.append(it)
        used += cost
    return out


def transcript(messages: list[dict], per_message_chars: int = 3000) -> str:
    return "\n\n".join(f"{m['role'].upper()}: {message_text(m)[:per_message_chars]}" for m in messages)


async def summarize_segment(llm, model: str, messages: list[dict]) -> str:
    return (await llm.complete(model=model, system=SEGMENT_SYSTEM, max_tokens=400,
                               messages=[text_msg("user", transcript(messages))])).strip()


async def fold_summary(llm, model: str, previous: str | None, segments: list[str],
                       max_tokens: int = SUMMARY_MAX_TOKENS) -> str:
    words = int(max_tokens * 0.7)
    body = (f"<existing_summary>\n{previous or '(none yet)'}\n</existing_summary>\n\n<new_sections>\n"
            + "\n\n---\n\n".join(segments) + "\n</new_sections>")
    out = (await llm.complete(model=model, system=ROLLING_SYSTEM.format(words=words), max_tokens=max_tokens + 200,
                              messages=[text_msg("user", body)])).strip()
    if estimate_tokens(out) > max_tokens * 1.3:  # model ignored the limit: hard cap at a sentence boundary
        cut = out[: max_tokens * 4]
        out = cut[: cut.rfind(". ") + 1] or cut
    return out
