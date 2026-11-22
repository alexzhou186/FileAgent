import json
from contextlib import closing
from pathlib import Path

import httpx
import pytest
from openai import OpenAI

from fileagent.agent import Agent
from fileagent.backend import DeepSeekBackend, ModelError, Usage
from fileagent.config import Settings
from fileagent.filesystem import Workspace
from fileagent.tools import FileTools

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def mock_backend(monkeypatch, handler, *, retries=0):
    transport = httpx.MockTransport(handler)
    sdk_transport = httpx.Client(transport=transport)
    settings = Settings(EXAMPLES, EXAMPLES, EXAMPLES, "test-secret", "deepseek-flash", 5, retries)

    def make_client(**options):
        assert options == {
            "api_key": "test-secret",
            "base_url": "https://api.deepseek.com",
            "timeout": 5.0,
            "max_retries": retries,
        }
        return OpenAI(http_client=sdk_transport, **options)

    monkeypatch.setattr("fileagent.backend.OpenAI", make_client)
    return DeepSeekBackend(settings)


def chat_response(message, *, reason="stop", cache=None):
    usage = {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}
    usage.update(cache or {})
    return httpx.Response(200, json={
        "id": "sdk-mock",
        "object": "chat.completion",
        "created": 0,
        "model": "deepseek-flash",
        "choices": [{"index": 0, "finish_reason": reason, "message": {"role": "assistant", **message}}],
        "usage": usage,
    })


def test_sdk_wire_format_retries_and_reads(monkeypatch):
    attempts = []

    def handle(request):
        assert str(request.url) == "https://api.deepseek.com/chat/completions"
        assert request.headers["Authorization"] == "Bearer test-secret"
        body = json.loads(request.content)
        attempts.append(body)
        assert body["model"] == "deepseek-flash"
        assert body["thinking"] == {"type": "disabled"}
        assert body["tool_choice"] == "auto"
        assert {item["function"]["name"] for item in body["tools"]} == {"list_files", "read_file", "search_files"}
        if len(attempts) == 1:
            return httpx.Response(503, headers={"retry-after-ms": "1"}, json={"error": {"message": "transient"}})
        if len(attempts) == 2:
            return chat_response({
                "content": None,
                "tool_calls": [
                    {"id": "call-a", "type": "function",
                     "function": {"name": "read_file", "arguments": '{"path":"plan-a.txt"}'}},
                    {"id": "call-b", "type": "function",
                     "function": {"name": "read_file", "arguments": '{"path":"plan-b.docx"}'}},
                ],
            }, reason="tool_calls", cache={"prompt_cache_hit_tokens": 30, "prompt_cache_miss_tokens": 70})
        assert body["messages"][-3]["tool_calls"][0]["id"] == "call-a"
        assert [item["tool_call_id"] for item in body["messages"][-2:]] == ["call-a", "call-b"]
        assert "4,800" in json.loads(body["messages"][-2]["content"])["text"]
        assert "5,900" in json.loads(body["messages"][-1]["content"])["text"]
        return chat_response({"content": "方案 A 花费较低。"}, cache={"prompt_tokens_details": {"cached_tokens": 20}})

    with closing(mock_backend(monkeypatch, handle, retries=1)) as backend:
        turn = Agent(backend, FileTools(Workspace(EXAMPLES))).run("比较方案 A 和方案 B")
    assert len(attempts) == 3
    assert turn.model_requests == 2
    assert turn.tool_calls == 2
    assert turn.usage == Usage(200, 50, 40)
    assert turn.answer == "方案 A 花费较低。"


@pytest.mark.parametrize("case", ["timeout", "http_401"])
def test_safe_errors(monkeypatch, case):
    def handle(request):
        if case == "timeout":
            raise httpx.ReadTimeout("PRIVATE_REMOTE_ERROR", request=request)
        return httpx.Response(401, json={"error": {"message": "PRIVATE_REMOTE_ERROR"}})

    with closing(mock_backend(monkeypatch, handle)) as backend:
        with pytest.raises(ModelError) as error:
            backend.complete([{"role": "user", "content": "测试"}], [])
    assert error.value.code == case
    assert "PRIVATE_REMOTE_ERROR" not in str(error.value)


@pytest.mark.parametrize("available", [True, False])
def test_verify_account_without_model_tokens(monkeypatch, available):
    attempts = []

    def handle(request):
        attempts.append(request)
        assert request.method == "GET"
        assert str(request.url) == "https://api.deepseek.com/user/balance"
        assert request.headers["Authorization"] == "Bearer test-secret"
        assert request.content == b""
        return httpx.Response(200, json={"is_available": available, "balance_infos": []})

    with closing(mock_backend(monkeypatch, handle)) as backend:
        assert backend.check_account() is available
    assert len(attempts) == 1


def test_rejected_account_check_sanitizes_error(monkeypatch):
    def handle(request):
        return httpx.Response(401, json={"error": {"message": "PRIVATE_REMOTE_ERROR"}})

    with closing(mock_backend(monkeypatch, handle)) as backend:
        with pytest.raises(ModelError) as error:
            backend.check_account()
    assert error.value.code == "http_401"
    assert "PRIVATE_REMOTE_ERROR" not in str(error.value)
