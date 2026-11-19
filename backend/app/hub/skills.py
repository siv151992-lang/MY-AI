"""Reusable AI skills: named instructions with input fields, an allowed tool set and an optional model.

A skill can be used three ways: run on its own (Skills page), applied to a chat message, or used as a step in an
automation. A few built-in skills ship with Aria; users create their own."""
from __future__ import annotations

import json
import re

from .. import db

VAR = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]{0,40})\s*\}\}")

BUILTIN = [
    {"id": "builtin:summarize", "name": "Summarize", "description": "Concise summary with key points and action items.",
     "instructions": "Summarize the text below for a busy reader: a 2-3 sentence overview, then the key points as bullets, "
                     "then any decisions or action items (with owners/dates if stated). Do not add information that is "
                     "not in the text.\n\nText:\n{{input}}",
     "inputs": [{"name": "input", "label": "Text to summarize", "required": True}], "tools": [], "model_id": None},
    {"id": "builtin:translate", "name": "Translate", "description": "Faithful translation that keeps formatting.",
     "instructions": "Translate the text into {{language}}. Keep meaning, tone, names, numbers and Markdown formatting. "
                     "Output only the translation.\n\nText:\n{{input}}",
     "inputs": [{"name": "language", "label": "Target language", "required": True},
                {"name": "input", "label": "Text", "required": True}], "tools": [], "model_id": None},
    {"id": "builtin:explain-code", "name": "Explain code", "description": "Walk through code, flag bugs and risks.",
     "instructions": "Explain what this code does, step by step, for a developer new to it. Then list likely bugs, edge "
                     "cases and security risks you can actually see in the code, each with the line it concerns.\n\n"
                     "{{input}}",
     "inputs": [{"name": "input", "label": "Code", "required": True}], "tools": [], "model_id": None},
    {"id": "builtin:email", "name": "Write an email", "description": "Clear email from a few notes.",
     "instructions": "Write an email to {{recipient}} based on these notes. Tone: {{tone}}. Subject line first, then the "
                     "body. Keep it short and specific; don't invent facts.\n\nNotes:\n{{input}}",
     "inputs": [{"name": "recipient", "label": "Recipient", "required": True},
                {"name": "tone", "label": "Tone", "required": False, "default": "friendly and professional"},
                {"name": "input", "label": "Notes", "required": True}], "tools": [], "model_id": None},
    {"id": "builtin:web-brief", "name": "Web brief", "description": "Short sourced brief on a topic (needs web search).",
     "instructions": "Search the web for the topic, open the best 2-4 sources with fetch_url, and write a short brief "
                     "with the key facts, each attributed to its URL. List sources at the end.\n\nTopic: {{input}}",
     "inputs": [{"name": "input", "label": "Topic", "required": True}], "tools": ["web_search", "fetch_url"],
     "model_id": None},
]
BUILTIN_BY_ID = {s["id"]: s for s in BUILTIN}


class SkillError(ValueError):
    pass


def render(template: str, inputs: list[dict], values: dict[str, str]) -> str:
    vals = {}
    for spec in inputs:
        v = str(values.get(spec["name"], "") or spec.get("default", "") or "").strip()
        if spec.get("required") and not v:
            raise SkillError(f"'{spec.get('label') or spec['name']}' is required")
        vals[spec["name"]] = v
    for k, v in values.items():
        vals.setdefault(k, str(v))
    text = VAR.sub(lambda m: vals.get(m.group(1), ""), template)
    if "{{" not in template and vals.get("input"):  # template without placeholders: append the input
        text += "\n\n" + vals["input"]
    return text


