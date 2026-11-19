"""Isolated Python execution service (stdlib only).

Defence in depth:
  * runs in its own container on an internal-only Docker network (no internet), read-only root FS,
    all capabilities dropped, non-root user, pids/memory limits (see docker-compose.yml);
  * each run is a fresh `python -I` process in a throwaway temp dir with CPU, memory, file-size and
    process rlimits, a wall-clock timeout, a scrubbed environment and capped output;
  * bearer-token auth so only the backend can call it.
"""
from __future__ import annotations

import json
import os
import resource
import shutil
import subprocess
import sys
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
        if self.path != "/run":
            return self._send(404, {"error": "not found"})
        if TOKEN and self.headers.get("authorization") != f"Bearer {TOKEN}":
            return self._send(401, {"error": "unauthorized"})
        length = int(self.headers.get("content-length") or 0)
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

    def log_message(self, fmt, *args):  # keep code out of logs
        sys.stderr.write(f"{self.address_string()} {fmt % args}\n")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8800"))
    print(f"sandbox listening on :{port}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
