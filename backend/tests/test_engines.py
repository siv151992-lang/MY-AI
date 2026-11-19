import asyncio
import io

import pytest

from app.agent import memory_engine, planner, verifier
from app.agent.jsonutil import extract_json
from app.agent.prompts import build_system_prompt
from app.agent.tools.base import Tool, ToolContext, ToolRegistry
from app.agent.tools.code import format_run_result
from app.agent.tools.web import html_to_text, is_public_url
from app.llm.formats import text_msg
from app.services.documents import UnsupportedFile, chunk_text, classify, extract_text


# ---------- planning ----------
def test_needs_plan_heuristics():
    assert planner.needs_plan("hi", agent_mode=True, has_documents=False)
    assert not planner.needs_plan("what is 2+2?", agent_mode=False, has_documents=False)
    assert planner.needs_plan("Research and compare the three most popular vector databases for production use",
                              agent_mode=False, has_documents=False)
    assert planner.needs_plan("word " * 130, agent_mode=False, has_documents=False)


def test_parse_plan_json_and_fallback():
    assert planner.parse_plan('Sure! {"steps": ["a", "b"]}') == ["a", "b"]
    assert planner.parse_plan("1. Search docs\n2) Run code\n- Answer") == ["Search docs", "Run code", "Answer"]


# ---------- verification ----------
def test_parse_verdict():
    assert verifier.parse_verdict('{"ok": true, "issues": []}') == {"ok": True, "issues": []}
    v = verifier.parse_verdict('{"ok": false, "issues": ["wrong number"]}')
    assert v == {"ok": False, "issues": ["wrong number"]}
    assert verifier.parse_verdict("garbage")["ok"] is True
    assert not verifier.should_verify(False, False, "x" * 500)
    assert verifier.should_verify(True, False, "x" * 500)


def test_extract_json_variants():
    assert extract_json('```json\n{"a":1}\n```') == {"a": 1}
    assert extract_json('text before [1,2] after') == [1, 2]
    assert extract_json("no json") is None


# ---------- memory ----------
def test_parse_facts_filters():
    assert memory_engine.parse_facts('{"facts": ["Works in Java", "x", "' + "y" * 400 + '"]}') == ["Works in Java"]


def test_is_duplicate():
    existing = [{"content": "Works in Java.", "score": 0.5}]
    assert memory_engine.is_duplicate("works in java", existing, vector_scores=False)
    assert not memory_engine.is_duplicate("Lives in Chennai", existing, vector_scores=False)
    assert memory_engine.is_duplicate("Codes in Java", [{"content": "x", "score": 0.95}], vector_scores=True)
    # ts_rank scores must not be mistaken for similarity
    assert not memory_engine.is_duplicate("Codes in Java", [{"content": "x", "score": 0.95}], vector_scores=False)


def test_split_history_keeps_recent_within_budget_and_starts_on_user():
    msgs = []
    for i in range(20):
        msgs += [text_msg("user", f"q{i} " + "w" * 400), text_msg("assistant", f"a{i} " + "w" * 400)]
    idx = memory_engine.split_history(msgs, budget=1000)
    assert 0 < idx < len(msgs)
    assert msgs[idx]["role"] == "user"
    assert sum(memory_engine.message_tokens(m) for m in msgs[idx:]) <= 1000 + 110
    assert memory_engine.split_history(msgs[:4], budget=100_000) == 0


def test_system_prompt_includes_context():
    s = build_system_prompt(user_name="Siva", memories=["Uses Java"], conversation_summary="Earlier: X",
                            documents=[{"filename": "a.txt", "full_text": "hello"},
                                       {"filename": "big.pdf", "excerpt": "start"}],
                            plan=["Search", "Answer"], tool_names=["web_search"])
    for needle in ("Siva", "Uses Java", "Earlier: X", '<document name="a.txt">', "search_documents",
                   "1. Search", "web_search"):
        assert needle in s


# ---------- tools ----------
def test_tool_registry_guards():
    async def boom(args, ctx):
        raise ValueError("bad")

    async def slow(args, ctx):
        await asyncio.sleep(5)
        return "late"

    async def big(args, ctx):
        return "x" * 50

    reg = ToolRegistry([Tool("boom", "", {"required": []}, boom), Tool("slow", "", {}, slow, timeout=0.05),
                        Tool("big", "", {"required": ["q"]}, big)])
    ctx = ToolContext("u", "c")
    run = lambda n, a: asyncio.run(reg.execute(n, a, ctx, max_chars=10))  # noqa: E731
    assert run("boom", {}) == ("Tool 'boom' failed: ValueError: bad", True)
    assert run("slow", {})[1] is True and "timed out" in run("slow", {})[0]
    assert run("big", {}) == ("Missing required argument(s): q", True)
    out, err = run("big", {"q": 1})
    assert not err and out.startswith("x" * 10) and "truncated 40" in out
    assert run("missing", {})[1] is True


def test_html_to_text_strips_noise():
    title, text = html_to_text("<html><head><title>T</title><style>.a{}</style></head><body><nav>menu</nav>"
                               "<h1>Head</h1><p>Para &amp; one</p><script>evil()</script><p>Two</p></body></html>")
    assert title == "T"
    assert "# Head" in text and "Para & one" in text and "Two" in text
    assert "evil" not in text and "menu" not in text


def test_ssrf_guard_blocks_private_targets():
    for url in ("http://127.0.0.1/", "http://localhost:8000", "http://10.0.0.5/x", "http://169.254.169.254/latest",
                "file:///etc/passwd", "ftp://example.com", "http://[::1]/"):
        assert not is_public_url(url), url


def test_format_run_result():
    assert "exit_code: 0" in format_run_result({"exit_code": 0, "stdout": "4\n", "stderr": ""})
    assert "print()" in format_run_result({"exit_code": 0, "stdout": "", "stderr": ""})


# ---------- documents ----------
def test_chunking_covers_text_with_overlap():
    text = "\n\n".join(f"Paragraph {i}. " + "lorem ipsum " * 40 for i in range(30))
    chunks = chunk_text(text, size=1200, overlap=200)
    assert len(chunks) > 5 and all(len(c) <= 1200 + 202 for c in chunks)
    for i in range(30):
        assert any(f"Paragraph {i}." in c for c in chunks)
    assert chunk_text("") == [] and chunk_text("short") == ["short"]
    giant = chunk_text("z" * 5000, size=1000, overlap=100)
    assert sum(len(c) for c in giant) >= 5000


def test_extract_text_and_classify(tmp_path):
    assert extract_text("a.py", b"print('hi')") == "print('hi')"
    with pytest.raises(UnsupportedFile):
        extract_text("a.exe", b"MZ\x90\x00")
    assert classify("x.txt", "text/plain", b"\x89PNG\r\n\x1a\n") == "image"  # magic bytes win over name/MIME
    assert classify("x.png", "image/png", b"hello") == "document"


def test_extract_pdf_text():
    pypdf = pytest.importorskip("pypdf")
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
    w = pypdf.PdfWriter()
    page = w.add_blank_page(300, 200)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
                             NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 20 100 Td (Quarterly revenue grew 12 percent) Tj ET")
    page[NameObject("/Contents")] = w._add_object(stream)
    buf = io.BytesIO()
    w.write(buf)
    text = extract_text("report.pdf", buf.getvalue())
    assert "[Page 1]" in text and "Quarterly revenue grew 12 percent" in text
