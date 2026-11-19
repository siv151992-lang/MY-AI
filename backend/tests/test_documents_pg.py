"""Document intelligence end to end: API, Postgres, access control between users, RAG, extraction, analysis.

Needs TEST_DATABASE_URL (schema from db/init/01-schema.sh)."""
import io
import json
import os
import sys
import threading
import time
import uuid
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from . import doc_fixtures as fx

DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="set TEST_DATABASE_URL to run Postgres integration tests")
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "sandbox"))
os.environ.setdefault("SANDBOX_TOKEN", "test-token")  # the sandbox module reads it at import time
import server as sandbox_server  # noqa: E402

H = {"X-Requested-With": "aria"}


class ScriptedLLM:
    async def complete(self, *, model, system, messages, max_tokens=1000, **_):
        body = messages[-1]["content"][0]["text"]
        if system.startswith("Summarise the document"):
            return "A short summary of " + body.split("\n", 1)[0]
        if system.startswith("Answer the user's question"):
            return "Invoices are due within 30 days [P1]. Late fees are 5% [P9]."
        if system.startswith("Compare the documents"):
            return "## Comparison\n| Aspect | A | B |\n|---|---|---|\n| Terms | 30 days [P1] | n/a [P2] |"
        if system.startswith("Extract the requested fields"):
            return json.dumps({"fields": {
                "payment_terms": {"value": "30 days", "quote": "Invoices are due within 30 days."},
                "parties": {"value": "Acme and Globex", "quote": "Agreement between Acme and Globex"},  # not in the doc
                "governing_law": {"value": None, "quote": None}}})
        if system.startswith("You answer questions about tabular data"):
            return "```python\nimport pandas as pd\ndf = pd.read_csv('t0_sales.csv')\nprint('Q1 total:', df[df.Region != 'Total'].Q1.sum())\n```"
        if system.startswith("You explain the result"):
            return "Q1 total is 180." if "180" in body else "The analysis failed."
        if system.startswith("You produce documents"):
            return json.dumps({"title": "Payment summary", "sections": [{"heading": "Terms", "paragraphs": ["Due in 30 days [P1]."]}]})
        return ""


@pytest.fixture()
def client(tmp_path, monkeypatch):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), sandbox_server.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    for k, v in {"DATABASE_URL": DSN, "JWT_SECRET": "x" * 40, "UPLOAD_DIR": str(tmp_path / "up"), "WORKSPACE_ROOT": str(tmp_path / "ws"),
                 "SANDBOX_URL": f"http://127.0.0.1:{srv.server_address[1]}", "SANDBOX_TOKEN": sandbox_server.TOKEN,
                 "RATE_LIMIT_PER_MINUTE": "1000"}.items():
        monkeypatch.setenv(k, v)
    from app.config import get_settings
    from app.documents import service
    get_settings.cache_clear()
    llm = ScriptedLLM()
    monkeypatch.setattr(service, "_models", lambda: ((llm, "m"), (llm, "u")))
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        c.users = {}
        for who in ("alice", "bob", "carol"):
            email = f"{who}-{uuid.uuid4().hex[:6]}@example.com"
            assert c.post("/api/auth/signup", json={"email": email, "name": who.title(), "password": "password1234"}, headers=H).status_code == 200
            c.users[who] = email
        yield c
    srv.shutdown()
    get_settings.cache_clear()


def login(c, who):
    c.cookies.clear()
    assert c.post("/api/auth/login", json={"email": c.users[who], "password": "password1234"}, headers=H).status_code == 200


def upload(c, name, data):
    r = c.post("/api/documents", files={"file": (name, data, "application/octet-stream")}, headers=H)
    assert r.status_code == 200, r.text
    return r.json()


