# Copyright 2026 The uni-llm-client Authors
# SPDX-License-Identifier: Apache-2.0

"""The conversation model, as specified in docs/specs/messages.md.

Vendor-neutral: every provider translates these to its own API and back.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal

Role = Literal["user", "assistant"]


@dataclass(frozen=True, slots=True)
class TextBlock:
    """Text the model wrote, or text the user sent."""

    text: str


@dataclass(frozen=True, slots=True)
class ReasoningBlock:
    """What the model thought before answering.

    A vendor that signs its reasoning gets it back through `Message.vendor`, never
    from this block.
    """

    text: str

    redacted: bool = False
    """The vendor withheld the text; `text` is empty."""


@dataclass(frozen=True, slots=True)
class ToolUseBlock:
    """The model calling one of the caller's tools."""

    id: str
    name: str
    input: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ToolResultBlock:
    """What a tool answered, in a user message, before any text of the user's."""

    tool_use_id: str
    content: str
    is_error: bool = False


@dataclass(frozen=True, slots=True)
class ServerToolUseBlock:
    """The vendor running one of its own tools, such as web search, for the model."""

    id: str
    name: str
    input: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ServerToolResultBlock:
    """The vendor's tool answered; the vendor keeps what it said."""

    tool_use_id: str


ContentBlock = (
    TextBlock
    | ReasoningBlock
    | ToolUseBlock
    | ToolResultBlock
    | ServerToolUseBlock
    | ServerToolResultBlock
)


@dataclass(frozen=True, slots=True)
class VendorContent:
    """An assistant message as its provider returned it.

    Replayed as it is to any provider of the same kind, so that nothing is lost:
    signed reasoning, citations, the vendor's own tool blocks. A provider of
    another kind never sees it.
    """

    kind: str
    """The kind of the provider that returned it (docs/specs/providers.md)."""

    content: Any


@dataclass(frozen=True, slots=True)
class Message:
    role: Role
    content: tuple[ContentBlock, ...]
    vendor: VendorContent | None = None

    @classmethod
    def user(cls, text: str) -> Message:
        return cls("user", (TextBlock(text),))

    @classmethod
    def assistant(cls, text: str) -> Message:
        return cls("assistant", (TextBlock(text),))

    @classmethod
    def tool_results(cls, *results: ToolResultBlock) -> Message:
        """The user message that answers the tool calls of an assistant message."""
        return cls("user", results)

    @property
    def text(self) -> str:
        """Every text block, joined."""
        return "".join(b.text for b in self.content if isinstance(b, TextBlock))

    @property
    def tool_uses(self) -> tuple[ToolUseBlock, ...]:
        return tuple(b for b in self.content if isinstance(b, ToolUseBlock))


@dataclass(frozen=True, slots=True)
class Tool:
    """A tool the model may call; the caller runs it."""

    name: str
    description: str
    input_schema: Mapping[str, Any]
    """A JSON Schema object describing the input."""


class StopReason(StrEnum):
    """Why the model stopped."""

    END_TURN = "end_turn"
    """The answer is complete."""

    TOOL_USE = "tool_use"
    """The model waits for the results of the tool calls in the message."""

    MAX_TOKENS = "max_tokens"
    """The output limit cut the message short."""

    PAUSE_TURN = "pause_turn"
    """A vendor's tool loop paused; send the message back to continue it."""


@dataclass(frozen=True, slots=True)
class Usage:
    input_tokens: int = 0
    """Every input token, the cached ones included."""

    output_tokens: int = 0
    """Every output token, the reasoning included."""

    cached_input_tokens: int = 0
    """Of `input_tokens`, those read from the vendor's cache."""

    reasoning_tokens: int = 0
    """Of `output_tokens`, those spent reasoning, when the vendor counts them."""


@dataclass(frozen=True, slots=True)
class Response:
    """What `complete` returns and a stream ends with."""

    message: Message
    stop_reason: StopReason
    usage: Usage

    @property
    def text(self) -> str:
        return self.message.text

    @property
    def tool_uses(self) -> tuple[ToolUseBlock, ...]:
        return self.message.tool_uses
