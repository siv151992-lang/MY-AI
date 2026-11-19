"""End-to-end deep research run through the API with Postgres, a fake web and a scripted (misbehaving) model.

Needs TEST_DATABASE_URL (schema from db/init/01-schema.sh)."""
import json
import os
import time
import uuid

import pytest

DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="set TEST_DATABASE_URL to run Postgres integration tests")
H = {"X-Requested-With": "aria"}

PAGES = {
    "https://www.nih.gov/coffee": ("Coffee and health | NIH",
        "Moderate coffee consumption of three to four cups a day is associated with a lower risk of type 2 diabetes. "
        "Researchers followed 100,000 adults for ten years.\n\n" + "Background text. " * 40),
    "https://www.reuters.com/coffee-study": ("Study on coffee | Reuters",
        "A large study found that drinking three to four cups of coffee daily was linked to a lower risk of type 2 diabetes. "
        "However, the researchers said more than six cups a day raised blood pressure.\n\n" + "More text. " * 40),
    "https://health-blog.example/coffee": ("Coffee myths",
        "Coffee has no effect on diabetes risk at all, according to our editors.\n\n" + "Filler. " * 60),
    "https://paywalled.example/coffee": None,  # fails to load
    "https://www.cdc.gov/verify": ("CDC on caffeine",
        "Drinking more than six cups of coffee a day can raise blood pressure in some adults.\n\n" + "Detail. " * 50),
}


class FakeLLM:
    async def complete(self, *, model, system, messages, max_tokens=1000, **_):
        body = messages[-1]["content"][0]["text"]
        if system.startswith("You plan web research"):
            return json.dumps({"subquestions": [
                {"question": "Does coffee affect diabetes risk?", "queries": ["coffee diabetes risk", "coffee type 2 diabetes study"]},
                {"question": "What are the risks of high coffee intake?", "queries": ["too much coffee risks"]}]})
        if system.startswith("You extract evidence"):
            if "nih.gov" in body:
                return json.dumps({"claims": [
                    {"claim": "3-4 cups of coffee a day is associated with lower type 2 diabetes risk", "subquestion": 1, "importance": "high",
                     "quote": "Moderate coffee consumption of three to four cups a day is associated with a lower risk of type 2 diabetes"},
                    {"claim": "Coffee cures diabetes", "subquestion": 1, "importance": "high",
                     "quote": "Coffee is a proven cure for diabetes"}]})  # fabricated quote: must be discarded
            if "reuters.com" in body:
                return json.dumps({"claims": [
                    {"claim": "Three to four daily cups linked to lower type 2 diabetes risk", "subquestion": 1, "importance": "high",
                     "quote": "drinking three to four cups of coffee daily was linked to a lower risk of type 2 diabetes"},
                    {"claim": "More than six cups a day raised blood pressure", "subquestion": 2, "importance": "high",
                     "quote": "more than six cups a day raised blood pressure"}]})
            if "health-blog" in body:
                return json.dumps({"claims": [
                    {"claim": "Coffee has no effect on diabetes risk", "subquestion": 1, "importance": "medium",
                     "quote": "Coffee has no effect on diabetes risk at all"}]})
            return json.dumps({"claims": []})
        if system.startswith("You compare evidence"):
            ids = [int(x) for x in __import__("re").findall(r"^C(\d+)", body, __import__("re").M)]
            # claims arrive in source order: NIH (C1), Reuters (C2), blog (C4)
            return json.dumps({"groups": [{"claims": [f"C{ids[0]}", f"C{ids[1]}"], "summary": "3-4 cups lowers risk"}],
                               "conflicts": [{"topic": "effect on diabetes", "positions": [
                                   {"summary": "lower risk", "claims": [f"C{ids[0]}", f"C{ids[1]}"]},
                                   {"summary": "no effect", "claims": [f"C{ids[2]}"]}],
                                   "assessment": "NIH and Reuters (high reliability) outweigh an unsourced blog."}]})
        if system.startswith("You check whether"):
            return json.dumps({"verdict": "supports", "quote": "more than six cups of coffee a day can raise blood pressure"})
        if system.startswith("You write a detailed"):
            return ("## Executive summary\n- Three to four cups a day is linked to lower type 2 diabetes risk [S1, S2].\n"
                    "- Very high intake may raise blood pressure [S2].\n- Coffee is purple [S42].\n\n"
                    "## Key findings\n### Diabetes\nTwo high-reliability sources agree [S1][S2], one blog disagrees [S3].\n\n"
                    "## Comparison\n| Source | Finding |\n|---|---|\n| S1 | lower risk [S1] |\n| S3 | no effect [S3] |\n\n"
                    "## Conflicting information\nThe blog [S3] conflicts with NIH [S1].\n\n"
                    "## Confidence and limitations\nOne study cohort only.\n\n"
                    "## References\n1. Smith J. Fake Journal of Coffee (2020).\n")
        return "{}"


