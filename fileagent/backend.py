from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from openai import APIConnectionError, APIError, APIStatusError, APITimeoutError, OpenAI

from fileagent.config import Settings

PRICES_USD = {
    "deepseek-flash": ("0.006", "0.30", "1.20"),
    "deepseek-v4-pro": ("0.044", "1.32", "3.96"),
}


class ModelError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class Usage:
    prompt: int = 0
    cached: int = 0
    completion: int = 0

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(self.prompt + other.prompt, self.cached + other.cached, self.completion + other.completion)

    def peak_usd(self, model: str) -> Decimal | None:
        rates = PRICES_USD.get(model)
        if rates is None:
            return None
        hit, miss, output = map(Decimal, rates)
        return (self.cached * hit + (self.prompt - self.cached) * miss + self.completion * output) / 1_000_000


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: str

    def as_message(self) -> dict:
        return {
            "id": self.id,
            "type": "function",
            "function": {"name": self.name, "arguments": self.arguments},
        }


@dataclass(frozen=True)
class Completion:
    text: str | None = None
    calls: tuple[ToolCall, ...] = ()
    usage: Usage | None = None
    finish_reason: str = "stop"


class Backend(Protocol):
    def complete(self, messages: list[dict], tools: list[dict]) -> Completion: ...


def usage_from_response(data) -> Usage | None:
    if data is None:
        return None
    fields = data.model_dump()
    prompt = fields["prompt_tokens"]
    cached = fields.get("prompt_cache_hit_tokens")
    if cached is None:
        cached = (fields.get("prompt_tokens_details") or {}).get("cached_tokens")
    return Usage(prompt, min(prompt, max(0, cached or 0)), fields["completion_tokens"])


@contextmanager
def translate_api_errors():
    try:
        yield
    except APITimeoutError:
        raise ModelError("timeout", "DeepSeek 请求超时，请稍后重试。") from None
    except APIConnectionError:
        raise ModelError("connection", "无法连接 DeepSeek，请检查网络。") from None
    except APIStatusError as error:
        message = {
            400: "DeepSeek 拒绝了请求，请检查模型名称和 API 设置。",
            401: "DeepSeek 身份验证失败，请检查 API Key。",
            402: "DeepSeek 账户余额不足，请检查 API 账户。",
            403: "DeepSeek 拒绝访问，请检查账户权限。",
            429: "DeepSeek 请求过于频繁，请稍后重试。",
        }.get(error.status_code, "DeepSeek 返回错误，请稍后重试。")
        raise ModelError(f"http_{error.status_code}", message) from None
    except (APIError, ValueError):
        raise ModelError("api_error", "DeepSeek 响应无法使用，请稍后重试。") from None


class DeepSeekBackend:
    def __init__(self, settings: Settings):
        self.model = settings.model
        self.connection_timeout = min(settings.timeout, 10)
        self.client = OpenAI(
            api_key=settings.api_key,
            base_url="https://api.deepseek.com",
            timeout=float(settings.timeout),
            max_retries=settings.retries,
        )

    def close(self):
        self.client.close()

    def check_account(self) -> bool:
        with translate_api_errors():
            response = self.client.with_options(timeout=self.connection_timeout, max_retries=0).get(
                "/user/balance", cast_to=object
            )
        if not isinstance(response, dict) or type(response.get("is_available")) is not bool:
            raise ModelError("invalid_response", "DeepSeek 返回了无法识别的账户状态。")
        return response["is_available"]

    def complete(self, messages: list[dict], tools: list[dict]) -> Completion:
        with translate_api_errors():
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=tools,
                tool_choice="auto",
                max_tokens=4_096,
                extra_body={"thinking": {"type": "disabled"}},
            )
        if not response.choices:
            raise ModelError("empty_response", "DeepSeek 返回了空响应。")
        choice = response.choices[0]
        message = choice.message
        if any(call.type != "function" for call in message.tool_calls or []):
            raise ModelError("invalid_response", "DeepSeek 返回了无法识别的工具调用格式。")
        calls = tuple(
            ToolCall(call.id, call.function.name, call.function.arguments)
            for call in message.tool_calls or []
        )
        return Completion(message.content, calls, usage_from_response(response.usage), choice.finish_reason)
