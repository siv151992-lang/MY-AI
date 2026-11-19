"""Codebase understanding: a compact repo map (top-level symbols per file) and code search."""
from __future__ import annotations

import ast
import math
import re
from collections import Counter

from .fs import Workspace, WorkspaceError, is_binary

CODE_EXT = {".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".go", ".rs", ".java", ".kt", ".rb", ".php",
            ".cs", ".c", ".h", ".cpp", ".hpp", ".swift", ".scala", ".sql", ".sh", ".vue", ".svelte"}
TEXT_EXT = CODE_EXT | {".md", ".txt", ".json", ".yml", ".yaml", ".toml", ".ini", ".cfg", ".html", ".css", ".scss",
                       ".env.example", ".xml", ".graphql", ".proto", ".dockerfile", ""}
MAX_SEARCH_FILE = 512 * 1024

_SYMBOL_PATTERNS = [
    re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)", re.M),
    re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+([A-Za-z_$][\w$]*)", re.M),
    re.compile(r"^\s*(?:export\s+)?(?:const|let)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>", re.M),
    re.compile(r"^\s*export\s+(?:interface|type|enum)\s+([A-Za-z_$][\w$]*)", re.M),
    re.compile(r"^func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)", re.M),                          # Go
    re.compile(r"^\s*(?:pub\s+)?(?:fn|struct|enum|trait)\s+([A-Za-z_]\w*)", re.M),          # Rust
    re.compile(r"^\s*(?:public|private|protected)?\s*(?:static\s+)?(?:class|interface|enum)\s+([A-Za-z_]\w*)", re.M),
    re.compile(r"^\s*def\s+([A-Za-z_]\w*[?!]?)", re.M),                                     # Ruby
]


def _ext(path: str) -> str:
    name = path.rsplit("/", 1)[-1].lower()
    if name == "dockerfile":
        return ".dockerfile"
    return "." + name.rsplit(".", 1)[-1] if "." in name else ""


def python_symbols(src: str) -> list[str]:
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    out = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.append(f"def {node.name}({', '.join(a.arg for a in node.args.args)})")
        elif isinstance(node, ast.ClassDef):
            methods = [n.name for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                       and not n.name.startswith("__")]
            out.append(f"class {node.name}" + (f" [{', '.join(methods[:12])}]" if methods else ""))
    return out


def symbols(path: str, src: str) -> list[str]:
    if path.endswith(".py"):
        return python_symbols(src)
    if _ext(path) not in CODE_EXT:
        return []
    found: list[str] = []
    for pat in _SYMBOL_PATTERNS:
        for m in pat.finditer(src):
            if m.group(1) not in found:
                found.append(m.group(1))
    return found[:40]


def repo_map(ws: Workspace, max_chars: int = 6000) -> str:
    """One line per file with its top-level symbols, trimmed to fit `max_chars`."""
    lines = []
    for e in ws.tree():
        if e["type"] != "file":
            continue
        path = e["path"]
        syms: list[str] = []
        if _ext(path) in CODE_EXT and e.get("size", 0) <= MAX_SEARCH_FILE:
            try:
                syms = symbols(path, ws.read_text(path))
            except WorkspaceError:
                pass
        lines.append(f"{path}" + (f": {', '.join(syms)}" if syms else ""))
    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[:max_chars].rsplit("\n", 1)[0] + f"\n… ({len(lines)} files in total; use list_files to see more)"
    return text


def _text_files(ws: Workspace, glob: str | None = None):
    import fnmatch
    for e in ws.tree():
        if e["type"] != "file" or e.get("size", 0) > MAX_SEARCH_FILE or _ext(e["path"]) not in TEXT_EXT:
            continue
        if glob and not fnmatch.fnmatch(e["path"], glob):
            continue
        try:
            data = ws.read_bytes(e["path"])
        except WorkspaceError:
            continue
        if not is_binary(data):
            yield e["path"], data.decode("utf-8", errors="replace")


def grep(ws: Workspace, pattern: str, *, regex: bool = False, ignore_case: bool = True, glob: str | None = None,
         limit: int = 80) -> list[dict]:
    flags = re.I if ignore_case else 0
    try:
        rx = re.compile(pattern if regex else re.escape(pattern), flags)
    except re.error as e:
        raise WorkspaceError(f"Invalid regular expression: {e}")
    hits = []
    for path, text in _text_files(ws, glob):
        for n, line in enumerate(text.splitlines(), 1):
            if rx.search(line):
                hits.append({"path": path, "line": n, "text": line.strip()[:240]})
                if len(hits) >= limit:
                    return hits
    return hits


_WORD = re.compile(r"[A-Za-z][A-Za-z0-9]+")


_STOP = {"the", "and", "for", "are", "was", "were", "where", "what", "which", "who", "how", "why", "when", "this",
         "that", "with", "from", "into", "does", "did", "has", "have", "can", "not", "all", "any", "get", "set", "use",
         "used", "code", "file", "files", "function", "find", "show", "there", "here", "its", "our", "your"}
_SUFFIXES = ("ations", "ation", "ings", "ing", "ers", "er", "ed", "es", "s", "e")


def _stem(w: str) -> str:
    for suf in _SUFFIXES:
        if len(w) - len(suf) >= 3 and w.endswith(suf):
            return w[: -len(suf)]
    return w


def _terms(text: str) -> list[str]:
    out = []
    for w in _WORD.findall(text):
        # split camelCase / snake_case so "getUserName" matches "user name"
        for part in re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+", w):
            p = part.lower()
            if len(p) > 2 and p not in _STOP:
                out.append(_stem(p))
    return out


def relevant_files(ws: Workspace, query: str, k: int = 8) -> list[dict]:
    """BM25 ranking of files for a natural-language query, with path and symbol names weighted up."""
    q = set(_terms(query))
    if not q:
        return []
    docs = []
    for path, text in _text_files(ws):
        tf = Counter(_terms(text))
        boost = Counter(_terms(path) * 3 + _terms(" ".join(symbols(path, text))) * 2)
        docs.append((path, tf + boost, sum(tf.values()) or 1, text))
    if not docs:
        return []
    avg = sum(d[2] for d in docs) / len(docs)
    df = Counter(t for _, tf, _, _ in docs for t in q if tf.get(t))
    scored = []
    for path, tf, length, text in docs:
        s = 0.0
        for t in q:
            f = tf.get(t, 0)
            if not f:
                continue
            idf = math.log(1 + (len(docs) - df[t] + 0.5) / (df[t] + 0.5))
            s += idf * f * 2.2 / (f + 1.2 * (0.25 + 0.75 * length / avg))
        if s > 0:
            lines = [f"{n}: {ln.strip()[:160]}" for n, ln in enumerate(text.splitlines(), 1)
                     if q & set(_terms(ln))][:4]
            scored.append({"path": path, "score": round(s, 3), "matches": lines})
    scored.sort(key=lambda x: -x["score"])
    return scored[:k]
