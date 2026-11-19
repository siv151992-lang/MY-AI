"""Path-safe file access inside one project directory."""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

IGNORED_DIRS = {".git", "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".venv",
                "venv", ".next", "dist", "build", ".cache", ".tox", ".idea", ".DS_Store"}
MAX_FILE_BYTES = 2 * 1024 * 1024        # largest file the editor / AI reads or writes
MAX_TREE_ENTRIES = 5000


class WorkspaceError(ValueError):
    """User-facing error: bad path, missing file, ambiguous edit…"""


def normalize(rel: str) -> str:
    """Clean a user/AI supplied relative path, rejecting anything that could escape the project or touch .git."""
    rel = (rel or "").replace("\\", "/").strip()
    while rel.startswith("./"):
        rel = rel[2:]
    rel = rel.strip("/")
    if not rel or rel == ".":
        return ""
    p = PurePosixPath(rel)
    if p.is_absolute() or any(part in ("..", "") for part in p.parts):
        raise WorkspaceError(f"Invalid path: {rel!r} (use a path relative to the project root)")
    if p.parts[0] == ".git" or ".git" in p.parts:
        raise WorkspaceError("The .git directory is managed by Aria and cannot be edited directly")
    if any(ord(c) < 32 for c in rel):
        raise WorkspaceError("Invalid characters in path")
    return str(p)


@dataclass
class Workspace:
    root: Path

    def resolve(self, rel: str, *, must_exist: bool = False) -> Path:
        rel = normalize(rel)
        path = (self.root / rel) if rel else self.root
        # Resolve symlinks of existing parents and make sure we are still inside the project.
        real_root = self.root.resolve()
        probe = path
        while not probe.exists() and probe != self.root:
            probe = probe.parent
        if probe.exists() and real_root not in (probe.resolve(), *probe.resolve().parents):
            raise WorkspaceError(f"Path escapes the project: {rel}")
        if path.is_symlink():
            raise WorkspaceError(f"Symbolic links are not followed: {rel}")
        if must_exist and not path.exists():
            raise WorkspaceError(f"No such file or directory: {rel}")
        return path

    def rel(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    # ---------- reading ----------
    def tree(self, include_ignored: bool = False) -> list[dict]:
        """Flat, sorted list of {path, type, size} (directories first within each folder)."""
        out: list[dict] = []
        for d, dirs, files in os.walk(self.root):
            dirs[:] = sorted(x for x in dirs if x != ".git" and (include_ignored or x not in IGNORED_DIRS)
                             and not os.path.islink(os.path.join(d, x)))
            base = Path(d)
            for x in dirs:
                out.append({"path": self.rel(base / x), "type": "dir"})
            for f in sorted(files):
                p = base / f
                if p.is_symlink() or (not include_ignored and f in IGNORED_DIRS):
                    continue
                out.append({"path": self.rel(p), "type": "file", "size": p.stat().st_size})
            if len(out) > MAX_TREE_ENTRIES:
                break
        return sorted(out, key=lambda e: e["path"])[:MAX_TREE_ENTRIES]

    def files(self) -> list[str]:
        return [e["path"] for e in self.tree() if e["type"] == "file"]

    def read_bytes(self, rel: str) -> bytes:
        p = self.resolve(rel, must_exist=True)
        if p.is_dir():
            raise WorkspaceError(f"{rel} is a directory")
        if p.stat().st_size > MAX_FILE_BYTES:
            raise WorkspaceError(f"{rel} is larger than {MAX_FILE_BYTES // 1024 // 1024} MB")
        return p.read_bytes()

    def read_text(self, rel: str) -> str:
        data = self.read_bytes(rel)
        if is_binary(data):
            raise WorkspaceError(f"{rel} is a binary file")
        return data.decode("utf-8", errors="replace")

    def exists(self, rel: str) -> bool:
        return self.resolve(rel).exists()

    # ---------- writing (non-destructive: creates or modifies; git keeps the previous version) ----------
    def write_text(self, rel: str, content: str) -> dict:
        p = self.resolve(rel)
        if p.is_dir():
            raise WorkspaceError(f"{rel} is a directory")
        data = content.encode("utf-8")
        if len(data) > MAX_FILE_BYTES:
            raise WorkspaceError("File is too large")
        before = p.read_text("utf-8", errors="replace") if p.exists() else None
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return {"path": normalize(rel), "created": before is None, **line_delta(before or "", content)}

    def write_bytes(self, rel: str, data: bytes) -> None:
        p = self.resolve(rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    def mkdir(self, rel: str) -> None:
        self.resolve(rel).mkdir(parents=True, exist_ok=True)

    # ---------- destructive (callers must have the user's approval) ----------
    def delete(self, rel: str) -> list[str]:
        """Delete a file or directory; returns the files removed."""
        rel = normalize(rel)
        if not rel:
            raise WorkspaceError("Refusing to delete the project root")
        p = self.resolve(rel, must_exist=True)
        if p.is_dir():
            removed = [self.rel(Path(d) / f) for d, _, fs in os.walk(p) for f in fs]
            shutil.rmtree(p)
        else:
            removed = [rel]
            p.unlink()
        return removed

    def move(self, src: str, dst: str) -> None:
        s, d = self.resolve(src, must_exist=True), self.resolve(dst)
        if not normalize(src) or not normalize(dst):
            raise WorkspaceError("Cannot move the project root")
        if d.exists():
            raise WorkspaceError(f"{dst} already exists")
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(s), str(d))


def is_binary(data: bytes) -> bool:
    return b"\0" in data[:8192]


def line_delta(before: str, after: str) -> dict:
    import difflib
    add = rem = 0
    for line in difflib.unified_diff(before.splitlines(), after.splitlines(), lineterm="", n=0):
        if line.startswith("+") and not line.startswith("+++"):
            add += 1
        elif line.startswith("-") and not line.startswith("---"):
            rem += 1
    return {"added": add, "removed": rem}


def apply_edit(text: str, old: str, new: str, replace_all: bool = False) -> tuple[str, int]:
    """Exact search-and-replace. `old` must occur exactly once unless replace_all."""
    if old == "":
        raise WorkspaceError("old_text is empty; use write_file to create or replace a whole file")
    n = text.count(old)
    if n == 0:
        # Give the model a useful hint: same text with different whitespace?
        squash = lambda s: " ".join(s.split())  # noqa: E731
        hint = " (it does exist with different whitespace/indentation — copy it exactly)" \
            if squash(old) and squash(old) in squash(text) else ""
        raise WorkspaceError(f"old_text not found in the file{hint}. Read the file again and copy the text exactly.")
    if n > 1 and not replace_all:
        raise WorkspaceError(f"old_text occurs {n} times; include more surrounding lines so it is unique, "
                             "or set replace_all")
    return text.replace(old, new) if replace_all else text.replace(old, new, 1), n
