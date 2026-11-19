"""Integration tests for the memory system against real PostgreSQL + pgvector.

Run with a disposable database (schema from db/init/01-schema.sh):
    TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:55432/aria_test pytest tests/test_memory_pg.py
"""
import asyncio
import hashlib
import json
import math
import os
import re
import uuid

import pytest

from app import db
from app.llm.formats import text_msg
from app.memory import context, service, store
from app.memory.context import Budget

DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="set TEST_DATABASE_URL to run Postgres integration tests")
DIM = 1536
STOP = {"the", "a", "an", "is", "in", "and", "to", "of", "user", "user's", "with", "uses", "for"}


async def fake_embed(texts):
    """Deterministic bag-of-words embedding: texts sharing words have high cosine similarity."""
    out = []
    for t in texts:
        v = [0.0] * DIM
        for w in re.findall(r"[a-z0-9]+", t.lower()):
            if w in STOP:
                continue
            h = int(hashlib.md5(w.encode()).hexdigest(), 16)
            v[h % DIM] += 1.0
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        out.append([x / n for x in v])
    return out


class FakeLLM:
    def __init__(self, extract_reply="{}"):
        self.extract_reply = extract_reply
        self.calls = []

    async def complete(self, *, model, system, messages, max_tokens=1000, **_):
        body = messages[-1]["content"][0]["text"]
        self.calls.append(system[:30])
        if system.startswith("You curate long-term memory"):
            return self.extract_reply if isinstance(self.extract_reply, str) else self.extract_reply(body)
        if system.startswith("Summarise this part"):
            first = re.findall(r"USER: (\w+)", body)
            return "Segment covering " + " ".join(first) + ". Decided to use Postgres for storage."
        if system.startswith("You maintain the running summary"):
            return "Rolling summary: " + str(body.count("Segment covering")) + " sections."
        return ""


def run(fn, llm=None):
    """Each test gets its own event loop, pool and user."""
    async def main(monkeypatch_llm):
        await db.connect(DSN)
        try:
            await db.migrate(DIM)
            u = await db.create_user(f"t{uuid.uuid4().hex[:10]}@example.com", "Tester", "x")
            return await fn(u["id"])
        finally:
            await db.close()
    return asyncio.run(main(llm))


@pytest.fixture(autouse=True)
def fakes(monkeypatch):
    monkeypatch.setattr(service, "_embed", fake_embed)
    llm = FakeLLM()
    monkeypatch.setattr(service, "_utility", lambda: (llm, "fake"))
    return llm


def test_create_dedupe_reinforce_and_secret_block():
    async def t(uid):
        m = await service.create(uid, "Works as a backend developer using Java")
        assert m["status"] == "active" and m["source"] == "manual" and m["category"] == "profile"
        again = await service.create(uid, "works as a backend developer using java.")
        assert again["duplicate"] and again["id"] == m["id"] and again["importance"] > m["importance"]
        with pytest.raises(service.MemoryRejected, match="Secrets are never kept"):
            await service.create(uid, "My password is hunter2")
        revs = await store.revisions(uid, m["id"])
        assert [r["changed_by"] for r in revs] == ["user", "system"]
    run(t)


def test_hybrid_semantic_search_ranks_relevant_first():
    async def t(uid):
        await service.create(uid, "Prefers Python for data analysis work")
        await service.create(uid, "Has a dog named Biscuit")
        await service.create(uid, "Lives in Chennai, India")
        hits = await service.search(uid, "which language for data analysis?")
        assert hits and hits[0]["content"].startswith("Prefers Python")
        assert all(h["content"] != "Has a dog named Biscuit" for h in hits)
        assert "score" in hits[0] and "sim" in hits[0]
    run(t)


