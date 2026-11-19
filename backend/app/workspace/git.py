"""Hardened git wrapper.

Everything runs with hooks, fsmonitor and external diff/filter drivers disabled, never prompts for
credentials, and never lets a repository's own config execute programs. Credentials for push are
passed per call through an environment-only header and are never written to disk.
"""
from __future__ import annotations

import asyncio
import base64
import os
import re
from dataclasses import dataclass
from pathlib import Path

from .fs import WorkspaceError, normalize

SAFE_CONFIG = [
    "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", "-c", "core.sshCommand=false",
    "-c", "protocol.allow=never", "-c", "protocol.https.allow=always", "-c", "diff.external=",
    "-c", "core.pager=cat", "-c", "credential.helper=", "-c", "core.quotepath=false",
    "-c", "advice.detachedHead=false", "-c", "init.defaultBranch=main", "-c", "safe.directory=*",
]
CHECKPOINT_REF = "refs/aria/checkpoints"
BRANCH_RE = re.compile(r"^(?!-)(?!.*\.\.)(?!.*//)[A-Za-z0-9._/-]{1,100}(?<![./])$")


class GitError(WorkspaceError):
    pass


@dataclass
class GitResult:
    code: int
    out: str
    err: str


async def git(root: Path, *args: str, env: dict | None = None, check: bool = True, timeout: float = 60,
              input_: bytes | None = None) -> GitResult:
    e = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(root.parent), "GIT_TERMINAL_PROMPT": "0",
         "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null", "LANG": "C.UTF-8",
         "GIT_AUTHOR_NAME": "Aria", "GIT_AUTHOR_EMAIL": "aria@localhost",
         "GIT_COMMITTER_NAME": "Aria", "GIT_COMMITTER_EMAIL": "aria@localhost", **(env or {})}
    if os.name == "nt":  # Windows needs these to start programs and find its own files
        e.update({k: os.environ[k] for k in ("SYSTEMROOT", "COMSPEC", "PATHEXT", "TEMP", "TMP") if k in os.environ})
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", *SAFE_CONFIG, *args, cwd=str(root), env=e, stdin=asyncio.subprocess.PIPE if input_ else None,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    except FileNotFoundError:
        raise GitError("Git is not installed on the server, so code workspaces are unavailable. Install Git and restart.")
    try:
        out, err = await asyncio.wait_for(proc.communicate(input_), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise GitError(f"git {args[0]} timed out")
    res = GitResult(proc.returncode or 0, out.decode("utf-8", "replace"), err.decode("utf-8", "replace"))
    if check and res.code != 0:
        raise GitError(f"git {args[0]} failed: {res.err.strip() or res.out.strip()}"[:2000])
    return res


async def init(root: Path, author: tuple[str, str] | None = None, message: str = "Initial commit") -> None:
    root.mkdir(parents=True, exist_ok=True)
    await git(root, "init", "-q")
    await commit_all(root, message, author=author, allow_empty=True)


def _author_env(author: tuple[str, str] | None) -> dict:
    if not author:
        return {}
    name, email = author
    return {"GIT_AUTHOR_NAME": name or "Aria user", "GIT_AUTHOR_EMAIL": email or "user@localhost"}


async def has_commits(root: Path) -> bool:
    return (await git(root, "rev-parse", "--verify", "-q", "HEAD", check=False)).code == 0


async def status(root: Path) -> dict:
    """Branch info and changed files (working tree + index vs HEAD)."""
    r = await git(root, "status", "--porcelain=v1", "-z", "--branch", "--untracked-files=all")
    entries = r.out.split("\0")
    branch, ahead, behind = "", 0, 0
    files = []
    i = 0
    while i < len(entries):
        e = entries[i]
        i += 1
        if not e:
            continue
        if e.startswith("## "):
            head = e[3:]
            m = re.match(r"(?:No commits yet on )?([^.\s]+)(?:\.\.\.(\S+))?(?: \[(.*)\])?", head)
            if m:
                branch = m.group(1)
                for part in (m.group(3) or "").split(", "):
                    if part.startswith("ahead "):
                        ahead = int(part[6:])
                    elif part.startswith("behind "):
                        behind = int(part[7:])
            continue
        xy, path = e[:2], e[3:]
        orig = None
        if xy[0] in "RC":
            orig = entries[i]
            i += 1
        code = xy.strip() or "?"
        kind = ("untracked" if xy == "??" else "added" if "A" in xy else "deleted" if "D" in xy
                else "renamed" if "R" in xy else "modified")
        files.append({"path": path, "status": kind, "code": code, "from": orig})
    remote = (await git(root, "remote", "get-url", "origin", check=False)).out.strip() or None
    return {"branch": branch, "ahead": ahead, "behind": behind, "files": files, "clean": not files,
            "remote": _strip_credentials(remote) if remote else None}


def _strip_credentials(url: str) -> str:
    return re.sub(r"//[^/@]+@", "//", url)


async def diff(root: Path, path: str | None = None, *, rev: str | None = None, context: int = 3) -> str:
    """Unified diff of the working tree (including untracked files) against HEAD or `rev`."""
    paths = [normalize(path)] if path else []
    base = rev or ("HEAD" if await has_commits(root) else None)
    # Make untracked files visible to `git diff` without staging their content.
    await git(root, "add", "--intent-to-add", "--", *(paths or ["."]), check=False)
    args = ["diff", "--no-color", "--no-ext-diff", f"-U{int(context)}", "--find-renames"]
    if base:
        args.append(base)
    r = await git(root, *args, "--", *paths)
    return r.out


async def numstat(root: Path) -> dict[str, tuple[int, int]]:
    if not await has_commits(root):
        return {}
    await git(root, "add", "--intent-to-add", "--", ".", check=False)
    r = await git(root, "diff", "--numstat", "--no-renames", "HEAD")
    out = {}
    for line in r.out.splitlines():
        a, d, p = line.split("\t", 2)
        out[p] = (int(a) if a.isdigit() else 0, int(d) if d.isdigit() else 0)
    return out


async def log(root: Path, limit: int = 50, path: str | None = None) -> list[dict]:
    if not await has_commits(root):
        return []
    fmt = "%x1e%H%x1f%h%x1f%an%x1f%ae%x1f%aI%x1f%s"  # record separator first: --shortstat follows each header
    args = ["log", f"-n{int(limit)}", f"--format={fmt}", "--shortstat"]
    if path:
        args += ["--", normalize(path)]
    r = await git(root, *args)
    commits = []
    for chunk in r.out.split("\x1e"):
        chunk = chunk.strip()
        if not chunk:
            continue
        head, _, stat = chunk.partition("\n")
        parts = head.split("\x1f")
        if len(parts) < 6:
            continue
        commits.append({"sha": parts[0], "short": parts[1], "author": parts[2], "email": parts[3],
                        "date": parts[4], "message": parts[5], "stat": stat.strip()})
    return commits


async def show(root: Path, sha: str) -> str:
    if not re.fullmatch(r"[0-9a-fA-F]{4,40}", sha):
        raise GitError("Invalid commit id")
    return (await git(root, "show", "--no-color", "--no-ext-diff", "--format=%H%n%an <%ae>%n%aI%n%n%B", sha)).out


async def commit_all(root: Path, message: str, *, paths: list[str] | None = None,
                     author: tuple[str, str] | None = None, allow_empty: bool = False) -> dict | None:
    message = message.strip()
    if not message:
        raise GitError("A commit message is required")
    clean = [normalize(p) for p in paths] if paths else []
    await git(root, "add", "-A", "--", *(clean or ["."]))
    staged = await git(root, "diff", "--cached", "--quiet", check=False)
    if staged.code == 0 and not allow_empty:
        return None
    args = ["commit", "-q", "-m", message] + (["--allow-empty"] if allow_empty else [])
    if clean:
        args += ["--", *clean]
    await git(root, *args, env=_author_env(author))
    return (await log(root, 1))[0]


async def discard(root: Path, paths: list[str]) -> list[str]:
    """Restore files to HEAD (deleting files that HEAD doesn't have). Destructive: needs approval."""
    done = []
    has_head = await has_commits(root)
    for p in [normalize(x) for x in paths]:
        if not p:
            continue
        tracked = has_head and (await git(root, "cat-file", "-e", f"HEAD:{p}", check=False)).code == 0
        if tracked:
            await git(root, "checkout", "HEAD", "--", p)
        else:
            await git(root, "rm", "-q", "--cached", "-f", "--", p, check=False)
            fp = root / p
            if fp.is_file():
                fp.unlink()
        done.append(p)
    return done


# ---------- branches ----------
async def branches(root: Path) -> dict:
    r = await git(root, "branch", "--format=%(refname:short)%09%(HEAD)", check=False)
    items = []
    current = None
    for line in r.out.splitlines():
        name, _, head = line.partition("\t")
        items.append(name)
        if head.strip() == "*":
            current = name
    return {"current": current or (await status(root))["branch"], "branches": items}


def check_branch(name: str) -> str:
    if not BRANCH_RE.match(name or ""):
        raise GitError("Invalid branch name")
    return name


async def create_branch(root: Path, name: str, checkout: bool = True) -> None:
    check_branch(name)
    await git(root, *(["checkout", "-q", "-b", name] if checkout else ["branch", name]))


async def checkout(root: Path, name: str) -> None:
    check_branch(name)
    if not (await status(root))["clean"]:
        raise GitError("Commit or discard your changes before switching branches")
    await git(root, "checkout", "-q", name)


# ---------- checkpoints (undo for AI turns) ----------
async def checkpoint(root: Path, label: str) -> str | None:
    """Snapshot the whole working tree (incl. untracked files) without touching the index or branch."""
    index = root.parent / f".aria-index-{os.getpid()}-{id(label)}"
    env = {"GIT_INDEX_FILE": str(index)}
    try:
        await git(root, "add", "-A", "--", ".", env=env)
        tree = (await git(root, "write-tree", env=env)).out.strip()
    finally:
        index.unlink(missing_ok=True)
    parent = ["-p", "HEAD"] if await has_commits(root) else []
    sha = (await git(root, "commit-tree", tree, *parent, "-m", label[:200])).out.strip()
    n = len(await list_checkpoints(root))
    await git(root, "update-ref", f"{CHECKPOINT_REF}/{n:05d}", sha)
    return sha


async def list_checkpoints(root: Path) -> list[dict]:
    r = await git(root, "for-each-ref", "--sort=-refname", "--format=%(refname)%09%(objectname)%09%(creatordate:iso-strict)%09%(subject)",
                  CHECKPOINT_REF, check=False)
    out = []
    for line in r.out.splitlines():
        ref, sha, date, subject = (line.split("\t", 3) + ["", "", ""])[:4]
        out.append({"id": ref.rsplit("/", 1)[-1], "sha": sha, "date": date, "label": subject})
    return out


async def restore_checkpoint(root: Path, sha: str) -> list[str]:
    """Make the working tree match a checkpoint exactly (index and branch untouched). Destructive: needs approval.
    Returns the files that changed."""
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise GitError("Invalid checkpoint")
    current = await checkpoint(root, "Before restoring a checkpoint")  # so the restore itself can be undone
    changed = (await git(root, "diff", "--name-only", "--no-renames", current, sha)).out.split()
    gone = (await git(root, "diff", "--name-only", "--diff-filter=A", "--no-renames", sha, current)).out.split()
    for p in gone:  # files created after the checkpoint
        fp = root / p
        if fp.is_file():
            fp.unlink()
    restore = [p for p in changed if p not in gone]
    if restore:
        await git(root, "--work-tree", str(root), "restore", "--source", sha, "--worktree", "--", *restore)
    return changed


async def changed_since(root: Path, sha: str) -> list[dict]:
    """Files whose content differs between a checkpoint and the current working tree."""
    now = await checkpoint_tree(root)
    r = await git(root, "diff", "--name-status", "--no-renames", sha, now)
    out = []
    for line in r.out.splitlines():
        st, _, p = line.partition("\t")
        out.append({"path": p, "status": {"A": "added", "D": "deleted"}.get(st, "modified")})
    return out


async def checkpoint_tree(root: Path) -> str:
    index = root.parent / f".aria-index-tree-{os.getpid()}"
    env = {"GIT_INDEX_FILE": str(index)}
    try:
        await git(root, "add", "-A", "--", ".", env=env)
        return (await git(root, "write-tree", env=env)).out.strip()
    finally:
        index.unlink(missing_ok=True)


# ---------- remotes ----------
def check_remote_url(url: str) -> str:
    from urllib.parse import urlparse
    u = urlparse(url)
    if u.scheme != "https" or not u.hostname or u.username or u.password:
        raise GitError("Only https:// remote URLs without embedded credentials are supported")
    return url


async def clone(url: str, dest: Path, depth: int | None = 50) -> None:
    from ..agent.tools.web import is_public_url
    check_remote_url(url)
    if not await asyncio.to_thread(is_public_url, url):
        raise GitError("Refused: the repository URL is not a public address")
    dest.parent.mkdir(parents=True, exist_ok=True)
    args = ["clone", "-q", "--no-recurse-submodules", "-c", "core.hooksPath=/dev/null"]
    if depth:
        args += ["--depth", str(int(depth))]
    await git(dest.parent, *args, "--", url, str(dest), timeout=300)


async def set_remote(root: Path, url: str) -> None:
    check_remote_url(url)
    has = (await git(root, "remote", "get-url", "origin", check=False)).code == 0
    await git(root, "remote", "set-url" if has else "add", "origin", url)


async def push(root: Path, *, branch: str | None = None, token: str | None = None, username: str = "x-access-token") -> str:
    """Push the current branch to origin. The token is sent as an HTTP header via environment-only config."""
    from ..agent.tools.web import is_public_url
    remote = (await git(root, "remote", "get-url", "origin", check=False)).out.strip()
    if not remote:
        raise GitError("No remote is configured. Set a remote URL first.")
    check_remote_url(remote)
    if not await asyncio.to_thread(is_public_url, remote):
        raise GitError("Refused: the remote is not a public address")
    branch = check_branch(branch or (await status(root))["branch"])
    env = {}
    if token:
        cred = base64.b64encode(f"{username}:{token}".encode()).decode()
        env = {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "http.extraHeader",
               "GIT_CONFIG_VALUE_0": f"Authorization: Basic {cred}"}
    r = await git(root, "push", "--porcelain", "origin", f"HEAD:refs/heads/{branch}", env=env, timeout=180)
    return (r.out + r.err).replace(token or "\0", "***")
