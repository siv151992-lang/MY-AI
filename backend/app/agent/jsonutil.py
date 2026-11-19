from __future__ import annotations

import json
import re
from typing import Any

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def extract_json(text: str) -> Any | None:
    """Pull the first JSON object/array out of a model reply (tolerates code fences and chatter)."""
    candidates = [m.group(1) for m in _FENCE.finditer(text)] + [text]
    for c in candidates:
        c = c.strip()
        try:
            return json.loads(c)
        except json.JSONDecodeError:
            pass
        for open_ch, close_ch in (("{", "}"), ("[", "]")):
            start = c.find(open_ch)
            end = c.rfind(close_ch)
            if start != -1 and end > start:
                try:
                    return json.loads(c[start:end + 1])
                except json.JSONDecodeError:
                    continue
    return None