def test_extraction_goes_to_pending_and_approval_supersedes(fakes):
    async def t(uid):
        old = await service.create(uid, "Lives in Chennai")
        fakes.extract_reply = lambda body: json.dumps({"memories": [
            {"content": "Lives in Berlin", "category": "profile", "importance": 4, "confidence": 0.95,
             "supersedes": re.search(r"\[([0-9a-f-]{36})\] Lives in Chennai", body).group(1)},
            {"content": "My password is swordfish", "category": "fact"}]})
        conv = await db.create_conversation(uid, None)
        conv = await db.get_conversation(uid, conv["id"])
        got = await service.propose_from_exchange(uid, conv, "I just moved to Berlin from Chennai!", "Congrats!")
        assert [m["content"] for m in got] == ["Lives in Berlin"]  # the secret was dropped
        new = got[0]
        assert new["status"] == "pending" and new["supersedes_id"] == old["id"]
        assert new["source_excerpt"].startswith("I just moved")
        # pending memories are not used in chat
        r = await service.recall(uid, conv, "where do I live", Budget(history=1000))
        assert [m["content"] for m in r.memories] == ["Lives in Chennai"]
        await service.approve(uid, new["id"], "Lives in Berlin, Germany")
        assert (await store.get(uid, old["id"]))["status"] == "archived"
        d = await service.detail(uid, new["id"])
        assert d["status"] == "active" and d["content"] == "Lives in Berlin, Germany"
        assert d["source_conversation"]["id"] == conv["id"]
        assert [x["reason"] for x in d["revisions"]][-2:] == ["corrected before approval", "approved"]
    run(t)


def test_auto_approve_mode_and_incognito_conversation(fakes):
    async def t(uid):
        await service.update_settings(uid, {"require_approval": False})
        fakes.extract_reply = json.dumps({"memories": [{"content": "Uses Neovim as editor", "category": "preference",
                                                        "importance": 3, "confidence": 0.9}]})
        conv = await db.get_conversation(uid, (await db.create_conversation(uid, None))["id"])
        got = await service.propose_from_exchange(uid, conv, "I do all my coding in Neovim these days", "ok")
        assert got and got[0]["status"] == "active"
        await store.set_conversation_memory_enabled(uid, conv["id"], False)
        conv = await db.get_conversation(uid, conv["id"])
        fakes.extract_reply = json.dumps({"memories": [{"content": "Plays chess", "category": "preference"}]})
        assert await service.propose_from_exchange(uid, conv, "I play a lot of chess online lately", "ok") == []
        r = await service.recall(uid, conv, "editor", Budget(history=1000))
        assert r.memories == []  # incognito chats neither read nor write long-term memory
    run(t)


def test_edit_reembeds_and_tracks_versions():
    async def t(uid):
        m = await service.create(uid, "Favourite language is Go")
        await service.update(uid, m["id"], {"content": "Favourite language is Rust", "importance": 0.9,
                                            "pinned": True, "category": "preference"})
        hits = await service.search(uid, "rust")
        assert hits and hits[0]["id"] == m["id"] and hits[0]["pinned"]
        revs = await store.revisions(uid, m["id"])
        assert [r["content"] for r in revs] == ["Favourite language is Go", "Favourite language is Rust"]
        with pytest.raises(service.MemoryRejected):
            await service.update(uid, m["id"], {"content": "api_key = abcdefghijklmnopqrstu"})
        with pytest.raises(service.MemoryNotFound):
            await service.update(uid, str(uuid.uuid4()), {"pinned": True})
        log = await store.audit_log(uid)
        assert all("Rust" not in json.dumps(e["detail"]) for e in log)  # audit never stores content
    run(t)


def test_recall_packs_pinned_core_and_bumps_usage():
    async def t(uid):
        pin = await service.create(uid, "Always answer in British English", pinned=True)
        await service.create(uid, "Building a budgeting app called Penny")
        conv = await db.get_conversation(uid, (await db.create_conversation(uid, None))["id"])
        r = await service.recall(uid, conv, "help me with the Penny budgeting app", Budget(history=1000))
        ids = [m["id"] for m in r.memories]
        assert ids[0] == pin["id"] and len(ids) == 2
        assert (await store.get(uid, pin["id"]))["access_count"] == 1
        await service.update_settings(uid, {"use_in_chat": False})
        assert (await service.recall(uid, conv, "Penny", Budget(history=1000))).memories == []
    run(t)


