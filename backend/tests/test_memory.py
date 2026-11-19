"""Unit tests for the memory architecture (no database needed)."""
from datetime import datetime, timedelta, timezone

from app.agent.prompts import build_system_prompt
from app.llm.formats import text_msg
from app.memory import context, extraction, privacy, scoring
from app.memory.privacy import PrivacySettings

NOW = datetime(2026, 11, 19, tzinfo=timezone.utc)


# ---------- privacy ----------
def test_secrets_are_never_admitted_even_when_the_user_asks():
    s = PrivacySettings()
    for text in ("My password is hunter2!", "my api key: sk-ant-abcdefghijklmnopqrstuv",
                 "Card 4111 1111 1111 1111", "SSN 123-45-6789", "-----BEGIN RSA PRIVATE KEY-----"):
        a = privacy.admit(text, s, "manual")
        assert not a.allowed and a.sensitivity == "secret", text


def test_card_detection_uses_luhn():
    assert privacy.classify("4111 1111 1111 1111").level == "secret"
    assert privacy.classify("order 1234 5678 9012 3456").level != "secret"


def test_sensitive_facts_need_opt_in_and_approval_when_extracted():
    text = "Has been diagnosed with diabetes"
    assert not privacy.admit(text, PrivacySettings(), "extracted").allowed
    a = privacy.admit(text, PrivacySettings(allow_sensitive=True, require_approval=False), "extracted")
    assert a.allowed and a.force_pending and a.sensitivity == "sensitive"
    assert privacy.admit(text, PrivacySettings(), "explicit").allowed  # the user asked for it


def test_extracted_memories_follow_approval_and_extraction_switches():
    assert privacy.admit("Works as a nurse", PrivacySettings(), "extracted").force_pending
    assert not privacy.admit("Works as a nurse", PrivacySettings(require_approval=False), "extracted").force_pending
    assert not privacy.admit("Works as a nurse", PrivacySettings(auto_extract=False), "extracted").allowed
    assert not privacy.admit("Works as a nurse", PrivacySettings(memory_enabled=False), "extracted").allowed


def test_redaction_masks_contact_details_and_secrets():
    out = privacy.redact("mail me at a.b@example.com or +1 (555) 123-4567, password: x9")
    assert "example.com" not in out and "555" not in out and "x9" not in out
    assert "[email]" in out and "[phone]" in out


def test_settings_validation():
    s = PrivacySettings.from_dict({"retention_days": 0, "pending_expiry_days": 9999, "use_in_chat": "nope",
                                   "allow_sensitive": True, "unknown": 1})
    assert s.retention_days is None and s.pending_expiry_days == 365
    assert s.use_in_chat is True and s.allow_sensitive is True
    assert PrivacySettings.from_dict({"retention_days": 30}).retention_days == 30


# ---------- scoring ----------
def test_initial_importance_orders_by_category_source_and_confidence():
    instr = scoring.initial_importance("instruction", "manual")
    trivia = scoring.initial_importance("other", "extracted", hint=0.0, confidence=0.5)
    assert instr > scoring.initial_importance("fact", "manual") > trivia
    assert 0.05 <= trivia <= 1 and instr <= 1


def test_effective_importance_decays_but_pinned_and_used_hold_up():
    fresh = {"importance": 0.8, "updated_at": NOW}
    old = {"importance": 0.8, "updated_at": NOW - timedelta(days=365)}
    assert scoring.effective_importance(fresh, NOW) > scoring.effective_importance(old, NOW) > 0.8 * 0.4
    assert scoring.effective_importance({**old, "pinned": True}, NOW) == 1.0
    used = {**old, "access_count": 50}
    assert scoring.effective_importance(used, NOW) > scoring.effective_importance(old, NOW)


def test_reinforce_is_bounded():
    x = 0.5
    for _ in range(100):
        x = scoring.reinforce(x)
    assert x <= 1.0


def test_rrf_normalised():
    s = scoring.rrf([["a", "b"], ["a", "c"]])
    assert abs(s["a"] - 1.0) < 1e-9 and s["b"] < s["a"] and s["c"] < s["a"]


