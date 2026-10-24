# Copyright 2026 The uni-llm-client Authors
# SPDX-License-Identifier: Apache-2.0

"""Providers of the caller's own, and the timeout of a call: docs/specs/providers.md."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Mapping
from typing import Any

import httpx2
import pytest

from uni_llm_client import (
    AsyncClient,
    AuthenticationError,
    Client,
    Message,
    ProviderConfig,
    TextBlock,
    VendorContent,
)

QUESTION = "What is the capital of France?"
# The HTTP client's own timeout, which httpx2 stamps on a request given none.
CLIENT_TIMEOUT = httpx2.Timeout(7.0)
GATEWAY = ProviderConfig("anthropic", "gateway-key", "https://llm.corp/anthropic/")
LOCAL = ProviderConfig("openrouter", "local-key", "https://ollama.corp/v1")

ANTHROPIC_ANSWER = {
    "id": "msg_01",
    "type": "message",
    "role": "assistant",
    "model": "claude-opus-5",
    "content": [{"type": "text", "text": "Paris."}],
    "stop_reason": "end_turn",
    "stop_sequence": None,
    "usage": {"input_tokens": 1, "output_tokens": 1},
}
OPENROUTER_ANSWER = {
    "id": "gen-01",
    "choices": [
        {"finish_reason": "stop", "message": {"role": "assistant", "content": "Paris."}}
    ],
    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
}

Ask = Callable[..., str]


class Recorder:
    """Answers every request the same and keeps the requests."""

    def __init__(self, body: dict[str, Any]) -> None:
        self.body = body
        self.requests: list[httpx2.Request] = []

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return httpx2.Response(200, json=self.body)


@pytest.fixture(params=["sync", "async"])
def ask(request: pytest.FixtureRequest) -> Ask:
    """Create a client with `providers` and `api_keys` over `recorder`, and ask."""

    def ask_sync(
        recorder: Recorder,
        model: str,
        *,
        providers: Mapping[str, ProviderConfig] | None = None,
        api_keys: Mapping[str, str] | None = None,
        **kwargs: Any,
    ) -> str:
        http = httpx2.Client(
            transport=httpx2.MockTransport(recorder.handle), timeout=CLIENT_TIMEOUT
        )
        with Client(api_keys=api_keys, providers=providers, http_client=http) as client:
            return client.ask(model, QUESTION, **kwargs)

    def ask_async(
        recorder: Recorder,
        model: str,
        *,
        providers: Mapping[str, ProviderConfig] | None = None,
        api_keys: Mapping[str, str] | None = None,
        **kwargs: Any,
    ) -> str:
        async def run() -> str:
            http = httpx2.AsyncClient(
                transport=httpx2.MockTransport(recorder.handle), timeout=CLIENT_TIMEOUT
            )
            async with AsyncClient(
                api_keys=api_keys, providers=providers, http_client=http
            ) as client:
                return await client.ask(model, QUESTION, **kwargs)

        return asyncio.run(run())

    return ask_sync if request.param == "sync" else ask_async


def test_a_configured_provider_sends_to_its_base_url_with_its_key(ask: Ask) -> None:
    recorder = Recorder(ANTHROPIC_ANSWER)

    assert (
        ask(recorder, "gateway/claude-opus-5", providers={"gateway": GATEWAY})
        == "Paris."
    )

    (request,) = recorder.requests
    assert str(request.url) == "https://llm.corp/anthropic/v1/messages"
    assert request.headers["x-api-key"] == "gateway-key"
    assert json.loads(request.content)["model"] == "claude-opus-5"


def test_the_openrouter_kind_at_a_base_url(ask: Ask) -> None:
    recorder = Recorder(OPENROUTER_ANSWER)

    assert ask(recorder, "local/llama4", providers={"local": LOCAL}) == "Paris."

    (request,) = recorder.requests
    assert str(request.url) == "https://ollama.corp/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer local-key"
    assert json.loads(request.content)["model"] == "llama4"


def test_the_built_ins_keep_their_urls(ask: Ask) -> None:
    recorder = Recorder(ANTHROPIC_ANSWER)
    ask(recorder, "anthropic/claude-opus-5", api_keys={"anthropic": "k"})
    recorder.body = OPENROUTER_ANSWER
    ask(recorder, "openrouter/x/y", api_keys={"openrouter": "k"})

    assert [str(r.url) for r in recorder.requests] == [
        "https://api.anthropic.com/v1/messages",
        "https://openrouter.ai/api/v1/chat/completions",
    ]


def test_a_configured_provider_never_reads_the_environment(
    ask: Ask, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "real-key")
    recorder = Recorder(ANTHROPIC_ANSWER)
    keyless = {"gateway": ProviderConfig("anthropic", base_url="https://llm.corp")}

    with pytest.raises(AuthenticationError, match="gateway"):
        ask(recorder, "gateway/claude-opus-5", providers=keyless)
    assert recorder.requests == []

    # Replacing a built-in takes its environment variable away too.
    replaced = {"anthropic": ProviderConfig("anthropic", base_url="https://llm.corp")}
    with pytest.raises(AuthenticationError):
        ask(recorder, "anthropic/claude-opus-5", providers=replaced)
    assert recorder.requests == []


@pytest.mark.parametrize(
    ("providers", "api_keys", "message"),
    [
        ({"x": ProviderConfig("nope", "k")}, None, "Unknown kind"),
        ({"a/b": ProviderConfig("anthropic", "k")}, None, "Invalid provider name"),
        ({"": ProviderConfig("anthropic", "k")}, None, "Invalid provider name"),
        ({"gateway": GATEWAY}, {"gateway": "k"}, "both api_keys and providers"),
        (None, {"nope": "k"}, "Unknown provider"),
    ],
)
def test_a_bad_configuration_is_refused_when_the_client_is_created(
    providers: Mapping[str, ProviderConfig] | None,
    api_keys: Mapping[str, str] | None,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        Client(api_keys=api_keys, providers=providers)
    with pytest.raises(ValueError, match=message):
        AsyncClient(api_keys=api_keys, providers=providers)


def test_replay_is_per_kind(ask: Ask) -> None:
    """An Anthropic message goes to the gateway as it came; to OpenRouter as blocks."""
    raw = [
        {"type": "text", "text": "Hello"},
        {"type": "thinking", "thinking": "t", "signature": "s"},
    ]
    conversation = [
        Message.user("Hi"),
        Message("assistant", (TextBlock("Hello"),), VendorContent("anthropic", raw)),
        Message.user("Go on"),
    ]
    recorder = Recorder(ANTHROPIC_ANSWER)
    http = httpx2.Client(transport=httpx2.MockTransport(recorder.handle))
    with Client(
        providers={"gateway": GATEWAY, "local": LOCAL}, http_client=http
    ) as client:
        client.complete("gateway/claude-opus-5", conversation)
        recorder.body = OPENROUTER_ANSWER
        client.complete("local/llama4", conversation)

    to_gateway, to_local = (
        json.loads(r.content)["messages"][1] for r in recorder.requests
    )
    assert to_gateway == {"role": "assistant", "content": raw}
    assert to_local == {"role": "assistant", "content": "Hello"}


def test_timeout_bounds_each_operation_of_the_request(ask: Ask) -> None:
    recorder = Recorder(ANTHROPIC_ANSWER)

    ask(recorder, "anthropic/claude-opus-5", api_keys={"anthropic": "k"}, timeout=30)
    ask(recorder, "anthropic/claude-opus-5", api_keys={"anthropic": "k"})

    with_timeout, without = recorder.requests
    assert with_timeout.extensions["timeout"] == {
        "connect": 30.0,
        "read": 30.0,
        "write": 30.0,
        "pool": 30.0,
    }
    assert without.extensions["timeout"] == CLIENT_TIMEOUT.as_dict()


@pytest.mark.parametrize("timeout", [0, -1.0])
def test_a_timeout_that_is_not_positive_is_refused_before_sending(
    ask: Ask, timeout: float
) -> None:
    recorder = Recorder(ANTHROPIC_ANSWER)
    with pytest.raises(ValueError, match="timeout"):
        ask(
            recorder,
            "anthropic/claude-opus-5",
            api_keys={"anthropic": "k"},
            timeout=timeout,
        )
    assert recorder.requests == []