def test_formats_rag_extraction_analysis_and_generation(client):
    c = client
    login(c, "alice")
    terms = upload(c, "terms.docx", fx.docx_bytes())
    sheet = upload(c, "sales.xlsx", fx.xlsx_bytes())
    deck = upload(c, "deck.pptx", fx.pptx_bytes())
    csvd = upload(c, "people.csv", b"name,age\nAnn,31\nBo,45\n")
    txt = upload(c, "notes.txt", b"Meeting notes: ship v2 in April.")
    pdf = upload(c, "scan.pdf", fx.scanned_pdf("Contract signed on 12 March 2025"))
    img = upload(c, "receipt.png", fx.png_with_text("Invoice total 4,250 EUR"))
    assert [d["doc_type"] for d in (terms, sheet, deck, csvd, txt, pdf, img)] == ["docx", "xlsx", "pptx", "csv", "txt", "pdf", "image"]
    assert c.post("/api/documents", files={"file": ("x.exe", b"MZ\x90\x00", "application/octet-stream")}, headers=H).status_code == 415
    lib = {d["filename"]: d for d in c.get("/api/documents").json()}
    assert lib["sales.xlsx"]["tables"] == 1 and lib["terms.docx"]["owned"] is True
    detail = c.get(f"/api/documents/{pdf['id']}").json()
    assert detail["ocr"] is True and detail["pages"] == 1
    content = c.get(f"/api/documents/{deck['id']}/content").json()["chunks"]
    assert content[0]["page"] == 1 and content[0]["section"] == "Slide 1: Roadmap"
    for _ in range(50):  # auto-summary runs in the background
        if c.get(f"/api/documents/{terms['id']}").json()["summary"]:
            break
        time.sleep(0.05)
    assert c.get(f"/api/documents/{terms['id']}").json()["summary"].startswith("A short summary")

    # RAG with citations; citations to passages that weren't retrieved are flagged
    ans = c.post("/api/documents/ask", json={"question": "When are invoices due?", "document_ids": [terms["id"]]}, headers=H).json()
    assert "[P1]" in ans["answer"] and "[unsupported]" in ans["answer"] and ans["removed_citations"] == ["P9"]
    assert ans["citations"][0]["filename"] == "terms.docx" and "30 days" in ans["citations"][0]["snippet"]
    ocr_hit = c.post("/api/documents/ask", json={"question": "invoice total EUR"}, headers=H).json()
    assert any(ci["filename"] == "receipt.png" for ci in ocr_hit["citations"])  # OCR text is searchable

    cmp = c.post("/api/documents/compare", json={"document_ids": [terms["id"], txt["id"]]}, headers=H).json()
    assert "| Terms |" in cmp["comparison"] and {d["filename"] for d in cmp["documents"]} == {"terms.docx", "notes.txt"}

    ext = c.post(f"/api/documents/{terms['id']}/extract", json={"fields": [{"name": "payment_terms"}, {"name": "parties"},
                                                                          {"name": "governing_law"}]}, headers=H).json()
    st = {f["field"]: f["status"] for f in ext["fields"]}
    assert st == {"payment_terms": "verified", "parties": "unverified", "governing_law": "not_found"}
    csv_out = c.post(f"/api/documents/{terms['id']}/extract?format=csv", json={"preset": "contract"}, headers=H)
    assert csv_out.text.startswith("field,value,status,source_text")

    tbls = c.get(f"/api/documents/{sheet['id']}/tables").json()
    assert tbls[0]["profile"]["columns"][1]["type"] == "number"
    rows = c.get(f"/api/documents/{sheet['id']}/tables/{tbls[0]['id']}").json()["rows"]
    assert rows[0] == ["North", 100, 150]
    an = c.post(f"/api/documents/{sheet['id']}/analyze", json={"question": "What is total Q1 revenue?"}, headers=H).json()
    assert an["ok"] and "Q1 total: 180" in an["output"] and an["answer"] == "Q1 total is 180." and "read_csv" in an["code"]

    gen = c.post("/api/documents/generate", json={"prompt": "Summarise payment terms", "format": "docx",
                                                  "source_ids": [terms["id"]]}, headers=H).json()
    assert gen["filename"] == "Payment summary.docx" and gen["doc_type"] == "docx"
    g = c.get(f"/api/documents/{gen['id']}").json()
    assert g["meta"]["generated"] is True and g["meta"]["sources"][0]["filename"] == "terms.docx"
    dl = c.get(f"/api/documents/{gen['id']}/download")
    assert dl.content.startswith(b"PK") and "attachment" in dl.headers["content-disposition"]
    raw = c.get(f"/api/documents/{img['id']}/download")
    assert raw.headers["content-type"] == "image/png" and raw.headers["x-content-type-options"] == "nosniff"


