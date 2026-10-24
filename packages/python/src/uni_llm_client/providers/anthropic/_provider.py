# Copyright 2026 The uni-llm-client Authors
# SPDX-License-Identifier: Apache-2.0

"""Anthropic Messages API."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import httpx2

from uni_llm_client._effort import Effort, resolve_effort
from uni_llm_client._errors import (
    AuthenticationError,
    IncompleteResponseError,
    ProviderError,
    RateLimitError,
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
    ServerToolResultBlock,
    ServerToolUseBlock,
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
from uni_llm_client.providers.anthropic._models import (
    DEFAULT_WEB_SEARCH_TOOL,
    lookup_model,
)

API_VERSION = "2023-06-01"

# Output tokens reserved for the answer when the caller sets no limit. Token-budget
# models add their thinking budget on top, since the budget must be lower than
# max_tokens.
MAX_TOKENS = 16_000

# Thinking budgets for token-budget models; MAX uses the model's full output limit.
BUDGET_TOKENS = {Effort.LOW: 1_024, Effort.MEDIUM: 8_192, Effort.HIGH: 16_384}

STOP_REASONS = {
    "end_turn": StopReason.END_TURN,
    "stop_sequence": StopReason.END_TURN,
    "tool_use": StopReason.TOOL_USE,
    "max_tokens": StopReason.MAX_TOKENS,
    "model_context_window_exceeded": StopReason.MAX_TOKENS,
    "pause_turn": StopReason.PAUSE_TURN,
}

# Error types of events sent mid-stream, where there is no HTTP status to map.
_ERROR_TYPES: dict[str, type[ProviderError]] = {
    "authentication_error": AuthenticationError,
    "permission_error": AuthenticationError,
    "rate_limit_error": RateLimitError,
}

# Blocks whose input streams as partial JSON.
_JSON_INPUT_BLOCKS = frozenset({"tool_use", "server_tool_use", "mcp_tool_use"})


class AnthropicProvider(Provider):
    kind = "anthropic"
    api_key_env = "ANTHROPIC_API_KEY"
    default_base_url = "https://api.anthropic.com"
    path = "/v1/messages"

    def build_request(self, call: ModelCall, *, stream: bool = False) -> httpx2.Request:
        info = lookup_model(call.model)
        max_tokens = MAX_TOKENS if call.max_tokens is None else call.max_tokens
        body: dict[str, Any] = {"model": call.model, "max_tokens": max_tokens}
        if call.system is not None:
            body["system"] = call.system
        body["messages"] = [_message(message, self.kind) for message in call.messages]
        effort = call.effort
        if effort is not None:
            if info is not None:
                effort = resolve_effort(effort, info.efforts)
            if info is not None and info.thinking == "budget":
                budget = BUDGET_TOKENS.get(effort, info.max_output_tokens - max_tokens)
                body["max_tokens"] = budget + max_tokens
                body["thinking"] = {"type": "enabled", "budget_tokens": budget}
            else:
                body["thinking"] = {"type": "adaptive"}
                body["output_config"] = {"effort": effort.value}
        tools: list[dict[str, Any]] = []
        if call.web_search:
            tool = info.web_search_tool if info else DEFAULT_WEB_SEARCH_TOOL
            tools.append({"type": tool, "name": "web_search"})
        tools.extend(
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
            }
            for tool in call.tools
        )
        if tools:
            body["tools"] = tools
        if stream:
            body["stream"] = True
        headers = {"x-api-key": self.api_key, "anthropic-version": API_VERSION}
        return self.post(headers, body, call.timeout)

    def parse_response(self, response: httpx2.Response) -> Response:
        if response.is_error:
            raise self.error_of(response)
        return self.response_of(response.json())

    def error_of(self, response: httpx2.Response) -> UniLLMError:
        return http_error(response.status_code, _error_message(response))

    def stream_parser(self) -> StreamParser:
        return AnthropicStreamParser(self)

    def response_of(self, message: dict[str, Any]) -> Response:
        """The response a complete message is, streamed or not."""
        stop_reason = message["stop_reason"]
        if stop_reason == "refusal":
            details = message.get("stop_details") or {}
            raise RefusalError(
                details.get("explanation") or "The model declined to answer"
            )
        if stop_reason not in STOP_REASONS:
            raise ProviderError(f"Unknown stop reason {stop_reason!r}")
        content = message["content"]
        return Response(
            message=Message(
                "assistant", _blocks_of(content), VendorContent(self.kind, content)
            ),
            stop_reason=STOP_REASONS[stop_reason],
            usage=_usage_of(message.get("usage") or {}),
        )


class AnthropicStreamParser(StreamParser):
    """Rebuilds the message from its events, as the vendor would have sent it whole."""

    def __init__(self, provider: AnthropicProvider) -> None:
        self._provider = provider
        self._message: dict[str, Any] | None = None
        self._blocks: dict[int, dict[str, Any]] = {}
        self._json: dict[int, list[str]] = {}
        self._stopped = False

    def feed(self, event: ServerSentEvent) -> Iterator[StreamEvent]:
        data = json.loads(event.data)
        match data["type"]:
            case "message_start":
                self._message = dict(data["message"])
            case "content_block_start":
                yield from self._start(data["index"], data["content_block"])
            case "content_block_delta":
                yield from self._delta(data["index"], data["delta"])
            case "content_block_stop":
                yield from self._stop(data["index"])
            case "message_delta":
                message = self._started()
                message.update(data["delta"])
                usage = message.get("usage") or {}
                message["usage"] = {**usage, **(data.get("usage") or {})}
            case "message_stop":
                self._stopped = True
            case "error":
                raise _stream_error(data["error"])
            # `ping`, and events this version does not know, carry nothing to keep.

    def finish(self) -> Response:
        message = self._message
        if message is None or not self._stopped or "stop_reason" not in message:
            raise IncompleteResponseError(
                "The stream ended before the message was complete"
            )
        message["content"] = [self._blocks[i] for i in sorted(self._blocks)]
        return self._provider.response_of(message)

    def _started(self) -> dict[str, Any]:
        if self._message is None:
            raise ProviderError("The stream sent content before its message started")
        return self._message

    def _start(self, index: int, block: dict[str, Any]) -> Iterator[StreamEvent]:
        # Fields the vendor leaves empty would be sent back as such on replay.
        block = {key: value for key, value in block.items() if value is not None}
        self._blocks[index] = block
        if block["type"] in _JSON_INPUT_BLOCKS:
            self._json[index] = []
            if block["type"] == "tool_use":
                yield ToolUseStart(block["id"], block["name"])

    def _delta(self, index: int, delta: dict[str, Any]) -> Iterator[StreamEvent]:
        block = self._blocks[index]
        match delta["type"]:
            case "text_delta":
                block["text"] = block.get("text", "") + delta["text"]
                if delta["text"]:
                    yield TextDelta(delta["text"])
            case "thinking_delta":
                block["thinking"] = block.get("thinking", "") + delta["thinking"]
                if delta["thinking"]:
                    yield ReasoningDelta(delta["thinking"])
            case "signature_delta":
                block["signature"] = block.get("signature", "") + delta["signature"]
            case "input_json_delta":
                self._json[index].append(delta["partial_json"])
                if block["type"] == "tool_use" and delta["partial_json"]:
                    yield ToolUseDelta(block["id"], delta["partial_json"])
            case "citations_delta":
                citations = list(block.get("citations") or [])
                citations.append(delta["citation"])
                block["citations"] = citations

    def _stop(self, index: int) -> Iterator[StreamEvent]:
        block = self._blocks[index]
        if index in self._json:
            block["input"] = _parse_input("".join(self._json.pop(index)))
            if block["type"] == "tool_use":
                yield ToolUseEnd(
                    ToolUseBlock(block["id"], block["name"], block["input"])
                )


def _message(message: Message, kind: str) -> dict[str, Any]:
    if (
        message.role == "assistant"
        and message.vendor is not None
        and message.vendor.kind == kind
    ):
        return {"role": "assistant", "content": message.vendor.content}
    blocks = [raw for block in message.content if (raw := _raw_block(block))]
    # The API requires tool results before any other block (MSG-3); the sort is
    # stable, so every other block keeps its order.
    blocks.sort(key=lambda raw: raw["type"] != "tool_result")
    content: Any = blocks
    if len(blocks) == 1 and blocks[0]["type"] == "text":
        content = blocks[0]["text"]
    return {"role": message.role, "content": content}


def _raw_block(block: ContentBlock) -> dict[str, Any] | None:
    match block:
        case TextBlock(text):
            return {"type": "text", "text": text}
        case ToolUseBlock(id, name, input):
            return {"type": "tool_use", "id": id, "name": name, "input": dict(input)}
        case ToolResultBlock(tool_use_id, content, is_error):
            raw: dict[str, Any] = {
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": content,
            }
            if is_error:
                raw["is_error"] = True
            return raw
        case _:
            # Reasoning without its signature, and another vendor's tool blocks.
            return None


def _blocks_of(content: list[dict[str, Any]]) -> tuple[ContentBlock, ...]:
    blocks: list[ContentBlock] = []
    for raw in content:
        block = _block_of(raw)
        if block is not None:
            blocks.append(block)
    return tuple(blocks)


def _block_of(raw: dict[str, Any]) -> ContentBlock | None:
    kind = str(raw["type"])
    match kind:
        case "text":
            return TextBlock(raw["text"])
        case "thinking":
            return ReasoningBlock(raw.get("thinking") or "")
        case "redacted_thinking":
            return ReasoningBlock("", redacted=True)
        case "tool_use":
            return ToolUseBlock(raw["id"], raw["name"], raw.get("input") or {})
        case "server_tool_use" | "mcp_tool_use":
            return ServerToolUseBlock(raw["id"], raw["name"], raw.get("input") or {})
        case _ if kind.endswith("_tool_result"):
            return ServerToolResultBlock(raw.get("tool_use_id") or "")
        case _:
            return None


def _usage_of(usage: dict[str, Any]) -> Usage:
    cached = usage.get("cache_read_input_tokens") or 0
    written = usage.get("cache_creation_input_tokens") or 0
    return Usage(
        input_tokens=(usage.get("input_tokens") or 0) + cached + written,
        output_tokens=usage.get("output_tokens") or 0,
        cached_input_tokens=cached,
    )


def _parse_input(text: str) -> dict[str, Any]:
    if not text.strip():
        return {}
    try:
        parsed = json.loads(text)
    except ValueError as error:
        raise ProviderError("The tool call's input is not valid JSON") from error
    if not isinstance(parsed, dict):
        raise ProviderError("The tool call's input is not a JSON object")
    return parsed


def _stream_error(error: dict[str, Any]) -> ProviderError:
    cls = _ERROR_TYPES.get(str(error.get("type")), ProviderError)
    return cls(str(error.get("message") or "Unknown error"))


def _error_message(response: httpx2.Response) -> str:
    try:
        return str(response.json()["error"]["message"])
    except (ValueError, KeyError, TypeError):
        return response.text or f"HTTP {response.status_code}"
