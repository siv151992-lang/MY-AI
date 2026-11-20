"""Basic answers without any AI model. Deterministic and honest: when it doesn't know, it says so."""
from __future__ import annotations

import ast
import math
import operator
import re
from datetime import datetime, timezone

from .knowledge import CAPITALS, ENTRIES

GREETING = re.compile(r"^\s*(hi|hello|hey|hiya|good (morning|afternoon|evening)|namaste|vanakkam|yo)\b[\s!.,]*\w*\s*[!.]*$", re.I)
THANKS = re.compile(r"\b(thanks|thank you|thx|ty|great|awesome|perfect|nice)\b", re.I)
WHO = re.compile(r"\b(who are you|what are you|your name)\b", re.I)
TIME = re.compile(r"\b(what(?:'s| is)? the (time|date|day)|what time is it|today'?s date|what day is (it|today)|current (time|date))\b", re.I)
CAPITAL = re.compile(r"capital (?:city )?of ([a-z .]+?)\s*\??$", re.I)
STOP = set("a an the is are was were what whats what's who how why when where which do does did can could you your i me my "
           "of to in on for and or tell about please explain define meaning mean is it this that there with by from".split())

MODE_NOTE = ("\n\n_Basic mode: no AI model is connected, so I answer from my built-in knowledge. Connect free local AI "
             "or an API key on the chat screen for full answers._")


# ------------------------------------------------------------------ math
_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow, ast.USub: operator.neg,
        ast.UAdd: operator.pos}
_FUNCS = {"sqrt": math.sqrt, "sin": math.sin, "cos": math.cos, "tan": math.tan, "log": math.log10, "ln": math.log,
          "abs": abs, "round": round}
_CONSTS = {"pi": math.pi, "e": math.e}


def _eval(node):
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.Name) and node.id in _CONSTS:
        return _CONSTS[node.id]
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and (abs(right) > 100 or abs(left) > 1e6):
            raise ValueError("too large")
        return _OPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.operand))
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCS and len(node.args) == 1:
        return _FUNCS[node.func.id](_eval(node.args[0]))
    raise ValueError("unsupported")


def calculate(text: str) -> str | None:
    t = text.lower().strip().rstrip("?.! ")
    m = re.search(r"(\d+(?:\.\d+)?)\s*%\s*of\s*(\d+(?:\.\d+)?)", t)
    if m:
        a, b = float(m.group(1)), float(m.group(2))
        return f"{_fmt(a)}% of {_fmt(b)} = **{_fmt(a * b / 100)}**"
    t = re.sub(r"^(what is|what's|whats|calculate|compute|solve|how much is|evaluate)\s+", "", t)
    t = t.replace("×", "*").replace("÷", "/").replace("^", "**").replace(" x ", " * ")
    t = re.sub(r"\b(plus)\b", "+", t)
    t = re.sub(r"\b(minus)\b", "-", t)
    t = re.sub(r"\b(times|multiplied by)\b", "*", t)
    t = re.sub(r"\b(divided by|over)\b", "/", t)
    t = re.sub(r"square root of\s*(\d+(?:\.\d+)?)", r"sqrt(\1)", t)
    if not re.search(r"\d", t) or not re.fullmatch(r"[\d\s.+\-*/%()a-z_,]*", t) or not re.search(r"[+\-*/%(]|sqrt|sin|cos|tan|log|ln", t):
        return None
    try:
        val = _eval(ast.parse(t, mode="eval"))
    except (SyntaxError, ValueError, ZeroDivisionError, OverflowError, TypeError):
        return "That calculation isn't valid (for example, division by zero)." if "/ 0" in t or "/0" in t else None
    return f"{t.replace('**', '^')} = **{_fmt(val)}**"


def _fmt(v: float) -> str:
    if isinstance(v, float) and v.is_integer() and abs(v) < 1e15:
        return f"{int(v):,}"
    if isinstance(v, int):
        return f"{v:,}"
    return f"{v:,.6g}" if abs(v) >= 1e-4 else f"{v:.6g}"


