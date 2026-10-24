# Copyright 2026 The uni-llm-client Authors
# SPDX-License-Identifier: Apache-2.0

"""OpenRouter Chat Completions API, which serves models from many vendors."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any, NoReturn

import httpx2

from uni_llm_client._errors import (
    IncompleteResponseError,
    ProviderError,
    RefusalError,
    UniLLMError,
)
from uni_llm_client._events import (
    ReasoningDelta,
    StreamEvent,
    TextDelta,
    ToolUseDelta,
    ToolUseEnd,
    ToolUseStart,
)
from uni_llm_client._messages import (
    ContentBlock,
    Message,
    ReasoningBlock,
    Response,
    StopReason,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    Usage,
    VendorContent,
)
from uni_llm_client._sse import ServerSentEvent
from uni_llm_client.providers._base import (
    ModelCall,
    Provider,
    StreamParser,
    http_error,
)

_REFUSAL_ERROR_TYPES = frozenset({"refusal", "content_policy_violation"})

FINISH_REASONS = {
    "stop": StopReason.END_TURN,
    "tool_calls": StopReason.TOOL_USE,
    "length": StopReason.MAX_TOKENS,
}

# The fields of an assistant message sent back as they came, so that a vendor
# behind OpenRouter gets its own reasoning back (`reasoning_details` carries it).
_REPLAYED = ("content", "tool_calls", "reasoning", "reasoning_details")

# Streamed `reasoning_details` whose text comes in pieces, joined by `index`.
_JOINED_DETAIL_FIELDS = ("text", "summary", "data")


class OpenRouterProvider(Provider):
    kind = "openrouter"
    api_key_env = "OPENROUTER_API_KEY"
    default_base_url = "https://openrouter.ai/api/v1"
    path = "/chat/completions"

    def build_request(self, call: ModelCall, *, stream: bool = False) -> httpx2.Request:
        body: dict[str, Any] = {
            "model": call.model,
            "messages": _messages(call, self.kind),
        }
        if call.max_tokens is not None:
            body["max_tokens"] = call.max_tokens
        if call.effort is not None:
            # OpenRouter maps the effort to the nearest level the model supports.
            body["reasoning"] = {"effort": call.effort.value}
        tools: list[dict[str, Any]] = []
        if call.web_search:
            tools.append({"type": "openrouter:web_search"})
        tools.extend(
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.input_schema,
                },
            }
            for tool in call.tools
        )
        if tools:
            body["tools"] = tools
        if stream:
            body["stream"] = True
        headers = {"Authorization": f"Bearer {self.api_key}"}
        return self.post(headers, body, call.timeout)

    def parse_response(self, response: httpx2.Response) -> Response:
        if response.is_error:
            raise self.error_of(response)
        return self.response_of(response.json())

    def error_of(self, response: httpx2.Response) -> UniLLMError:
        error = _error_body(response)
        return _error_of(error, response.status_code)

    def stream_parser(self) -> StreamParser:
        return OpenRouterStreamParser(self)

    def response_of(self, body: dict[str, Any]) -> Response:
        """The response a complete body is, streamed or not."""
        # Failures after generation starts still return HTTP 200.
        if "error" in body:
            _raise(body["error"], body["error"].get("code"))
        choice = body["choices"][0]
        message = choice["message"]
        finish_reason = choice.get("finish_reason")
        if finish_reason == "error":
            _raise(choice["error"], choice["error"].get("code"))
        if finish_reason == "content_filter" or message.get("refusal"):
            raise RefusalError(message.get("refusal") or "The model declined to answer")
        if finish_reason not in FINISH_REASONS:
            raise ProviderError(f"Unknown finish reason {finish_reason!r}")
        stop_reason = FINISH_REASONS[finish_reason]
        if message.get("tool_calls"):
            # Some vendors behind OpenRouter say "stop" with calls to make.
            stop_reason = StopReason.TOOL_USE
        replayed = {
            key: message[key] for key in _REPLAYED if message.get(key) is not None
        }
        return Response(
            message=Message(
                "assistant", _blocks_of(message), VendorContent(self.kind, replayed)
            ),
            stop_reason=stop_reason,
            usage=_usage_of(body.get("usage") or {}),
        )


class OpenRouterStreamParser(StreamParser):
    """Rebuilds the choice from its chunks, as the vendor would have sent it whole."""

    def __init__(self, provider: OpenRouterProvider) -> None:
        self._provider = provider
        self._content: list[str] = []
        self._reasoning: list[str] = []
        self._details: list[dict[str, Any]] = []
        self._detail_at: dict[int, int] = {}
        self._calls: dict[int, dict[str, Any]] = {}
        self._announced: set[int] = set()
        self._refusal: list[str] = []
        self._finish_reason: str | None = None
        self._error: dict[str, Any] | None = None
        self._usage: dict[str, Any] | None = None
        self._ended = False

    def feed(self, event: ServerSentEvent) -> Iterator[StreamEvent]:
        if event.data.strip() == "[DONE]":
            return
        data = json.loads(event.data)
        if "error" in data:
            _raise(data["error"], data["error"].get("code"))
        if data.get("usage"):
            self._usage = data["usage"]
        choices = data.get("choices") or []
        if not choices:
            return
        choice = choices[0]
        delta = choice.get("delta") or {}
        for text in _texts(delta.get("content")):
            self._content.append(text)
            yield TextDelta(text)
        if delta.get("reasoning"):
            self._reasoning.append(delta["reasoning"])
            yield ReasoningDelta(delta["reasoning"])
        for detail in delta.get("reasoning_details") or []:
            self._merge_detail(detail)
        for call in delta.get("tool_calls") or []:
            yield from self._tool_call(call)
        if delta.get("refusal"):
            self._refusal.append(delta["refusal"])
        if choice.get("finish_reason"):
            self._finish_reason = choice["finish_reason"]
            if self._finish_reason == "error":
                self._error = choice.get("error") or {}
            yield from self._ends()

    def finish(self) -> Response:
        if self._finish_reason is None:
            raise IncompleteResponseError(
                "The stream ended before the message was complete"
            )
        message: dict[str, Any] = {
            "role": "assistant",
            "content": "".join(self._content) or None,
        }
        if self._reasoning:
            message["reasoning"] = "".join(self._reasoning)
        if self._details:
            message["reasoning_details"] = self._details
        if self._calls:
            message["tool_calls"] = [self._calls[i] for i in sorted(self._calls)]
        if self._refusal:
            message["refusal"] = "".join(self._refusal)
        choice: dict[str, Any] = {
            "finish_reason": self._finish_reason,
            "message": message,
        }
        if self._error is not None:
            choice["error"] = self._error
        body: dict[str, Any] = {"choices": [choice]}
        if self._usage is not None:
            body["usage"] = self._usage
        return self._provider.response_of(body)

    def _tool_call(self, call: dict[str, Any]) -> Iterator[StreamEvent]:
        index = int(call.get("index", 0))
        entry = self._calls.setdefault(
            index,
            {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
        )
        if call.get("id"):
            entry["id"] = call["id"]
        function = call.get("function") or {}
        if function.get("name"):
            entry["function"]["name"] += function["name"]
        arguments = function.get("arguments") or ""
        entry["function"]["arguments"] += arguments
        if index not in self._announced and entry["id"] and entry["function"]["name"]:
            self._announced.add(index)
            yield ToolUseStart(entry["id"], entry["function"]["name"])
        if arguments and index in self._announced:
            yield ToolUseDelta(entry["id"], arguments)

    def _ends(self) -> Iterator[StreamEvent]:
        if self._ended:
            return
        self._ended = True
        for index in sorted(self._calls):
            call = self._calls[index]
            yield ToolUseEnd(_tool_use_of(call))

    def _merge_detail(self, detail: dict[str, Any]) -> None:
        index = detail.get("index")
        if isinstance(index, int) and index in self._detail_at:
            existing = self._details[self._detail_at[index]]
            for key, value in detail.items():
                if key in _JOINED_DETAIL_FIELDS and isinstance(value, str):
                    existing[key] = (existing.get(key) or "") + value
                else:
                    existing.setdefault(key, value)
            return
        self._details.append(dict(detail))
        if isinstance(index, int):
            self._detail_at[index] = len(self._details) - 1


def _messages(call: ModelCall, kind: str) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    if call.system is not None:
        messages.append({"role": "system", "content": call.system})
    for message in call.messages:
        if message.role == "assistant":
            messages.append(_assistant(message, kind))
        else:
            messages.extend(_user(message))
    return messages


def _assistant(message: Message, kind: str) -> dict[str, Any]:
    if message.vendor is not None and message.vendor.kind == kind:
        return {"role": "assistant", **message.vendor.content}
    raw: dict[str, Any] = {"role": "assistant", "content": message.text or None}
    calls = [
        {
            "id": block.id,
            "type": "function",
            "function": {"name": block.name, "arguments": json.dumps(block.input)},
        }
        for block in message.tool_uses
    ]
    if calls:
        raw["tool_calls"] = calls
    return raw


def _user(message: Message) -> list[dict[str, Any]]:
    """Tool results are messages of their own on this API; they come first."""
    messages: list[dict[str, Any]] = [
        {"role": "tool", "tool_call_id": block.tool_use_id, "content": block.content}
        for block in message.content
        if isinstance(block, ToolResultBlock)
    ]
    texts = [block.text for block in message.content if isinstance(block, TextBlock)]
    if len(texts) == 1:
        messages.append({"role": "user", "content": texts[0]})
    elif texts:
        parts = [{"type": "text", "text": text} for text in texts]
        messages.append({"role": "user", "content": parts})
    return messages


def _blocks_of(message: dict[str, Any]) -> tuple[ContentBlock, ...]:
    blocks: list[ContentBlock] = []
    if message.get("reasoning"):
        blocks.append(ReasoningBlock(str(message["reasoning"])))
    blocks.extend(TextBlock(text) for text in _texts(message.get("content")))
    blocks.extend(_tool_use_of(call) for call in message.get("tool_calls") or [])
    return tuple(blocks)


def _tool_use_of(call: dict[str, Any]) -> ToolUseBlock:
    function = call.get("function") or {}
    return ToolUseBlock(
        str(call.get("id") or ""),
        str(function.get("name") or ""),
        _parse_arguments(function.get("arguments")),
    )


def _parse_arguments(text: object) -> dict[str, Any]:
    if not isinstance(text, str) or not text.strip():
        return {}
    try:
        parsed = json.loads(text)
    except ValueError as error:
        raise ProviderError("The tool call's arguments are not valid JSON") from error
    if not isinstance(parsed, dict):
        raise ProviderError("The tool call's arguments are not a JSON object")
    return parsed


def _texts(content: object) -> list[str]:
    if isinstance(content, str):
        return [content] if content else []
    if isinstance(content, list):
        return [
            part["text"]
            for part in content
            if isinstance(part, dict)
            and part.get("type") == "text"
            and part.get("text")
        ]
    return []


def _usage_of(usage: dict[str, Any]) -> Usage:
    prompt_details = usage.get("prompt_tokens_details") or {}
    completion_details = usage.get("completion_tokens_details") or {}
    return Usage(
        input_tokens=usage.get("prompt_tokens") or 0,
        output_tokens=usage.get("completion_tokens") or 0,
        cached_input_tokens=prompt_details.get("cached_tokens") or 0,
        reasoning_tokens=completion_details.get("reasoning_tokens") or 0,
    )


def _error_body(response: httpx2.Response) -> dict[str, Any]:
    try:
        error = response.json()["error"]
    except (ValueError, KeyError, TypeError):
        return {"message": response.text or f"HTTP {response.status_code}"}
    return error if isinstance(error, dict) else {"message": str(error)}


def _error_of(error: dict[str, Any], status_code: object) -> UniLLMError:
    code = status_code if isinstance(status_code, int) else None
    message = str(error.get("message") or "Unknown error")
    error_type = (error.get("metadata") or {}).get("error_type")
    # OpenRouter uses 403 for guardrail, moderation and model refusals.
    if code == 403 or error_type in _REFUSAL_ERROR_TYPES:
        return RefusalError(message)
    if code is None:
        return ProviderError(message)
    return http_error(code, message)


def _raise(error: dict[str, Any], status_code: object) -> NoReturn:
    raise _error_of(error, status_code)