def validate(data: dict) -> dict:
    name = str(data.get("name", "")).strip()
    instructions = str(data.get("instructions", "")).strip()
    if not name or len(name) > 80:
        raise SkillError("Name must be 1-80 characters")
    if not instructions or len(instructions) > 20_000:
        raise SkillError("Instructions must be 1-20000 characters")
    inputs = []
    seen = set()
    for i in data.get("inputs") or []:
        n = str(i.get("name", "")).strip()
        if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]{0,40}", n) or n in seen:
            raise SkillError(f"Invalid or duplicate input name '{n}'")
        seen.add(n)
        inputs.append({"name": n, "label": str(i.get("label") or n)[:80], "required": bool(i.get("required")),
                       "default": str(i.get("default") or "")[:500]})
    for v in set(VAR.findall(instructions)) - seen:
        inputs.append({"name": v, "label": v.replace("_", " ").capitalize(), "required": True, "default": ""})
    if not inputs:
        inputs = [{"name": "input", "label": "Input", "required": True, "default": ""}]
    tools = [str(t)[:64] for t in (data.get("tools") or [])][:20]
    return {"name": name, "description": str(data.get("description", "")).strip()[:300], "instructions": instructions,
            "inputs": inputs, "tools": tools, "model_id": data.get("model_id") or None, "project_id": data.get("project_id") or None}


def _row(r) -> dict:
    d = db.row(r)
    for k in ("inputs", "tools"):
        if isinstance(d.get(k), str):
            d[k] = json.loads(d[k])
    d["builtin"] = False
    return d


async def list_skills(user_id: str) -> list[dict]:
    rows = await db.pool().fetch("SELECT * FROM skills WHERE user_id=$1 ORDER BY uses DESC, updated_at DESC", user_id)
    return [_row(r) for r in rows] + [{**s, "builtin": True, "uses": None} for s in BUILTIN]


async def get_skill(user_id: str, skill_id: str) -> dict | None:
    if skill_id in BUILTIN_BY_ID:
        return {**BUILTIN_BY_ID[skill_id], "builtin": True}
    try:
        r = await db.pool().fetchrow("SELECT * FROM skills WHERE id=$1 AND user_id=$2", skill_id, user_id)
    except Exception:
        return None
    return _row(r) if r else None


async def create_skill(user_id: str, data: dict) -> dict:
    d = validate(data)
    return _row(await db.pool().fetchrow(
        "INSERT INTO skills (user_id, name, description, instructions, inputs, tools, model_id, project_id) "
        "VALUES ($1,$2,$3,$4,$5::jsonb,$6::jsonb,$7,$8) RETURNING *", user_id, d["name"], d["description"],
        d["instructions"], json.dumps(d["inputs"]), json.dumps(d["tools"]), d["model_id"], d["project_id"]))


async def update_skill(user_id: str, skill_id: str, data: dict) -> dict | None:
    d = validate(data)
    r = await db.pool().fetchrow(
        "UPDATE skills SET name=$3, description=$4, instructions=$5, inputs=$6::jsonb, tools=$7::jsonb, model_id=$8, "
        "project_id=$9, updated_at=now() WHERE id=$1 AND user_id=$2 RETURNING *", skill_id, user_id, d["name"],
        d["description"], d["instructions"], json.dumps(d["inputs"]), json.dumps(d["tools"]), d["model_id"], d["project_id"])
    return _row(r) if r else None


async def delete_skill(user_id: str, skill_id: str) -> bool:
    try:
        return (await db.pool().execute("DELETE FROM skills WHERE id=$1 AND user_id=$2", skill_id, user_id)).endswith("1")
    except Exception:
        return False


async def count_use(skill: dict) -> None:
    if not skill.get("builtin"):
        await db.pool().execute("UPDATE skills SET uses = uses + 1 WHERE id=$1", skill["id"])


async def run(user_id: str, skill: dict, values: dict[str, str], *, model_id: str | None = None, emit=None) -> dict:
    """Run a skill once and return its result. Raises SkillError for invalid input."""
    from . import executor
    prompt = render(skill["instructions"], skill["inputs"], values)
    out, used_model = await executor.execute(
        user_id, label=f"the '{skill['name']}' skill", instructions="Apply the user's instructions below exactly.",
        prompt=prompt, tool_names=skill.get("tools") or [], model_id=model_id or skill.get("model_id"), emit=emit)
    await count_use(skill)
    return {"status": out.status, "result": out.result, "sources": out.sources, "notes": out.notes,
            "error": out.error, "model": used_model, "tool_calls": out.tool_calls,
            "tools_used": [{"tool": t["tool"], "ok": t["ok"], "input": t["input"]} for t in out.tool_log]}
