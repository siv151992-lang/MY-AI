"""Isolated code execution service (stdlib only).

  POST /run   run a Python snippet (the chat's run_python tool)
  POST /exec  run a shell command against a throwaway copy of a project (the coding workspace); returns the
              output plus every file the command created, changed or deleted, which the backend then applies

Defence in depth:
  * runs in its own container on an internal-only Docker network (no internet), read-only root FS,
    all capabilities dropped, non-root user, pids/memory limits (see docker-compose.yml);
  * each run is a fresh `python -I` process in a throwaway temp dir with CPU, memory, file-size and
    process rlimits, a wall-clock timeout, a scrubbed environment and capped output;
  * bearer-token auth so only the backend can call it.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import resource
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOKEN = os.environ.get("SANDBOX_TOKEN", "")
MAX_CODE = 100_000
MAX_OUTPUT = 20_000
MEM_BYTES = int(os.environ.get("SANDBOX_MEM_MB", "512")) * 1024 * 1024
CONCURRENCY = threading.BoundedSemaphore(int(os.environ.get("SANDBOX_CONCURRENCY", "4")))


def _limits(cpu_seconds: int):
    def apply():
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 1))
        resource.setrlimit(resource.RLIMIT_AS, (MEM_BYTES, MEM_BYTES))
        resource.setrlimit(resource.RLIMIT_FSIZE, (10 * 1024 * 1024, 10 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
        try:
            resource.setrlimit(resource.RLIMIT_NPROC, (32, 32))
        except (ValueError, OSError):
            pass
        os.setsid()
    return apply


def _cap(s: str) -> str:
    return s if len(s) <= MAX_OUTPUT else s[:MAX_OUTPUT] + f"\n…[truncated {len(s) - MAX_OUTPUT} chars]"


def run_code(code: str, timeout: int = 30) -> dict:
    timeout = max(1, min(int(timeout), 60))
    workdir = tempfile.mkdtemp(prefix="run-")
    try:
        path = os.path.join(workdir, "main.py")
        with open(path, "w") as f:
            f.write(code)
        env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": workdir, "TMPDIR": workdir,
               "MPLBACKEND": "Agg", "PYTHONIOENCODING": "utf-8", "OPENBLAS_NUM_THREADS": "1"}
        try:
            p = subprocess.run([sys.executable, "-I", "-B", path], cwd=workdir, env=env, capture_output=True,
                               text=True, timeout=timeout, preexec_fn=_limits(timeout))
            return {"exit_code": p.returncode, "stdout": _cap(p.stdout), "stderr": _cap(p.stderr), "timed_out": False}
        except subprocess.TimeoutExpired as e:
            out = e.stdout.decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
            err = e.stderr.decode() if isinstance(e.stderr, bytes) else (e.stderr or "")
            return {"exit_code": -9, "stdout": _cap(out), "stderr": _cap(err + f"\nTimed out after {timeout}s"),
                    "timed_out": True}
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# ---------------------------------------------------------------- project commands (/exec)
MAX_ARCHIVE = 40 * 1024 * 1024          # compressed project upload
MAX_FILE_OUT = 5 * 1024 * 1024          # per changed file returned
MAX_CHANGES_OUT = 25 * 1024 * 1024      # all changed files returned
IGNORED_DIRS = {".git", "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".venv",
                "venv", ".next", "dist", "build", ".cache", ".tox"}
SHELL = "/bin/bash" if os.path.exists("/bin/bash") else "/bin/sh"


def _exec_limits(cpu_seconds: int):
    def apply():
        # No RLIMIT_AS here: Node/V8 reserve large virtual ranges. Memory is capped by the container's cgroup.
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 1))
        resource.setrlimit(resource.RLIMIT_FSIZE, (50 * 1024 * 1024, 50 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))
        try:
            resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))
        except (ValueError, OSError):
            pass
        os.setsid()
    return apply


def _walk(root: str) -> dict[str, str]:
    """relative path -> sha256 of every regular file, skipping ignored directories and symlinks."""
    out: dict[str, str] = {}
    for d, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if x not in IGNORED_DIRS and not os.path.islink(os.path.join(d, x))]
        for f in files:
            p = os.path.join(d, f)
            if os.path.islink(p) or not os.path.isfile(p):
                continue
            h = hashlib.sha256()
            with open(p, "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 16), b""):
                    h.update(chunk)
            out[os.path.relpath(p, root)] = h.hexdigest()
    return out


def _safe_rel(path: str) -> bool:
    return bool(path) and not os.path.isabs(path) and ".." not in path.replace("\\", "/").split("/")


def _extract(archive_b64: str, dest: str) -> None:
    raw = base64.b64decode(archive_b64, validate=True)
    if len(raw) > MAX_ARCHIVE:
        raise ValueError("project archive too large")
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tf:
        for m in tf.getmembers():
            if not _safe_rel(m.name) or not (m.isfile() or m.isdir()):
                raise ValueError(f"unsafe archive member: {m.name}")
        tf.extractall(dest, filter="data")


def exec_command(archive_b64: str, command: str, timeout: int = 60, cwd: str = "") -> dict:
    """Run a shell command against a throwaway copy of a project and report the files it changed."""
    timeout = max(1, min(int(timeout), 300))
    base = tempfile.mkdtemp(prefix="exec-")
    try:
        project, home = os.path.join(base, "project"), os.path.join(base, "home")
        os.makedirs(project)
        os.makedirs(home)
        if archive_b64:
            _extract(archive_b64, project)
        workdir = project
        if cwd:
            if not _safe_rel(cwd):
                raise ValueError("cwd must be a relative path inside the project")
            workdir = os.path.join(project, cwd)
            if not os.path.isdir(workdir):
                raise ValueError(f"no such directory: {cwd}")
        before = _walk(project)
        # The sandbox's own interpreter comes first, so `python`/`pytest` match what is installed alongside it.
        path = os.pathsep.join(dict.fromkeys([os.path.dirname(sys.executable), "/usr/local/bin", "/usr/bin", "/bin"]))
        env = {"PATH": path, "HOME": home, "TMPDIR": home, "LANG": "C.UTF-8",
               "MPLBACKEND": "Agg", "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1",
               "OPENBLAS_NUM_THREADS": "1", "CI": "1", "NO_COLOR": "1", "TERM": "dumb",
               "npm_config_cache": os.path.join(home, ".npm"), "npm_config_offline": "true"}
        p = subprocess.Popen([SHELL, "-c", command], cwd=workdir, env=env, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, preexec_fn=_exec_limits(timeout))
        timed_out = False
        try:
            out, err = p.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            out, err = p.communicate()
        after = _walk(project)
        written: dict[str, str] = {}
        skipped: list[str] = []
        total = 0
        for rel, digest in sorted(after.items()):
            if before.get(rel) == digest:
                continue
            path = os.path.join(project, rel)
            size = os.path.getsize(path)
            if size > MAX_FILE_OUT or total + size > MAX_CHANGES_OUT:
                skipped.append(rel)
                continue
            total += size
            with open(path, "rb") as fh:
                written[rel] = base64.b64encode(fh.read()).decode()
        deleted = sorted(set(before) - set(after))
        stderr = err.decode("utf-8", "replace") + (f"\nTimed out after {timeout}s" if timed_out else "")
        return {"exit_code": -9 if timed_out else p.returncode, "stdout": _cap(out.decode("utf-8", "replace")),
                "stderr": _cap(stderr), "timed_out": timed_out,
                "changes": {"written": written, "deleted": deleted, "skipped": skipped}}
    finally:
        shutil.rmtree(base, ignore_errors=True)


class Handler(BaseHTTPRequestHandler):
    server_version = "AriaSandbox/1.0"

    def _send(self, status: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/health":
            return self._send(200, {"ok": True})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path not in ("/run", "/exec"):
            return self._send(404, {"error": "not found"})
        if TOKEN and self.headers.get("authorization") != f"Bearer {TOKEN}":
            return self._send(401, {"error": "unauthorized"})
        length = int(self.headers.get("content-length") or 0)
        if self.path == "/exec":
            return self._exec(length)
        if length <= 0 or length > MAX_CODE * 2:
            return self._send(413, {"error": "payload too large or empty"})
        try:
            req = json.loads(self.rfile.read(length))
            code = str(req["code"])
        except (ValueError, KeyError):
            return self._send(400, {"error": "expected JSON {code, timeout?}"})
        if len(code) > MAX_CODE:
            return self._send(413, {"error": "code too long"})
        if not CONCURRENCY.acquire(timeout=30):
            return self._send(503, {"error": "sandbox busy"})
        try:
            self._send(200, run_code(code, req.get("timeout", 30)))
        finally:
            CONCURRENCY.release()

    def _exec(self, length: int) -> None:
        if length <= 0 or length > MAX_ARCHIVE * 2:
            return self._send(413, {"error": "payload too large or empty"})
        try:
            req = json.loads(self.rfile.read(length))
            command = str(req["command"])
        except (ValueError, KeyError):
            return self._send(400, {"error": "expected JSON {command, archive?, timeout?, cwd?}"})
        if not command.strip() or len(command) > 20_000:
            return self._send(400, {"error": "command is empty or too long"})
        if not CONCURRENCY.acquire(timeout=30):
            return self._send(503, {"error": "sandbox busy"})
        try:
            self._send(200, exec_command(req.get("archive") or "", command, req.get("timeout", 60),
                                         str(req.get("cwd") or "")))
        except (ValueError, tarfile.TarError) as e:
            self._send(400, {"error": str(e)})
        finally:
            CONCURRENCY.release()

    def log_message(self, fmt, *args):  # keep code out of logs
        sys.stderr.write(f"{self.address_string()} {fmt % args}\n")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8800"))
    print(f"sandbox listening on :{port}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