def test_long_conversation_compaction_segments_and_recall(fakes):
    async def t(uid):
        conv = await db.create_conversation(uid, None)
        for i in range(24):
            topic = "kubernetes" if i == 2 else f"topic{i}"
            await db.add_message(conv["id"], "user", f"{topic} " + "words " * 300)
            await db.add_message(conv["id"], "assistant", "answer " * 300)
        conv = await db.get_conversation(uid, conv["id"])
        prior = await db.list_messages(conv["id"])
        assert service.plan_needs_compaction(conv, prior, 4000)
        window, summary, compacted = await service.prepare_history(uid, conv, prior, 4000)
        assert compacted and summary.startswith("Rolling summary")
        assert sum(context.message_tokens(m) for m in window) <= 4000 and window[0]["role"] == "user"
        conv = await db.get_conversation(uid, conv["id"])
        assert conv["summarized_upto"] == len(prior) - len(window)
        segs = await store.list_segments(uid, conv["id"])
        assert segs and segs[0]["from_seq"] == 1 and segs[-1]["to_seq"] == conv["summarized_upto"]
        # second call: nothing new to compact
        _, _, again = await service.prepare_history(uid, conv, prior, 4000)
        assert not again
        r = await service.recall(uid, conv, "what did we say about kubernetes?", Budget(history=4000))
        assert r.recalled and "kubernetes" in r.recalled[0]["content"] and not r.recalled[0]["other"]
        # the user can inspect, correct and reset the conversation's memory
        info = await service.conversation_memory(uid, conv)
        assert info["summary"] == summary and len(info["segments"]) == len(segs)
        await store.clear_conversation_memory(uid, conv["id"])
        assert (await service.conversation_memory(uid, await db.get_conversation(uid, conv["id"])))["segments"] == []
    run(t)


def test_summaries_disabled_truncates_without_storing():
    async def t(uid):
        await service.update_settings(uid, {"summarize_conversations": False})
        conv = await db.create_conversation(uid, None)
        for i in range(10):
            await db.add_message(conv["id"], "user", f"q{i} " + "w " * 400)
            await db.add_message(conv["id"], "assistant", "a " * 400)
        conv = await db.get_conversation(uid, conv["id"])
        window, summary, compacted = await service.prepare_history(uid, conv, await db.list_messages(conv["id"]), 1500)
        assert summary is None and not compacted and 0 < len(window) < 20
        assert await store.list_segments(uid, conv["id"]) == []
    run(t)


def test_export_import_roundtrip_and_bulk_delete():
    async def t(uid):
        await service.create(uid, "Has two cats", pinned=True)
        a = await service.create(uid, "Studies machine learning")
        await service.update(uid, a["id"], {"status": "archived"})
        data = await service.export(uid)
        assert data["format"] == "aria.memory-export" and len(data["memories"]) == 2
        assert all("embedding" not in m for m in data["memories"])
        assert data["memories"][0]["revisions"]
        md = service.export_markdown(data)
        assert "Has two cats" in md and "## Archived" in md
        res = await service.delete_bulk(uid, "all")
        assert res["deleted"] == 2 and await store.list_(uid) == []
        imp = await service.import_memories(uid, json.loads(json.dumps(data, default=str)))
        assert imp["imported"] == 2
        assert (await store.counts(uid)) == {"pending": 0, "active": 1, "archived": 1}
        assert (await service.import_memories(uid, ["Has two cats"]))["duplicates"] == 1
        actions = [e["action"] for e in await store.audit_log(uid)]
        assert {"exported", "deleted_bulk", "imported"} <= set(actions)
    run(t)


def test_maintenance_expires_old_pending_and_retention():
    async def t(uid):
        await service.update_settings(uid, {"retention_days": 30, "pending_expiry_days": 7})
        keep = await service.create(uid, "Pinned forever", pinned=True)
        stale = await service.create(uid, "Unused for ages")
        p = await store.insert(uid, content="Old suggestion", category="fact", status="pending", source="extracted",
                               importance=0.5, confidence=0.9, sensitivity="normal", pinned=False, embedding=None,
                               conv_id=None, excerpt=None, supersedes_id=None, changed_by="assistant")
        await db.pool().execute("UPDATE memories SET updated_at=now()-interval '60 days', "
                                "created_at=now()-interval '60 days' WHERE user_id=$1", uid)
        res = await service.run_maintenance()
        assert res["expired"] >= 2
        left = {m["id"] for m in await store.list_(uid)}
        assert keep["id"] in left and stale["id"] not in left and p["id"] not in left
        assert (await service.export(uid))["memories"][0]["content"] == "Pinned forever"
    run(t)
