"""Memory service: the one entry point for chat, tools and HTTP routes.

Long-term memory lifecycle
  extracted from a chat ─► pending (needs approval) ─► active ─► archived (superseded / hidden)
  added by the user or on explicit request ──────────► active
  any state ─► deleted (hard delete, revisions included; the audit log keeps only that it happened)
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .. import db
from ..llm.formats import text_msg
from . import extraction, store
from .context import (Budget, chunk_ranges, fold_summary, pack, plan_window, split_history, summarize_segment)
from .privacy import PrivacySettings, admit, redact
from .scoring import clamp, fuse_and_rank, initial_importance, normalize_category, reinforce

log = logging.getLogger("aria.memory")

MAX_CONTENT = 500
LOW_CONFIDENCE = 0.6
EXPORT_FORMAT = "aria.memory-export"


class MemoryRejected(ValueError):
    """The content may not be stored (privacy rule) or the operation is not allowed."""


class MemoryNotFound(LookupError):
    pass


# ---------- providers (patched in tests) ----------
async def _embed(texts: list[str]) -> list[list[float]] | None:
    from ..llm import registry
    try:
        return await registry.embed(texts)
    except Exception:  # embeddings are an enhancement; keyword search still works
        log.exception("embedding failed")
        return None


def _utility():
    from ..llm import registry
    spec = registry.utility_model()
    return registry.provider_for(spec), spec.model


def _clean(text: str) -> str:
    return " ".join(str(text or "").split())[:MAX_CONTENT]


# ---------- settings ----------
async def get_settings(user_id: str) -> PrivacySettings:
    return PrivacySettings.from_dict(await store.get_settings(user_id))


async def update_settings(user_id: str, patch: dict) -> PrivacySettings:
    cur = await get_settings(user_id)
    new = PrivacySettings.from_dict({**cur.to_dict(), **patch})
    changed = {k: v for k, v in new.to_dict().items() if cur.to_dict()[k] != v}
    if changed:
        await store.put_settings(user_id, new.to_dict())
        await store.audit(user_id, "settings_changed", None, changed=changed)
    return new


# ---------- create / edit / review / delete ----------
async def create(user_id: str, content: str, *, source: str = "manual", category: str | None = None,
                 importance: float | None = None, pinned: bool = False, conv_id: str | None = None,
                 excerpt: str | None = None, hint: float | None = None, confidence: float = 1.0,
                 supersedes_id: str | None = None, changed_by: str = "user",
                 settings: PrivacySettings | None = None) -> dict:
    content = _clean(content)
    if len(content) < 2:
        raise MemoryRejected("A memory needs some content.")
    settings = settings or await get_settings(user_id)
    if source == "explicit" and not settings.memory_enabled:
        raise MemoryRejected("Memory is turned off in the user's privacy settings.")
    adm = admit(content, settings, source)
    if not adm.allowed:
        raise MemoryRejected(adm.reason)
    status = "pending" if adm.force_pending or (source == "extracted" and confidence < LOW_CONFIDENCE) else "active"
    cat = normalize_category(category, content)

    emb = await _embed([content])
    similar = await _candidates(user_id, content, emb[0] if emb else None, ("active", "pending"), k=5)
    dup = extraction.find_duplicate(content, similar, vector_scores=emb is not None)
    if dup:
        fields: dict[str, Any] = {"importance": reinforce(float(dup["importance"]))}
        if dup["status"] == "pending" and status == "active":
            fields["status"] = "active"
        m = await store.update(user_id, dup["id"], fields, changed_by="system",
                               reason="reinforced: mentioned again")
        return {**(m or dup), "duplicate": True}

    imp = clamp(importance) if importance is not None else initial_importance(cat, source, hint, confidence)
    exc = redact(excerpt)[:400] if excerpt and settings.store_source_excerpts else None
    m = await store.insert(user_id, content=content, category=cat, status=status, source=source, importance=imp,
                           confidence=clamp(confidence), sensitivity=adm.sensitivity, pinned=pinned,
                           embedding=emb[0] if emb else None, conv_id=conv_id, excerpt=exc,
                           supersedes_id=supersedes_id, changed_by=changed_by,
                           reason={"manual": "added by you", "explicit": "you asked to remember this",
                                   "import": "imported", "extracted": "learned from a conversation"}.get(source, ""))
    await store.audit(user_id, "created", m["id"], source=source, status=status, category=cat)
    return m


async def update(user_id: str, mem_id: str, patch: dict, *, changed_by: str = "user", reason: str = "") -> dict:
    m = await store.get(user_id, mem_id)
    if not m:
        raise MemoryNotFound(mem_id)
    fields: dict[str, Any] = {}
    emb = None
    content_changed = False
    if patch.get("content") is not None:
        content = _clean(patch["content"])
        if len(content) < 2:
            raise MemoryRejected("A memory needs some content.")
        if content != m["content"]:
            adm = admit(content, await get_settings(user_id), "manual")
            if not adm.allowed:
                raise MemoryRejected(adm.reason)
            fields.update(content=content, sensitivity=adm.sensitivity, confidence=1.0)
            content_changed = True
            emb = await _embed([content])
    if patch.get("category") is not None:
        fields["category"] = normalize_category(patch["category"], fields.get("content", m["content"]))
    if patch.get("importance") is not None:
        fields["importance"] = round(clamp(float(patch["importance"]), 0.0, 1.0), 3)
    if patch.get("pinned") is not None:
        fields["pinned"] = bool(patch["pinned"])
    if patch.get("status") is not None:
        st = patch["status"]
        if st not in ("active", "archived"):
            raise MemoryRejected("Status must be 'active' or 'archived' (use approve for suggestions).")
        if m["status"] == "pending" and st == "active":
            return await approve(user_id, mem_id, fields.get("content"))
        fields["status"] = st
    fields = {k: v for k, v in fields.items() if m.get(k) != v}
    if not fields:
        return m
    out = await store.update(user_id, mem_id, fields, embedding=emb[0] if emb else None,
                             clear_embedding=content_changed and not emb, changed_by=changed_by,
                             reason=reason or ("corrected" if content_changed else "edited"))
    await store.audit(user_id, "edited", mem_id, fields=sorted(fields), by=changed_by)
    return out  # type: ignore[return-value]


async def approve(user_id: str, mem_id: str, content: str | None = None) -> dict:
    m = await store.get(user_id, mem_id)
    if not m:
        raise MemoryNotFound(mem_id)
    if m["status"] != "pending":
        raise MemoryRejected("Only suggested memories can be approved.")
    if content and _clean(content) != m["content"]:
        await update(user_id, mem_id, {"content": content}, reason="corrected before approval")
    out = await store.update(user_id, mem_id, {"status": "active"}, changed_by="user", reason="approved")
    if m.get("supersedes_id"):
        await _archive_superseded(user_id, m["supersedes_id"], mem_id)
    await store.audit(user_id, "approved", mem_id)
    return out  # type: ignore[return-value]


async def _archive_superseded(user_id: str, old_id: str, new_id: str) -> None:
    old = await store.get(user_id, old_id)
    if old and old["status"] == "active":
        await store.update(user_id, old_id, {"status": "archived"}, changed_by="system",
                           reason=f"superseded by {new_id}")
        await store.audit(user_id, "archived", old_id, superseded_by=new_id)


async def reject(user_id: str, mem_id: str) -> None:
    m = await store.get(user_id, mem_id)
    if not m:
        raise MemoryNotFound(mem_id)
    await store.delete(user_id, mem_id)
    await store.audit(user_id, "rejected", mem_id, category=m["category"])


async def review(user_id: str, approve_ids: list[str], reject_ids: list[str]) -> dict:
    done = {"approved": 0, "rejected": 0, "errors": []}
    for i in approve_ids:
        try:
            await approve(user_id, i)
            done["approved"] += 1
        except (MemoryRejected, MemoryNotFound) as e:
            done["errors"].append(f"{i}: {e}")
    for i in reject_ids:
        try:
            await reject(user_id, i)
            done["rejected"] += 1
        except MemoryNotFound as e:
            done["errors"].append(f"{i}: not found ({e})")
    return done


async def delete(user_id: str, mem_id: str) -> None:
    m = await store.get(user_id, mem_id)
    if not m or not await store.delete(user_id, mem_id):
        raise MemoryNotFound(mem_id)
    await store.audit(user_id, "deleted", mem_id, category=m["category"], status=m["status"])


async def delete_bulk(user_id: str, scope: str, *, include_conversation_memory: bool = False,
                      ids: list[str] | None = None) -> dict:
    if ids:
        n = await store.delete_ids(user_id, ids)
    else:
        statuses = {"all": store.STATUSES, "pending": ("pending",), "archived": ("archived",),
                    "active": ("active",)}.get(scope)
        if statuses is None:
            raise MemoryRejected("scope must be all, active, pending or archived")
        n = await store.delete_where(user_id, statuses)
    if include_conversation_memory:
        await store.clear_all_conversation_memory(user_id)
    await store.audit(user_id, "deleted_bulk", None, scope="ids" if ids else scope, count=n,
                      conversation_memory=include_conversation_memory)
    return {"deleted": n}


# ---------- search & retrieval ----------
async def _candidates(user_id: str, query: str, embedding: list[float] | None, statuses: tuple[str, ...],
                      k: int) -> list[dict]:
    vh = await store.vector_search(user_id, embedding, statuses, k) if embedding is not None else []
    kh = await store.keyword_search(user_id, query, statuses, k)
    return fuse_and_rank(vh, kh, min_similarity=0.0) if vh else kh


async def search(user_id: str, query: str, k: int = 10, statuses: tuple[str, ...] = ("active",),
                 embedding: list[float] | None = None) -> list[dict]:
    """Hybrid semantic search: pgvector cosine + Postgres full-text, fused (RRF) and re-ranked by importance."""
    query = query.strip()
    if not query:
        return []
    if embedding is None:
        emb = await _embed([query])
        embedding = emb[0] if emb else None
    vh = await store.vector_search(user_id, embedding, statuses, k * 3) if embedding is not None else []
    kh = await store.keyword_search(user_id, query, statuses, k * 3)
    return fuse_and_rank(vh, kh)[:k]


@dataclass
class Recall:
    memories: list[dict] = field(default_factory=list)   # long-term memories for the prompt
    recalled: list[dict] = field(default_factory=list)   # episodic segments (earlier in this / other chats)

    def public(self) -> dict:
        return {"memories": [{"id": m["id"], "content": m["content"], "category": m["category"],
                              "pinned": m["pinned"], "score": m.get("score")} for m in self.memories],
                "recalled": [{"conversation_id": r["conversation_id"], "title": r.get("title"), "other": r.get("other"),
                              "from_seq": r["from_seq"], "to_seq": r["to_seq"]} for r in self.recalled]}


async def recall(user_id: str, conv: dict, query: str, budget: Budget) -> Recall:
    """Context retrieval for one turn. Never raises: memory must not break chat."""
    out = Recall()
    try:
        settings = await get_settings(user_id)
        long_term = settings.memory_enabled and settings.use_in_chat and conv.get("memory_enabled", True)
        has_segments = (conv.get("summarized_upto") or 0) > 0
        cross = settings.cross_conversation_recall and long_term
        if not (long_term or has_segments or cross) or not query.strip():
            if long_term:  # still give the model the user's pinned core memories
                out.memories = pack(await store.pinned(user_id), budget.memories)
                await store.touch(user_id, [m["id"] for m in out.memories])
            return out
        emb = await _embed([query[:2000]])
        e = emb[0] if emb else None
        if long_term:
            core = await store.pinned(user_id)
            hits = await search(user_id, query, k=12, embedding=e)
            seen = {m["id"] for m in core}
            merged = core + [h for h in hits if h["id"] not in seen]
            out.memories = pack(merged, budget.memories)[:16]
            await store.touch(user_id, [m["id"] for m in out.memories])
        if has_segments or cross:
            vh, kh = await store.search_segments(user_id, query, e, conv_id=conv.get("id"), include_other=cross, k=6)
            ranked = fuse_and_rank(vh, kh, min_similarity=0.35)
            for r in ranked:
                r["other"] = r["conversation_id"] != conv.get("id")
            out.recalled = pack(ranked, budget.recall)[:4]
    except Exception:
        log.exception("memory recall failed")
    return out


# ---------- short-term memory & automatic compaction ----------
def plan_needs_compaction(conv: dict, prior: list[dict], budget: int) -> bool:
    msgs = [text_msg(m["role"], m["content"] or "(no content)") for m in prior]
    return plan_window(msgs, conv.get("summarized_upto") or 0, budget).compact is not None


async def prepare_history(user_id: str, conv: dict, prior: list[dict], budget: int) -> tuple[list[dict], str | None, bool]:
    """Working window (verbatim) + rolling summary for a conversation; compacts older turns when needed.

    Returns (window messages, summary or None, compacted_now)."""
    msgs = [text_msg(m["role"], m["content"] or "(no content)") for m in prior]
    settings = await get_settings(user_id)
    if not settings.summarize_conversations:
        # Privacy: keep nothing derived from the chat; simply drop the oldest turns.
        return msgs[split_history(msgs, budget):], None, False

    plan = plan_window(msgs, conv.get("summarized_upto") or 0, budget)
    summary = conv.get("summary")
    if not plan.compact:
        return msgs[plan.start:], summary if plan.start > 0 else None, False
    a, b = plan.compact
    try:
        llm, model = _utility()
        ranges = chunk_ranges(msgs, a, b)
        texts = list(await asyncio.gather(*(summarize_segment(llm, model, msgs[i:j]) for i, j in ranges)))
        embs = await _embed(texts)
        for n, ((i, j), t) in enumerate(zip(ranges, texts)):
            await store.add_segment(user_id, conv["id"], prior[i]["seq"], prior[j - 1]["seq"], t,
                                    embs[n] if embs else None)
        summary = await fold_summary(llm, model, summary, texts)
        await db.update_conversation(user_id, conv["id"], summary=summary, summarized_upto=b)
        conv.update(summary=summary, summarized_upto=b)
        return msgs[plan.start:], summary, True
    except Exception:
        log.exception("compaction failed; truncating this turn instead")
        return msgs[plan.start:], summary, False


# ---------- learning from conversations ----------
async def propose_from_exchange(user_id: str, conv: dict, user_text: str, answer: str) -> list[dict]:
    """Extract candidate memories from one exchange. Returns newly created (pending or active) memories."""
    try:
        settings = await get_settings(user_id)
        if not (settings.memory_enabled and settings.auto_extract and conv.get("memory_enabled", True)):
            return []
        if len(user_text.strip()) < 12:
            return []
        llm, model = _utility()
        emb = await _embed([user_text[:2000]])
        known = await search(user_id, user_text[:2000], k=10, statuses=("active", "pending"),
                             embedding=emb[0] if emb else None)
        cands = await extraction.extract(llm, model, user_text, answer, known)
        out = []
        for c in cands:
            try:
                m = await create(user_id, c.content, source="extracted", category=c.category, hint=c.importance,
                                 confidence=c.confidence, conv_id=conv["id"], excerpt=user_text[:400],
                                 supersedes_id=c.supersedes, changed_by="assistant", settings=settings)
            except MemoryRejected as e:
                log.info("candidate memory not stored: %s", e)
                continue
            if m.get("duplicate"):
                continue
            if m["status"] == "active" and c.supersedes:
                await _archive_superseded(user_id, c.supersedes, m["id"])
            out.append(m)
        return out
    except Exception:
        log.exception("memory extraction failed")
        return []


# ---------- inspection, export, import ----------
async def detail(user_id: str, mem_id: str) -> dict:
    m = await store.get(user_id, mem_id)
    if not m:
        raise MemoryNotFound(mem_id)
    m["revisions"] = await store.revisions(user_id, mem_id)
    m["source_conversation"] = None
    if m.get("source_conversation_id"):
        c = await db.get_conversation(user_id, m["source_conversation_id"])
        if c:
            m["source_conversation"] = {"id": c["id"], "title": c["title"]}
    return m


async def conversation_memory(user_id: str, conv: dict) -> dict:
    msgs = await db.list_messages(conv["id"])
    return {"conversation_id": conv["id"], "memory_enabled": conv.get("memory_enabled", True),
            "summary": conv.get("summary"), "summarized_upto": conv.get("summarized_upto") or 0,
            "total_messages": len(msgs), "state": conv.get("state") or {},
            "segments": await store.list_segments(user_id, conv["id"]),
            "memories": await store.by_conversation(user_id, conv["id"])}


async def export(user_id: str) -> dict:
    user = await db.get_user(user_id)
    revs: dict[str, list[dict]] = {}
    for r in await store.all_revisions(user_id):
        revs.setdefault(r.pop("memory_id"), []).append(r)
    memories = await store.list_(user_id, sort="recent", limit=100_000)
    for m in memories:
        m["revisions"] = revs.get(m["id"], [])
    segs: dict[str, list[dict]] = {}
    for s in await store.list_segments(user_id):
        segs.setdefault(s["conversation_id"], []).append(
            {"from_seq": s["from_seq"], "to_seq": s["to_seq"], "summary": s["summary"], "created_at": s["created_at"]})
    convs = [{"conversation_id": c["id"], "title": c["title"], "summary": c["summary"], "state": c["state"],
              "memory_enabled": c["memory_enabled"], "segments": segs.pop(c["id"], [])}
             for c in await store.conversation_summaries(user_id)]
    for cid, ss in segs.items():  # segments of chats whose summary was cleared
        convs.append({"conversation_id": cid, "title": None, "summary": None, "state": {}, "segments": ss})
    await store.audit(user_id, "exported", None, memories=len(memories))
    return {"format": EXPORT_FORMAT, "version": 1, "exported_at": datetime.now(timezone.utc).isoformat(),
            "user": {"email": user["email"], "name": user["name"]} if user else None,
            "settings": (await get_settings(user_id)).to_dict(), "memories": memories,
            "conversation_memory": convs, "audit_log": await store.audit_log(user_id, limit=10_000)}


def export_markdown(data: dict) -> str:
    lines = ["# Aria memory export", "", f"Exported {data['exported_at']}", ""]
    by_status: dict[str, list[dict]] = {}
    for m in data["memories"]:
        by_status.setdefault(m["status"], []).append(m)
    for status, title in (("active", "Memories"), ("pending", "Suggested (awaiting your approval)"),
                          ("archived", "Archived")):
        items = by_status.get(status, [])
        if not items:
            continue
        lines += [f"## {title}", ""]
        for m in sorted(items, key=lambda x: -float(x["importance"])):
            pin = " 📌" if m["pinned"] else ""
            lines.append(f"- **[{m['category']}]** {m['content']}{pin} _(importance {float(m['importance']):.2f}, "
                         f"{m['source']}, updated {str(m['updated_at'])[:10]})_")
        lines.append("")
    convs = [c for c in data["conversation_memory"] if c.get("summary")]
    if convs:
        lines += ["## Conversation summaries", ""]
        for c in convs:
            lines += [f"### {c.get('title') or c['conversation_id']}", "", c["summary"], ""]
    lines += ["## Privacy settings", ""] + [f"- `{k}`: {v}" for k, v in data["settings"].items()]
    return "\n".join(lines) + "\n"


async def import_memories(user_id: str, data: dict) -> dict:
    items = data.get("memories") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise MemoryRejected("Expected an Aria memory export or a list of memories.")
    result = {"imported": 0, "duplicates": 0, "skipped": []}
    settings = await get_settings(user_id)
    for it in items[:2000]:
        if isinstance(it, str):
            it = {"content": it}
        if not isinstance(it, dict) or it.get("status") == "pending":
            continue
        try:
            m = await create(user_id, str(it.get("content", "")), source="import", category=it.get("category"),
                             importance=it.get("importance"), pinned=bool(it.get("pinned")), settings=settings)
            if it.get("status") == "archived" and not m.get("duplicate"):
                await store.update(user_id, m["id"], {"status": "archived"}, changed_by="system", reason="imported")
            result["duplicates" if m.get("duplicate") else "imported"] += 1
        except MemoryRejected as e:
            result["skipped"].append(f"{str(it.get('content', ''))[:60]}… — {e}")
    await store.audit(user_id, "imported", None, imported=result["imported"], skipped=len(result["skipped"]))
    return result


# ---------- maintenance ----------
async def run_maintenance() -> dict:
    """Expire stale suggestions and memories past each user's retention window; backfill missing embeddings."""
    all_settings = {uid: PrivacySettings.from_dict(s) for uid, s in (await store.all_settings()).items()}
    pending = {uid: all_settings.get(uid, PrivacySettings()).pending_expiry_days
               for uid in await store.users_with_pending()}
    retention = {uid: s.retention_days for uid, s in all_settings.items() if s.retention_days}
    removed = await store.expire(None, pending, retention)
    for r in removed:
        await store.audit(r["user_id"], "expired", r["id"], status=r["status"])
    backfilled = 0
    missing = await store.missing_embeddings(64)
    if missing:
        embs = await _embed([m["content"] for m in missing])
        if embs:
            for m, e in zip(missing, embs):
                await store.set_embedding(m["id"], e)
            backfilled = len(missing)
    return {"expired": len(removed), "embeddings_backfilled": backfilled}


async def maintenance_loop(interval_s: float = 6 * 3600) -> None:
    while True:
        try:
            res = await run_maintenance()
            if any(res.values()):
                log.info("memory maintenance: %s", res)
        except Exception:
            log.exception("memory maintenance failed")
        await asyncio.sleep(interval_s)