SEARCH = {
    "coffee diabetes risk": ["https://www.nih.gov/coffee", "https://health-blog.example/coffee", "https://paywalled.example/coffee"],
    "coffee type 2 diabetes study": ["https://www.reuters.com/coffee-study", "https://www.nih.gov/coffee"],
    "too much coffee risks": ["https://www.reuters.com/coffee-study"],
}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", DSN)
    monkeypatch.setenv("JWT_SECRET", "x" * 40)
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "up"))
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path / "ws"))
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "1000")
    from app.config import get_settings
    get_settings.cache_clear()
    from app.research import manager as mgr
    from app.research.web import FetchError, Page, SearchResult
    m = mgr.get_manager()

    async def search(q, n):
        if q in SEARCH:
            return [SearchResult(u, f"title {u}", "snippet") for u in SEARCH[q]]
        if "blood pressure" in q:  # verification search for the single-source claim
            return [SearchResult("https://www.reuters.com/coffee-study", "dup", ""), SearchResult("https://www.cdc.gov/verify", "CDC", "")]
        return []

    async def fetch(url):
        page = PAGES.get(url)
        if page is None:
            raise FetchError("HTTP 403")
        return Page(url, page[0], page[1])

    llm = FakeLLM()
    monkeypatch.setattr(m, "search_factory", lambda: search)
    monkeypatch.setattr(m, "fetch", fetch)
    monkeypatch.setattr(m, "models", lambda mid: ((llm, "w"), (llm, "u")))
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        c.post("/api/auth/signup", json={"email": f"r{uuid.uuid4().hex[:8]}@example.com", "name": "R", "password": "password1234"}, headers=H)
        c.manager = m
        yield c
    get_settings.cache_clear()


def _wait(client, rid, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(f"/api/research/{rid}").json()
        if r["status"] in ("completed", "failed", "cancelled") and not r["active"]:
            return r
        time.sleep(0.1)
    raise AssertionError("research run did not finish")


def test_full_run_is_grounded_and_honest(client):
    rid = client.post("/api/research", json={"question": "Is coffee good or bad for health?", "depth": "standard"}, headers=H).json()["id"]
    run = _wait(client, rid)
    assert run["status"] == "completed", run["error"]
    srcs = {s["url"]: s for s in run["sources"]}
    assert srcs["https://paywalled.example/coffee"]["status"] == "failed" and srcs["https://paywalled.example/coffee"]["n"] is None
    read = {s["n"]: s for s in run["sources"] if s["n"]}
    assert read[1]["domain"] == "nih.gov" and read[1]["reliability_label"] == "high"
    claims = {c["text"]: c for c in run["claims"]}
    assert "Coffee cures diabetes" not in claims                       # fabricated quote discarded
    assert run["stats"]["quotes_rejected"] == 1
    assert claims["3-4 cups of coffee a day is associated with lower type 2 diabetes risk"]["status"] == "conflicting"
    bp = claims["More than six cups a day raised blood pressure"]
    assert bp["status"] == "corroborated" and "Independently confirmed" in bp["note"]  # verification found the CDC page
    assert any(s["domain"] == "cdc.gov" and s["purpose"] == "verification" for s in run["sources"])
    assert run["analysis"]["conflicts"][0]["topic"] == "effect on diabetes"
    md = run["report"]
    assert "[S42]" not in md and "Coffee is purple [unsupported: cited a source that was not read]." in md
    assert "Fake Journal of Coffee" not in md                               # model's own reference list dropped
    assert "1 citation to sources that were never read was removed" in md
    for s in read.values():
        assert s["url"] in md                                               # every listed source is a page actually read
    assert "paywalled.example" in md and "could not be read" in md          # ...and failures are disclosed
    assert "| C" in md and "## Source comparison" in md
    assert run["summary"].startswith("- Three to four cups")
    types = [e["data"].get("status") for e in _events(client, rid) if e["type"] == "phase"]
    assert types == ["planning", "searching", "reading", "extracting", "cross_checking", "verifying", "writing"]
    exp = client.get(f"/api/research/{rid}/export?format=md")
    assert exp.text == md and "attachment" in exp.headers["content-disposition"]
    pdf = client.get(f"/api/research/{rid}/export?format=pdf")
    assert pdf.content.startswith(b"%PDF-") and pdf.headers["content-type"] == "application/pdf"
    assert any(r["id"] == rid for r in client.get("/api/research").json())


def _events(client, rid):
    text = client.get(f"/api/research/{rid}/events").text
    return [json.loads(l[6:]) for l in text.splitlines() if l.startswith("data: {\"seq\"")]


def test_refuses_without_search_provider(client, monkeypatch):
    monkeypatch.setattr(client.manager, "search_factory", lambda: None)
    cfg = client.get("/api/research/config").json()
    assert cfg["available"] is False and "TAVILY_API_KEY" in cfg["reason"]
    r = client.post("/api/research", json={"question": "Anything at all?"}, headers=H)
    assert r.status_code == 503


def test_failed_searches_produce_no_report(client, monkeypatch):
    async def broken(q, n):
        raise RuntimeError("provider down")
    monkeypatch.setattr(client.manager, "search_factory", lambda: broken)
    rid = client.post("/api/research", json={"question": "What is the speed of light?", "depth": "quick"}, headers=H).json()["id"]
    run = _wait(client, rid)
    assert run["status"] == "failed" and "no research was performed" in run["error"] and run["report"] == ""
    assert client.get(f"/api/research/{rid}/export").status_code == 409
    assert any(e["type"] == "search" and not e["data"]["ok"] for e in _events(client, rid))


def test_unreadable_pages_produce_no_report(client, monkeypatch):
    async def fetch(url):
        from app.research.web import FetchError
        raise FetchError("HTTP 403")
    monkeypatch.setattr(client.manager, "fetch", fetch)
    rid = client.post("/api/research", json={"question": "Is coffee good or bad for health?", "depth": "quick"}, headers=H).json()["id"]
    run = _wait(client, rid)
    assert run["status"] == "failed" and "could be read" in run["error"] and run["report"] == ""
