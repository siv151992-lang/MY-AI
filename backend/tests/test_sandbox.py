import json
import os
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "sandbox"))
os.environ["SANDBOX_TOKEN"] = "test-token"
import server  # noqa: E402


def test_runs_code_and_captures_output():
    r = server.run_code("import math\nprint(math.factorial(10))")
    assert r == {"exit_code": 0, "stdout": "3628800\n", "stderr": "", "timed_out": False}


def test_reports_exceptions():
    r = server.run_code("1/0")
    assert r["exit_code"] == 1 and "ZeroDivisionError" in r["stderr"]


def test_timeout_kills_infinite_loop():
    r = server.run_code("while True: pass", timeout=1)
    assert r["timed_out"] or r["exit_code"] != 0


def test_memory_limit_enforced():
    r = server.run_code("x = bytearray(2 * 1024 * 1024 * 1024)\nprint('allocated')")
    assert r["exit_code"] != 0 and "allocated" not in r["stdout"]


def test_environment_is_scrubbed_and_tempdir_removed():
    r = server.run_code("import os; print(sorted(k for k in os.environ if 'TOKEN' in k)); print(os.getcwd())")
    assert r["stdout"].splitlines()[0] == "[]"
    assert not Path(r["stdout"].splitlines()[1]).exists()


@pytest.fixture(scope="module")
def http_server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def _post(url, body, token="test-token"):
    req = urllib.request.Request(url + "/run", data=json.dumps(body).encode(), method="POST",
                                 headers={"content-type": "application/json", "authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_http_api_requires_token(http_server):
    assert _post(http_server, {"code": "print(1)"}, token="wrong")[0] == 401
    status, body = _post(http_server, {"code": "print(6*7)"})
    assert status == 200 and body["stdout"] == "42\n"
    assert _post(http_server, {"nope": 1})[0] == 400