def test_access_is_private_until_shared_and_viewers_cannot_manage(client):
    c = client
    login(c, "alice")
    secret = upload(c, "secret.docx", fx.docx_bytes())
    sid = secret["id"]
    login(c, "bob")
    # Bob can't see, read, download, search, ask, analyse or attach Alice's document
    assert c.get("/api/documents").json() == []
    for path in (f"/api/documents/{sid}", f"/api/documents/{sid}/content", f"/api/documents/{sid}/download",
                 f"/api/documents/{sid}/tables", f"/api/files/{sid}/content"):
        assert c.get(path).status_code == 404, path
    assert c.post(f"/api/documents/{sid}/summarize", json={}, headers=H).status_code == 404
    assert c.post(f"/api/documents/{sid}/extract", json={"preset": "invoice"}, headers=H).status_code == 404
    assert c.post("/api/documents/ask", json={"question": "invoices due", "document_ids": [sid]}, headers=H).json()["citations"] == []
    assert c.post("/api/documents/ask", json={"question": "invoices due"}, headers=H).json()["citations"] == []
    assert c.delete(f"/api/documents/{sid}", headers=H).status_code == 404
    assert c.post(f"/api/documents/{sid}/access", json={"email": c.users["bob"]}, headers=H).status_code == 404

    # Alice shares with Bob (viewer)
    login(c, "alice")
    assert c.post(f"/api/documents/{sid}/access", json={"email": "nobody@example.com"}, headers=H).status_code == 422
    g = c.post(f"/api/documents/{sid}/access", json={"email": c.users["bob"]}, headers=H).json()
    assert g["role"] == "viewer"
    login(c, "bob")
    lib = c.get("/api/documents").json()
    assert lib[0]["id"] == sid and lib[0]["owned"] is False and lib[0]["owner_name"] == "Alice"
    assert c.get(f"/api/documents/{sid}").json()["access"] is None          # viewers don't see the share list
    assert c.get(f"/api/documents/{sid}/download").status_code == 200
    hits = c.post("/api/documents/ask", json={"question": "When are invoices due?"}, headers=H).json()["citations"]
    assert hits and hits[0]["file_id"] == sid
    assert c.patch(f"/api/documents/{sid}", json={"filename": "mine.docx"}, headers=H).status_code == 403
    assert c.post(f"/api/documents/{sid}/reindex", headers=H).status_code == 403
    assert c.post(f"/api/documents/{sid}/access", json={"email": c.users["carol"]}, headers=H).status_code == 403
    # a viewer "deleting" only removes it from their own library
    assert c.delete(f"/api/documents/{sid}", headers=H).json() == {"ok": True, "deleted": False}
    assert c.get(f"/api/documents/{sid}").status_code == 404
    login(c, "carol")
    assert c.get(f"/api/documents/{sid}").status_code == 404                # never shared with Carol

    # Owner revokes and deletes; the file is gone from disk too
    import asyncio
    login(c, "alice")
    c.post(f"/api/documents/{sid}/access", json={"email": c.users["bob"]}, headers=H)
    assert len(c.get(f"/api/documents/{sid}/access").json()) == 1
    bob_id = c.get(f"/api/documents/{sid}/access").json()[0]["user_id"]
    assert c.delete(f"/api/documents/{sid}/access/{bob_id}", headers=H).json() == {"ok": True}
    path = asyncio.run(_storage_path(sid))
    assert Path(path).exists()
    assert c.delete(f"/api/documents/{sid}", headers=H).json()["deleted"] is True
    assert not Path(path).exists()


async def _storage_path(fid):
    import asyncpg
    conn = await asyncpg.connect(DSN)
    try:
        return await conn.fetchval("SELECT storage_path FROM files WHERE id=$1", fid)
    finally:
        await conn.close()


def test_chat_attachments_respect_access(client, monkeypatch):
    from app.llm import registry
    from app.llm.registry import ModelSpec
    seen = []

    class ChatLLM(ScriptedLLM):
        async def stream(self, *, model, system, messages, tools=None, max_tokens=4096, temperature=None, reasoning=None):
            seen.append(system + json.dumps(messages))
            yield {"type": "text", "text": "ok"}
            yield {"type": "done", "stop_reason": "end_turn", "usage": {}}

    llm = ChatLLM()
    spec = ModelSpec(id="fake", label="F", provider="fake", model="f", vision=False, tools=False, max_tokens=100, reasoning="none")
    monkeypatch.setattr(registry, "get_model", lambda *a, **k: spec)
    monkeypatch.setattr(registry, "utility_model", lambda: spec)
    monkeypatch.setattr(registry, "provider_for", lambda s: llm)
    c = client
    login(c, "alice")
    doc = upload(c, "private.txt", b"The launch code is in the blue safe.")
    login(c, "bob")
    conv = c.post("/api/conversations", json={}, headers=H).json()
    r = c.post(f"/api/conversations/{conv['id']}/messages", json={"content": "summarise this", "file_ids": [doc["id"]]}, headers=H)
    assert r.status_code == 200 and "event: done" in r.text
    assert seen and not any("blue safe" in s for s in seen)                 # Alice's text never reached Bob's prompt
    msgs = c.get(f"/api/conversations/{conv['id']}").json()["messages"]
    assert msgs[0]["file_ids"] == [] and msgs[0]["files"] == []
    # after sharing, the same attachment works
    login(c, "alice")
    c.post(f"/api/documents/{doc['id']}/access", json={"email": c.users["bob"]}, headers=H)
    login(c, "bob")
    seen.clear()
    c.post(f"/api/conversations/{conv['id']}/messages", json={"content": "and now?", "file_ids": [doc["id"]]}, headers=H)
    assert any("blue safe" in s for s in seen)
