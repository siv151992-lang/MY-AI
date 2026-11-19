import pytest

from app import setup


@pytest.mark.parametrize("raw,provider,value", [
    ("sk-ant-api03-abcDEF_123-xyz", "anthropic", "sk-ant-api03-abcDEF_123-xyz"),
    ("  sk-proj-abc123DEF456  ", "openai", "sk-proj-abc123DEF456"),
    ("ANTHROPIC_API_KEY=sk-ant-abc12345", "anthropic", "sk-ant-abc12345"),
    ('"sk-abc12345678"', "openai", "sk-abc12345678"),
    ("tvly-dev-abc12345", "tavily", "tvly-dev-abc12345"),
    ("Ollama", "ollama", ""),
])
def test_detect_recognises_keys(raw, provider, value):
    assert setup.detect(raw) == (provider, value)


@pytest.mark.parametrize("raw", ["hello there", "", "sk-ant-abc 123 456", "sk-a<script>bcdefgh", "sk-1"])
def test_detect_rejects_other_text(raw):
    with pytest.raises(setup.SetupError):
        setup.detect(raw)


def test_mask_never_shows_the_whole_key():
    assert setup.mask("sk-ant-api03-SECRETSECRET-wxyz") == "sk-ant-…wxyz"
    assert setup.mask("http://127.0.0.1:11434/v1") == "http://127.0.0.1:11434/v1"
    assert setup.mask(None) is None