# ------------------------------------------------------------------ units
_UNITS = {  # unit -> (dimension, factor to base)
    "km": ("len", 1000), "kilometer": ("len", 1000), "kilometers": ("len", 1000), "kilometre": ("len", 1000), "kilometres": ("len", 1000),
    "m": ("len", 1), "meter": ("len", 1), "meters": ("len", 1), "metre": ("len", 1), "metres": ("len", 1),
    "cm": ("len", 0.01), "centimeters": ("len", 0.01), "centimetres": ("len", 0.01), "mm": ("len", 0.001),
    "mile": ("len", 1609.344), "miles": ("len", 1609.344), "mi": ("len", 1609.344),
    "ft": ("len", 0.3048), "feet": ("len", 0.3048), "foot": ("len", 0.3048),
    "in": ("len", 0.0254), "inch": ("len", 0.0254), "inches": ("len", 0.0254), "yard": ("len", 0.9144), "yards": ("len", 0.9144),
    "kg": ("mass", 1), "kilogram": ("mass", 1), "kilograms": ("mass", 1), "g": ("mass", 0.001), "grams": ("mass", 0.001),
    "lb": ("mass", 0.45359237), "lbs": ("mass", 0.45359237), "pound": ("mass", 0.45359237), "pounds": ("mass", 0.45359237),
    "oz": ("mass", 0.028349523125), "ounce": ("mass", 0.028349523125), "ounces": ("mass", 0.028349523125),
    "l": ("vol", 1), "liter": ("vol", 1), "liters": ("vol", 1), "litre": ("vol", 1), "litres": ("vol", 1),
    "ml": ("vol", 0.001), "gallon": ("vol", 3.785411784), "gallons": ("vol", 3.785411784),
    "kmh": ("speed", 1 / 3.6), "km/h": ("speed", 1 / 3.6), "mph": ("speed", 0.44704), "m/s": ("speed", 1),
}
_TEMP = {"c": "C", "celsius": "C", "°c": "C", "f": "F", "fahrenheit": "F", "°f": "F", "k": "K", "kelvin": "K"}


def convert(text: str) -> str | None:
    m = re.search(r"(-?\d+(?:\.\d+)?)\s*([a-z°/]+)\s+(?:to|in|into|=)\s+([a-z°/]+)", text.lower())
    if not m:
        return None
    val, a, b = float(m.group(1)), m.group(2), m.group(3)
    if a in _TEMP and b in _TEMP:
        fa, fb = _TEMP[a], _TEMP[b]
        c = val if fa == "C" else (val - 32) * 5 / 9 if fa == "F" else val - 273.15
        out = c if fb == "C" else c * 9 / 5 + 32 if fb == "F" else c + 273.15
        return f"{_fmt(val)} °{fa} = **{_fmt(round(out, 2))} °{fb}**".replace("°K", "K")
    if a in _UNITS and b in _UNITS and _UNITS[a][0] == _UNITS[b][0]:
        out = val * _UNITS[a][1] / _UNITS[b][1]
        return f"{_fmt(val)} {a} = **{_fmt(round(out, 4))} {b}**"
    return None


# ------------------------------------------------------------------ knowledge base
def _words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in STOP]


def kb_search(text: str) -> tuple[dict | None, float]:
    """Best entry by keyword/phrase overlap. Phrases count more than single words, and an entry only qualifies
    when its keywords cover at least half of the question's content words (so "last week" doesn't match "week")."""
    t = " " + " ".join(re.findall(r"[a-z0-9]+", text.lower())) + " "
    words = set(_words(text))
    best, best_score = None, 0.0
    for e in ENTRIES:
        vocab = {w for k in e["k"] for w in _words(k)}
        if words and len(words & vocab) / len(words) < 0.5:
            continue
        score = 0.0
        for k in e["k"]:
            kn = " " + " ".join(re.findall(r"[a-z0-9]+", k.lower())) + " "
            if kn.strip() and kn in t:
                score += 1.5 * len(kn.split())
            else:
                kw = set(_words(k))
                if kw and kw <= words:
                    score += 0.8 * len(kw)
        if score > best_score:
            best, best_score = e, score
    return best, best_score


