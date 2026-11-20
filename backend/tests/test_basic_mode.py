"""Basic mode: useful answers with no AI model connected."""
from datetime import datetime, timezone

import pytest

from app.basic import engine


@pytest.mark.parametrize("q,expect", [
    ("hi", "basic mode"),
    ("What is 12*(3+4)?", "= **84**"),
    ("what is 15% of 80", "**12**"),
    ("square root of 144", "**12**"),
    ("10 miles to km", "**16.0934 km**"),
    ("100 f to c", "**37.78 °C**"),
    ("5 kg in lbs", "**11.0231 lbs**"),
    ("What is the capital of Australia?", "**Canberra**"),
    ("capital of the united states", "Washington, D.C."),
    ("how many planets are in the solar system", "**8 planets**"),
    ("what is the speed of light", "299,792,458"),
    ("tell me about photosynthesis", "Photosynthesis"),
    ("how do I connect an API key", "Connect an AI model"),
    ("what can you do", "Aria"),
    ("when did world war 2 end", "1939 to 1945"),
])
def test_answers(q, expect):
    assert expect in engine.answer(q)


def test_date_time_uses_given_clock():
    now = datetime(2026, 11, 20, 9, 30, tzinfo=timezone.utc)
    assert "Friday, 20 November 2026" in engine.answer("what is the date today?", now=now)


def test_honest_when_unknown_and_safe_math():
    out = engine.answer("who won the chess championship last week")
    assert "don't know" in out and "free local AI" in out
    assert engine.calculate("__import__('os').system('x')") is None
    assert engine.calculate("9**9**9") is None  # refuses huge powers instead of hanging
    assert "capital" in engine.answer("capital of Atlantis") and "don't have" in engine.answer("capital of Atlantis")


def test_document_hits_are_quoted_with_source():
    out = engine.answer("refund policy", doc_hits=[{"filename": "terms.pdf", "page": 2, "content": "Refunds within 30 days."}])
    assert "Refunds within 30 days." in out and "terms.pdf, page 2" in out
