from decimal import Decimal
from pathlib import Path

import pytest

from fileagent.agent import MAX_MODEL_REQUESTS, MAX_TOOL_CALLS, Agent
from fileagent.backend import Completion, ModelError, ToolCall, Usage
from fileagent.filesystem import Workspace
from fileagent.tools import FileTools

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


class ScriptedBackend:
    def __init__(self, steps):
        self.steps = iter(steps)
        self.requests = []

    def complete(self, messages, tools):
        self.requests.append((list(messages), tools))
        response = next(self.steps)
        if isinstance(response, Exception):
            raise response
        return response


def build_agent(steps):
    return Agent(ScriptedBackend(steps), FileTools(Workspace(EXAMPLES)))


def test_multiple_rounds_calls_and_history():
    agent = build_agent([
        Completion(calls=(ToolCall("first", "list_files", "{}"),), usage=Usage(100, 40, 10)),
        Completion(calls=(
            ToolCall("second", "read_file", '{"path":"plan-a.txt"}'),
            ToolCall("third", "read_file", '{"path":"plan-b.docx"}'),
        ), usage=Usage(120, 50, 25)),
        Completion(text="方案 B 交付更快。", usage=Usage(140, 60, 35)),
    ])
    turn = agent.run("比较它们", history=[("先看方案 A 和 B", "工作区有这两个文件。")])
    assert turn.model_requests == 3
    assert turn.tool_calls == 3
    assert turn.usage == Usage(360, 150, 70)
    assert turn.usage.peak_usd("deepseek-flash") == Decimal("0.0001479")
    assert turn.usage.peak_usd("other") is None
    requests = agent.backend.requests
    messages, tools = requests[-1]
    assert messages[1] == {"role": "user", "content": "先看方案 A 和 B"}
    assert messages[-3]["role"] == "assistant"
    assert [message["tool_call_id"] for message in messages[-2:]] == ["second", "third"]
    assert "4,800" in messages[-2]["content"]
    assert "5,900" in messages[-1]["content"]
    assert {tool["function"]["name"] for tool in tools} == {"list_files", "read_file", "search_files"}
    assert turn.answer == "方案 B 交付更快。"
    assert not turn.failed


def test_recovers_from_invalid_or_unknown_calls(caplog):
    agent = build_agent([
        Completion(calls=(
            ToolCall("bad-1", "run_shell", '{"command":"PRIVATE_TOOL_ARGUMENT"}'),
            ToolCall("bad-2", "read_file", '{"path":"../../private.txt"}'),
            ToolCall("bad-3", "read_file", "{"),
        )),
        Completion(text="请提供有效路径。"),
    ])
    with caplog.at_level("INFO", logger="fileagent"):
        turn = agent.run("PRIVATE_PROMPT")
    errors = [event.code for event in turn.events if event.action != "model"]
    assert errors == ["unknown_tool", "invalid_path", "invalid_arguments"]
    messages, _ = agent.backend.requests[-1]
    assert [message["tool_call_id"] for message in messages[-3:]] == ["bad-1", "bad-2", "bad-3"]
    assert "PRIVATE_TOOL_ARGUMENT" not in caplog.text
    assert "PRIVATE_PROMPT" not in caplog.text
    assert not turn.failed


def test_model_request_limit():
    repeat = Completion(calls=(ToolCall("loop", "list_files", "{}"),))
    turn = build_agent([repeat] * MAX_MODEL_REQUESTS).run("继续列出文件")
    assert turn.failed
    assert turn.events[-1].code == "call_limit"
    assert turn.tool_calls == MAX_MODEL_REQUESTS - 1


def test_tool_call_limit_rejects_entire_batch():
    batch = tuple(ToolCall(str(i), "list_files", "{}") for i in range(MAX_TOOL_CALLS + 1))
    turn = build_agent([Completion(calls=batch)]).run("请求过多")
    assert turn.failed
    assert turn.tool_calls == 0
    assert turn.events[-1].code == "call_limit"


def test_duplicate_ids_rejected_before_any_call():
    duplicate = ToolCall("same", "list_files", "{}")
    turn = build_agent([Completion(calls=(duplicate, duplicate))]).run("列出文件")
    assert turn.failed
    assert turn.events[-1].code == "invalid_call_ids"
    assert turn.tool_calls == 0


@pytest.mark.parametrize("response", [Completion(), Completion(finish_reason="length"),
                                      ModelError("timeout", "请求超时")])
def test_unusable_responses_fail_cleanly(response):
    turn = build_agent([response]).run("列出文件")
    assert turn.failed
    assert turn.answer
    assert turn.model_requests == 1