def capital(text: str) -> str | None:
    m = CAPITAL.search(text.strip())
    if not m:
        return None
    country = re.sub(r"^the\s+", "", m.group(1).strip().lower()).strip(" .")
    if country not in CAPITALS:
        return f"I don't have the capital of “{m.group(1).strip()}” in my built-in list."
    cap = CAPITALS[country]
    if cap is None:
        return "That country's capital is politically disputed, so I can't give a simple answer in basic mode."
    return f"The capital of {country.title() if len(country) > 3 else country.upper()} is **{cap}**."


# ------------------------------------------------------------------ answer
def answer(text: str, *, name: str = "", now: datetime | None = None, doc_hits: list[dict] | None = None) -> str:
    t = (text or "").strip()
    now = now or datetime.now(timezone.utc).astimezone()
    if not t:
        return "Ask me something!" + MODE_NOTE
    if GREETING.match(t):
        hi = f"Hello{', ' + name.split()[0] if name else ''}! 👋"
        return (f"{hi} I'm Aria. No AI model is connected yet, so I'm in **basic mode**: I can answer simple facts, "
                "do math (`15% of 80`, `sqrt(144)`), convert units (`10 miles to km`), tell the date and time, and "
                "search your uploaded documents. Connect free local AI or an API key on the chat screen for full answers.")
    if WHO.search(t):
        return "I'm **Aria**, your AI assistant. " + kb_search("what can you do")[0]["a"].split(". ", 1)[1] + MODE_NOTE
    if TIME.search(t):
        return (f"It's **{now.strftime('%A, %d %B %Y')}**, **{now.strftime('%H:%M')}** "
                f"({now.tzname() or 'server time'}, the time on the computer running Aria).")
    for fn in (convert, calculate, capital):
        out = fn(t)
        if out:
            return out
    entry, score = kb_search(t)
    if entry and score >= 0.8:
        return entry["a"] + ("" if entry in ENTRIES[:13] else MODE_NOTE)
    if doc_hits:
        parts = ["Here's what I found in your documents:"]
        for h in doc_hits[:3]:
            snippet = re.sub(r"\s+", " ", h["content"]).strip()[:400]
            where = f", page {h['page']}" if h.get("page") else ""
            parts.append(f"> {snippet}…\n\n— *{h['filename']}{where}*")
        return "\n\n".join(parts) + MODE_NOTE
    if THANKS.search(t) and len(t.split()) <= 6:
        return "You're welcome! 😊"
    return ("I don't know that one in basic mode — I can only answer simple facts, math, unit conversions, the date "
            "and time, and search your documents without an AI model. For a real answer, connect **free local AI** "
            "(no key) or an API key on the chat screen.")


async def search_documents(user_id: str, text: str, k: int = 3) -> list[dict]:
    """Keyword search over the user's readable documents (no model needed). Only real matches are returned."""
    from ..db import or_query, pool, rows
    from ..documents import access
    q = or_query(text)
    if not q:
        return []
    allowed = await access.accessible_ids(user_id)
    if not allowed:
        return []
    return rows(await pool().fetch(
        "SELECT f.filename, c.page, c.content, ts_rank(c.tsv, websearch_to_tsquery('english', $2)) AS kw "
        "FROM document_chunks c JOIN files f ON f.id = c.file_id WHERE c.file_id = ANY($1::uuid[]) "
        "AND c.tsv @@ websearch_to_tsquery('english', $2) ORDER BY kw DESC LIMIT $3", allowed, q, k))
