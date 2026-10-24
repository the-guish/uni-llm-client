# Copyright 2026 The uni-llm-client Authors
# SPDX-License-Identifier: Apache-2.0

"""The JSON form of messages and responses: docs/specs/messages.md, Serialisation."""

import json
from types import MappingProxyType
from typing import Any

import pytest

from uni_llm_client import (
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

BLOCKS: dict[str, ContentBlock] = {
    "text": TextBlock("Paris."),
    "reasoning": ReasoningBlock("The user wants a city."),
    "redacted-reasoning": ReasoningBlock("", redacted=True),
    "tool-use": ToolUseBlock("toolu_1", "get_weather", {"city": "Paris", "days": [1]}),
    "tool-result": ToolResultBlock("toolu_1", "18°C", is_error=True),
    "server-tool-use": ServerToolUseBlock("srvtoolu_1", "web_search", {"query": "a"}),
    "server-tool-result": ServerToolResultBlock("srvtoolu_1"),
}
VENDOR = VendorContent("anthropic", [{"type": "text", "text": "Paris.", "id": None}])
MESSAGE = Message("assistant", tuple(BLOCKS.values()), VENDOR)
RESPONSE = Response(MESSAGE, StopReason.TOOL_USE, Usage(10, 20, 5, 3))
USER = {"role": "user", "content": [{"type": "text", "text": "Hi"}]}


@pytest.mark.parametrize("block", BLOCKS.values(), ids=BLOCKS.keys())
def test_every_block_reads_back_as_itself(block: ContentBlock) -> None:
    message = Message("user", (block,))

    assert Message.from_dict(message.to_dict()) == message


def test_a_message_and_a_response_read_back_as_themselves() -> None:
    assert Message.from_dict(MESSAGE.to_dict()) == MESSAGE
    assert Response.from_dict(RESPONSE.to_dict()) == RESPONSE


def test_the_form_survives_json() -> None:
    message = json.loads(json.dumps(MESSAGE.to_dict()))
    response = json.loads(json.dumps(RESPONSE.to_dict()))

    assert Message.from_dict(message) == MESSAGE
    assert Response.from_dict(response) == RESPONSE


def test_the_form_holds_every_field_and_a_type_per_block() -> None:
    reply = Response(Message.user("Hi"), StopReason.END_TURN, Usage())

    assert reply.to_dict() == {
        "message": {**USER, "vendor": None},
        "stop_reason": "end_turn",
        "usage": {
            "input_tokens": 0,
            "output_tokens": 0,
            "cached_input_tokens": 0,
            "reasoning_tokens": 0,
        },
    }
    assert Message.tool_results(ToolResultBlock("t", "ok")).to_dict()["content"] == [
        {"type": "tool_result", "tool_use_id": "t", "content": "ok", "is_error": False}
    ]
    assert [block["type"] for block in MESSAGE.to_dict()["content"]] == [
        "text",
        "reasoning",
        "reasoning",
        "tool_use",
        "tool_result",
        "server_tool_use",
        "server_tool_result",
    ]


def test_the_form_is_a_copy_made_of_json_values() -> None:
    call = ToolUseBlock("t", "f", MappingProxyType({"days": (1, 2)}))
    data = Message("assistant", (call,), VENDOR).to_dict()

    assert type(data["content"][0]["input"]) is dict
    assert data["content"][0]["input"] == {"days": [1, 2]}
    data["vendor"]["content"][0]["text"] = "Lyon."
    assert VENDOR.content[0]["text"] == "Paris."

    source = MESSAGE.to_dict()
    message = Message.from_dict(source)
    source["content"][3]["input"]["city"] = "Lyon"
    source["vendor"]["content"][0]["text"] = "Lyon."
    assert message == MESSAGE


@pytest.mark.parametrize(
    ("value", "error"),
    [
        pytest.param({"days": {1, 2}}, r"tool_use\.input holds a set", id="a-set"),
        pytest.param({1: "Monday"}, "a key that is not a string", id="an-int-key"),
    ],
)
def test_a_value_json_cannot_hold_is_refused(value: Any, error: str) -> None:
    with pytest.raises(ValueError, match=error):
        Message("assistant", (ToolUseBlock("t", "f", value),)).to_dict()


def test_fields_with_a_default_may_be_left_out() -> None:
    data = {
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": "t", "content": "ok"},
            {"type": "reasoning", "text": ""},
            {"type": "tool_use", "id": "t", "name": "f"},
        ],
    }

    assert Message.from_dict(data) == Message(
        "user", (ToolResultBlock("t", "ok"), ReasoningBlock(""), ToolUseBlock("t", "f"))
    )


def content(*blocks: Any) -> dict[str, Any]:
    return {"role": "user", "content": list(blocks)}


@pytest.mark.parametrize(
    ("data", "error"),
    [
        pytest.param({**USER, "role": "system"}, "Unknown role 'system'", id="role"),
        pytest.param(
            content({"type": "image"}), "unknown type 'image'", id="block-type"
        ),
        pytest.param(content({"text": "Hi"}), "has no 'type'", id="no-block-type"),
        pytest.param(content({"type": "text"}), "has no 'text'", id="missing-field"),
        pytest.param({"content": []}, "message has no 'role'", id="missing-role"),
        pytest.param(
            {**USER, "metadata": {}}, "unknown field 'metadata'", id="message-field"
        ),
        pytest.param(
            content({"type": "text", "text": "Hi", "cache_control": {}}),
            "unknown field 'cache_control'",
            id="block-field",
        ),
        pytest.param(
            {"role": "user", "content": "Hi"},
            "content must be an array",
            id="content-of-text",
        ),
        pytest.param(content("Hi"), r"content\[0\] must be an object", id="block"),
        pytest.param(
            content({"type": "tool_result", "tool_use_id": "t", "content": "ok"}, 1),
            r"content\[1\] must be an object",
            id="second-block",
        ),
        pytest.param(
            content(
                {
                    "type": "tool_result",
                    "tool_use_id": "t",
                    "content": "a",
                    "is_error": 0,
                }
            ),
            "is_error must be true or false",
            id="field-type",
        ),
        pytest.param(
            content({"type": "tool_use", "id": "t", "name": "f", "input": "{}"}),
            "input must be an object",
            id="input-of-text",
        ),
        pytest.param(
            {**USER, "vendor": {"kind": "anthropic"}},
            "vendor has no 'content'",
            id="vendor",
        ),
        pytest.param("Hi", "message must be an object", id="not-an-object"),
    ],
)
def test_a_message_of_another_form_is_refused(data: Any, error: str) -> None:
    with pytest.raises(ValueError, match=error):
        Message.from_dict(data)


RESPONSE_DICT = RESPONSE.to_dict()


@pytest.mark.parametrize(
    ("data", "error"),
    [
        pytest.param(
            {**RESPONSE_DICT, "stop_reason": "stop"},
            "Unknown stop reason 'stop'",
            id="stop-reason",
        ),
        pytest.param(
            {**RESPONSE_DICT, "usage": {"input_tokens": True}},
            r"response\.usage\.input_tokens must be an integer",
            id="usage",
        ),
        pytest.param(
            {k: v for k, v in RESPONSE_DICT.items() if k != "usage"},
            "response has no 'usage'",
            id="missing-usage",
        ),
        pytest.param(
            {**RESPONSE_DICT, "message": {**USER, "role": "system"}},
            "Unknown role 'system'",
            id="message",
        ),
    ],
)
def test_a_response_of_another_form_is_refused(data: Any, error: str) -> None:
    with pytest.raises(ValueError, match=error):
        Response.from_dict(data)
