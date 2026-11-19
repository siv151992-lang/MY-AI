import json

import pytest

from app.llm.formats import (AnthropicStreamParser, OpenAIStreamParser, ProviderError, to_anthropic, to_openai,
                             tools_to_anthropic, tools_to_openai)


def _feed(parser, lines):
    out = []
    for ln in lines:
        out.extend(parser.feed(ln))
    return out


def test_anthropic_stream_text_and_tool_use():
    lines = [
        "event: message_start",
        'data: {"type":"message_start","message":{"usage":{"input_tokens":42}}}',
        'data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}',
        'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Let me "}}',
        'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"check."}}',
        'data: {"type":"content_block_stop","index":0}',
        'data: {"type":"content_block_start","index":1,"content_block":{"type":"tool_use","id":"tu_1","name":"web_search","input":{}}}',
        'data: {"type":"content_block_delta","index":1,"delta":{"type":"input_json_delta","partial_json":"{\\"query\\": \\"pgv"}}',
        'data: {"type":"content_block_delta","index":1,"delta":{"type":"input_json_delta","partial_json":"ector\\"}"}}',
        'data: {"type":"content_block_stop","index":1}',
        'data: {"type":"message_delta","delta":{"stop_reason":"tool_use"},"usage":{"output_tokens":17}}',
        'data: {"type":"message_stop"}',
    ]
    evs = _feed(AnthropicStreamParser(), lines)
    assert [e["type"] for e in evs] == ["text", "text", "tool_call", "done"]
    assert evs[2] == {"type": "tool_call", "id": "tu_1", "name": "web_search", "input": {"query": "pgvector"}}
    assert evs[3]["stop_reason"] == "tool_use"
    assert evs[3]["usage"] == {"input_tokens": 42, "output_tokens": 17}


def test_anthropic_stream_error_is_raised():
    p = AnthropicStreamParser()
    with pytest.raises(ProviderError):
        list(p.feed('data: {"type":"error","error":{"type":"overloaded_error","message":"Overloaded"}}'))


def test_openai_stream_with_fragmented_tool_calls():
    chunks = [
        {"choices": [{"delta": {"content": "Hi"}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "run_python", "arguments": "{\"co"}}]}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": "de\": \"print(1)\"}"}}]}}]},
        {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
        {"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 5}},
    ]
    lines = [f"data: {json.dumps(c)}" for c in chunks] + ["", "data: [DONE]"]
    evs = _feed(OpenAIStreamParser(), lines)
    assert evs[0] == {"type": "text", "text": "Hi"}
    assert evs[1] == {"type": "tool_call", "id": "c1", "name": "run_python", "input": {"code": "print(1)"}}
    assert evs[2]["type"] == "done" and evs[2]["stop_reason"] == "tool_use"
    assert evs[2]["usage"] == {"input_tokens": 10, "output_tokens": 5}


def _conversation():
    return [
        {"role": "user", "content": [{"type": "image", "media_type": "image/png", "data": "AAA"},
                                     {"type": "text", "text": "What is this?"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "Checking"},
                                          {"type": "tool_call", "id": "t1", "name": "fetch_url", "input": {"url": "u"}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_call_id": "t1", "content": "page", "is_error": False}]},
        {"role": "user", "content": [{"type": "text", "text": "and?"}]},
    ]


def test_to_anthropic_merges_consecutive_roles_and_maps_blocks():
    out = to_anthropic(_conversation())
    assert [m["role"] for m in out] == ["user", "assistant", "user"]
    assert out[0]["content"][0]["source"]["type"] == "base64"
    assert out[1]["content"][1] == {"type": "tool_use", "id": "t1", "name": "fetch_url", "input": {"url": "u"}}
    assert out[2]["content"][0]["type"] == "tool_result" and out[2]["content"][1]["text"] == "and?"


def test_to_openai_maps_tools_and_images():
    out = to_openai("sys", _conversation())
    assert out[0] == {"role": "system", "content": "sys"}
    assert out[1]["content"][0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert out[2]["tool_calls"][0]["function"]["name"] == "fetch_url"
    assert out[3] == {"role": "tool", "tool_call_id": "t1", "content": "page"}
    assert out[4] == {"role": "user", "content": "and?"}


def test_tool_spec_conversion():
    spec = [{"name": "x", "description": "d", "parameters": {"type": "object", "properties": {}}}]
    assert tools_to_anthropic(spec)[0]["input_schema"]["type"] == "object"
    assert tools_to_openai(spec)[0]["function"]["name"] == "x"
