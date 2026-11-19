"""Table profiling (pure Python) and question answering over tables with pandas in the sandbox."""
from __future__ import annotations

import base64
import csv
import io
import math
import re
import statistics
import tarfile
from collections import Counter
from datetime import date, datetime

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2})?)?")


def _num(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)) and not (isinstance(v, float) and math.isnan(v)):
        return float(v)
    if isinstance(v, str):
        s = v.strip().replace(",", "").replace("$", "").replace("€", "").replace("£", "").rstrip("%")
        try:
            return float(s) if s else None
        except ValueError:
            return None
    return None


def profile(columns: list[str], rows: list[list], total_rows: int | None = None) -> dict:
    cols = []
    for j, name in enumerate(columns):
        vals = [r[j] for r in rows if j < len(r) and r[j] not in (None, "")]
        nums = [x for x in (_num(v) for v in vals) if x is not None]
        dates = [v for v in vals if isinstance(v, str) and DATE_RE.match(v)]
        if vals and len(nums) >= 0.8 * len(vals):
            kind = "number"
        elif vals and len(dates) >= 0.8 * len(vals):
            kind = "date"
        elif vals and all(isinstance(v, bool) or str(v).lower() in ("true", "false", "yes", "no") for v in vals):
            kind = "boolean"
        else:
            kind = "text"
        c = {"name": name, "type": kind, "non_empty": len(vals), "empty": len(rows) - len(vals),
             "distinct": len({str(v) for v in vals})}
        if kind == "number" and nums:
            c.update(min=min(nums), max=max(nums), mean=round(statistics.fmean(nums), 4), sum=round(sum(nums), 4),
                     median=statistics.median(nums))
        elif kind == "date" and dates:
            c.update(min=min(dates), max=max(dates))
        else:
            c["top"] = [[str(v)[:60], n] for v, n in Counter(str(v) for v in vals).most_common(5)]
        cols.append(c)
    return {"rows": total_rows if total_rows is not None else len(rows), "analysed_rows": len(rows), "columns": cols}


def to_csv(columns: list[str], rows: list[list]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(columns)
    w.writerows(rows)
    return buf.getvalue()


def safe_name(name: str, i: int) -> str:
    s = re.sub(r"[^A-Za-z0-9_]+", "_", name).strip("_").lower()[:40] or "table"
    return f"t{i}_{s}.csv"


ANALYZE_SYSTEM = """You answer questions about tabular data by writing a Python 3 script that uses pandas.

The data files are in the current directory (listed below with their columns and a profile). Load them with \
pandas.read_csv. Write a complete script that computes the answer from ALL rows and prints a clear, labelled \
result (use df.to_string() or to_markdown-free formatting for small tables; round sensibly). Never hard-code values \
you could compute, never invent data, and print any caveats (e.g. missing values excluded). No network, no plotting.
Reply with ONLY the script in a ```python code block."""

EXPLAIN_SYSTEM = """You explain the result of a data analysis to the user in a few sentences or bullets. Use only the \
numbers in the output. If the output shows an error or is empty, say the analysis failed and why."""


def build_prompt(question: str, tables: list[dict]) -> str:
    parts = [f"Question: {question}", "Files:"]
    for i, t in enumerate(tables):
        cols = ", ".join(f"{c['name']} ({c['type']})" for c in t["profile"].get("columns", []))
        parts.append(f"- {safe_name(t['name'], i)}: table '{t['name']}' ({t['location']}), {t['total_rows']} rows"
                     + (f" (file contains the first {len(t['rows'])})" if len(t["rows"]) < t["total_rows"] else "")
                     + f"; columns: {cols}")
        sample = to_csv(t["columns"], t["rows"][:5])
        parts.append(f"  first rows:\n{sample}")
    return "\n".join(parts)


def extract_code(reply: str) -> str:
    m = re.search(r"```(?:python)?\s*\n(.*?)```", reply, re.S)
    return (m.group(1) if m else reply).strip()


def archive(files: dict[str, bytes]) -> str:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return base64.b64encode(buf.getvalue()).decode()


async def run_in_sandbox(files: dict[str, bytes], command: str, *, sandbox_url: str | None, token: str | None,
                         timeout: int = 60) -> dict:
    import httpx
    if not sandbox_url:
        raise RuntimeError("No sandbox is configured (SANDBOX_URL), so table analysis cannot run code.")
    headers = {"authorization": f"Bearer {token}"} if token else {}
    async with httpx.AsyncClient(timeout=timeout + 30) as c:
        r = await c.post(f"{sandbox_url.rstrip('/')}/exec", headers=headers,
                         json={"archive": archive(files), "command": command, "timeout": timeout})
    r.raise_for_status()
    return r.json()


def _json_default(v):
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    return str(v)