def test_fuse_and_rank_combines_vector_keyword_and_importance():
    base = {"updated_at": NOW, "access_count": 0, "pinned": False}
    vector = [{"id": "1", "sim": 0.82, "importance": 0.5, **base},
              {"id": "2", "sim": 0.80, "importance": 0.95, **base},
              {"id": "noise", "sim": 0.1, "importance": 1.0, **base}]
    keyword = [{"id": "1", "kw": 0.3, "importance": 0.5, **base},
               {"id": "kwonly", "kw": 0.2, "importance": 0.5, **base}]
    ranked = scoring.fuse_and_rank(vector, keyword, NOW)
    ids = [m["id"] for m in ranked]
    assert "noise" not in ids and "kwonly" in ids
    assert ids.index("1") < ids.index("kwonly")
    assert all("score" in m and "relevance" in m for m in ranked)


def test_guess_category():
    assert scoring.guess_category("Always answer in British English") == "instruction"
    assert scoring.guess_category("Prefers dark mode") == "preference"
    assert scoring.guess_category("Has a daughter named Maya") == "relationship"
    assert scoring.normalize_category("bogus", "Works at Acme as a data engineer") == "profile"


# ---------- extraction ----------
def test_parse_candidates_validates_fields_and_supersedes():
    raw = """```json
    {"memories": [
      {"content": "Lives in Berlin", "category": "profile", "importance": 4, "confidence": 0.9, "supersedes": "m1"},
      {"content": "Lives in Berlin", "category": "profile"},
      {"content": "Likes tea", "category": "weird", "importance": "high", "supersedes": "not-known"},
      {"content": "ok"}
    ]}```"""
    got = extraction.parse_candidates(raw, {"m1"})
    assert [c.content for c in got] == ["Lives in Berlin", "Likes tea"]
    assert got[0].supersedes == "m1" and got[0].importance == 0.75 and got[0].confidence == 0.9
    assert got[1].supersedes is None and got[1].category == "preference" and got[1].importance == 0.5


def test_extract_prompt_lists_known_memories():
    p = extraction.build_prompt("I moved to Berlin", "Nice!", [{"id": "m1", "content": "Lives in Chennai"}])
    assert "[m1] Lives in Chennai" in p and "I moved to Berlin" in p


# ---------- short-term context management ----------
def _convo(n: int, words: int = 400) -> list[dict]:
    msgs = []
    for i in range(n):
        msgs += [text_msg("user", f"q{i} " + "w" * words), text_msg("assistant", f"a{i} " + "w" * words)]
    return msgs


def test_plan_window_noop_while_window_fits():
    msgs = _convo(3)
    assert context.plan_window(msgs, 0, 100_000) == context.WindowPlan(0, None)


def test_plan_window_compacts_with_hysteresis():
    msgs = _convo(20)  # ~4.2k tokens
    plan = context.plan_window(msgs, 0, budget=2000, low_water=0.5)
    assert plan.compact == (0, plan.start) and msgs[plan.start]["role"] == "user"
    window = sum(context.message_tokens(m) for m in msgs[plan.start:])
    assert window <= 1000 + 110
    # next turn: window grew slightly but still fits, so no new compaction
    grown = msgs + _convo(1)
    assert context.plan_window(grown, plan.start, budget=2000).compact is None


def test_chunk_ranges_cover_range_and_start_on_user_turns():
    msgs = _convo(30, words=200)
    ranges = context.chunk_ranges(msgs, 0, 50, max_tokens=600, max_messages=8)
    assert ranges[0][0] == 0 and ranges[-1][1] == 50
    assert all(a[1] == b[0] for a, b in zip(ranges, ranges[1:]))
    assert all(msgs[a]["role"] == "user" for a, _ in ranges)
    assert all(b - a <= 8 for a, b in ranges)


def test_pack_respects_budget():
    items = [{"content": "x" * 400}, {"content": "y" * 4000}, {"content": "z" * 40}]
    assert [i["content"][0] for i in context.pack(items, 200)] == ["x", "z"]


def test_system_prompt_renders_memories_and_recall():
    s = build_system_prompt(user_name="", memories=[{"content": "Answer in Spanish", "category": "instruction",
                                                     "pinned": True}],
                            recalled=[{"content": "We chose Postgres", "from_seq": 3, "to_seq": 9, "other": False},
                                      {"content": "Trip to Rome", "title": "Travel", "other": True,
                                       "from_seq": 1, "to_seq": 4}],
                            conversation_summary=None, conversation_state="", documents=[],
                            reasoning_guidance="", tool_names=[])
    assert "(instruction, pinned) Answer in Spanish" in s
    assert "messages 3-9" in s and "We chose Postgres" in s and "different chat: Travel" in s
