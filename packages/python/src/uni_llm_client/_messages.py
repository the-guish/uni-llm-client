# Copyright 2026 The uni-llm-client Authors
# SPDX-License-Identifier: Apache-2.0

"""The conversation model, as specified in docs/specs/messages.md.

Vendor-neutral: every provider translates these to its own API and back. Messages
and responses have a JSON form of their own, for callers that store them.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import MISSING, asdict, dataclass, field, fields
from enum import StrEnum
from typing import Any, Literal, get_args

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

    def to_dict(self) -> dict[str, Any]:
        """The message's JSON form: every field, as JSON values alone (MSG-9)."""
        vendor = self.vendor
        return {
            "role": self.role,
            "content": [_block_dict(block) for block in self.content],
            "vendor": None
            if vendor is None
            else {
                "kind": vendor.kind,
                "content": _json(vendor.content, "vendor.content"),
            },
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Message:
        """The message whose JSON form `data` is (MSG-11).

        Raises:
            ValueError: `data` is not the JSON form of a message (MSG-12).
        """
        return _message_of(data, "message")


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

    def to_dict(self) -> dict[str, Any]:
        """The response's JSON form: every field, as JSON values alone (MSG-9)."""
        return {
            "message": self.message.to_dict(),
            "stop_reason": self.stop_reason.value,
            "usage": asdict(self.usage),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Response:
        """The response whose JSON form `data` is (MSG-11).

        Raises:
            ValueError: `data` is not the JSON form of a response (MSG-12).
        """
        data = _object(data, "response", ("message", "stop_reason", "usage"))
        stop_reason = data["stop_reason"]
        if stop_reason not in tuple(StopReason):
            raise ValueError(f"Unknown stop reason {stop_reason!r}")
        return cls(
            message=_message_of(data["message"], "response.message"),
            stop_reason=StopReason(stop_reason),
            usage=_dataclass_of(Usage, data["usage"], "response.usage"),
        )


# The JSON form (docs/specs/messages.md, Serialisation).

_ROLES = get_args(Role)

# The `type` of each block (MSG-10).
_BLOCK_TYPES: dict[str, type[ContentBlock]] = {
    "text": TextBlock,
    "reasoning": ReasoningBlock,
    "tool_use": ToolUseBlock,
    "tool_result": ToolResultBlock,
    "server_tool_use": ServerToolUseBlock,
    "server_tool_result": ServerToolResultBlock,
}
_BLOCK_NAMES = {cls: name for name, cls in _BLOCK_TYPES.items()}

# The JSON type of each field of a block and of the usage.
_FIELD_TYPES: dict[str, type] = {
    "text": str,
    "redacted": bool,
    "id": str,
    "name": str,
    "input": dict,
    "tool_use_id": str,
    "content": str,
    "is_error": bool,
    "input_tokens": int,
    "output_tokens": int,
    "cached_input_tokens": int,
    "reasoning_tokens": int,
}
_TYPE_NAMES = {
    dict: "an object",
    list: "an array",
    str: "a string",
    bool: "true or false",
    int: "an integer",
}


def _block_dict(block: ContentBlock) -> dict[str, Any]:
    name = _BLOCK_NAMES[type(block)]
    return {
        "type": name,
        **{
            f.name: _json(getattr(block, f.name), f"{name}.{f.name}")
            for f in fields(block)
        },
    }


def _message_of(data: Any, what: str) -> Message:
    data = _object(data, what, ("role", "content"), ("vendor",))
    if data["role"] not in _ROLES:
        raise ValueError(f"Unknown role {data['role']!r}")
    content = _checked(data["content"], list, f"{what}.content")
    blocks = tuple(
        _block_of(block, f"{what}.content[{i}]") for i, block in enumerate(content)
    )
    if data.get("vendor") is None:
        return Message(data["role"], blocks)
    vendor = _object(data["vendor"], f"{what}.vendor", ("kind", "content"))
    return Message(
        data["role"],
        blocks,
        VendorContent(
            _checked(vendor["kind"], str, f"{what}.vendor.kind"),
            _json(vendor["content"], f"{what}.vendor.content"),
        ),
    )


def _block_of(data: Any, what: str) -> ContentBlock:
    if not isinstance(data, Mapping):
        raise ValueError(f"{what} must be an object")
    if "type" not in data:
        raise ValueError(f"{what} has no 'type'")
    kind = data["type"]
    if not isinstance(kind, str) or kind not in _BLOCK_TYPES:
        raise ValueError(f"{what} has an unknown type {kind!r}")
    block: ContentBlock = _dataclass_of(_BLOCK_TYPES[kind], data, what, "type")
    return block


def _dataclass_of(cls: Any, data: Any, what: str, *tags: str) -> Any:
    """The dataclass `cls` of the JSON object `data`, which holds `tags` and the
    fields of `cls`, each of its JSON type; those with a default may be left out."""
    names = [f.name for f in fields(cls)]
    required = [
        f.name
        for f in fields(cls)
        if f.default is MISSING and f.default_factory is MISSING
    ]
    data = _object(data, what, (*tags, *required), names)
    values: dict[str, Any] = {}
    for name in names:
        if name in data:
            path = f"{what}.{name}"
            values[name] = _json(_checked(data[name], _FIELD_TYPES[name], path), path)
    return cls(**values)


def _object(
    data: Any, what: str, required: Collection[str], optional: Collection[str] = ()
) -> Mapping[str, Any]:
    """`data`, if it is an object with every key `required` and no unknown one."""
    if not isinstance(data, Mapping):
        raise ValueError(f"{what} must be an object")
    for key in required:
        if key not in data:
            raise ValueError(f"{what} has no {key!r}")
    for key in data:
        if key not in required and key not in optional:
            raise ValueError(f"{what} has an unknown field {key!r}")
    return data


def _checked(value: Any, expected: type, what: str) -> Any:
    """`value`, if it is of the JSON type `expected`."""
    if expected is dict:
        valid = isinstance(value, Mapping)
    else:
        # A bool is an int to Python, not to JSON.
        valid = isinstance(value, expected) and (
            expected is bool or not isinstance(value, bool)
        )
    if not valid:
        raise ValueError(f"{what} must be {_TYPE_NAMES[expected]}")
    return value


def _json(value: Any, what: str) -> Any:
    """A copy of `value` made of JSON values alone: dicts with string keys, lists,
    strings, numbers, booleans and None (MSG-9)."""
    if value is None or isinstance(value, str | int | float):
        return value
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise ValueError(f"{what} has a key that is not a string")
        return {key: _json(item, what) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json(item, what) for item in value]
    raise ValueError(f"{what} holds a {type(value).__name__}, not a JSON value")
