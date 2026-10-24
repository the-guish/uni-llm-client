# Copyright 2026 The uni-llm-client Authors
# SPDX-License-Identifier: Apache-2.0

import pytest
from conftest import Ask, FakeVendor

from uni_llm_client import (
    AuthenticationError,
    Effort,
    IncompleteResponseError,
    ProviderError,
    RateLimitError,
    RefusalError,
    UniLLMError,
)
from uni_llm_client._registry import PROVIDERS

QUESTION = "What is the capital of France?"
ANSWER = "Paris is the capital of France."


def test_returns_the_answer(ask: Ask, vendor: FakeVendor, model: str) -> None:
    vendor.serve(model, "answer")

    assert ask(model, QUESTION) == ANSWER


@pytest.mark.parametrize("effort", [None, *Effort, "low"])
def test_accepts_every_effort(
    ask: Ask, vendor: FakeVendor, model: str, effort: Effort | str | None
) -> None:
    vendor.serve(model, "answer")

    assert ask(model, QUESTION, effort=effort) == ANSWER


@pytest.mark.parametrize("effort", [None, *Effort])
def test_same_arguments_send_the_same_request(
    ask: Ask, vendor: FakeVendor, model: str, effort: Effort | None
) -> None:
    for _ in range(2):
        vendor.serve(model, "answer")
        ask(model, QUESTION, effort=effort, web_search=True)

    first, second = vendor.requests
    assert first.url == second.url
    assert first.content == second.content


def test_web_search_returns_only_the_final_answer(
    ask: Ask, vendor: FakeVendor, model: str
) -> None:
    vendor.serve(model, "web_search")

    assert ask(model, QUESTION, web_search=True) == ANSWER


def test_resumes_a_paused_turn(ask: Ask, vendor: FakeVendor, model: str) -> None:
    vendor.serve(model, "paused_turn")

    assert ask(model, QUESTION, web_search=True) == ANSWER
    assert len(vendor.requests) == 2


@pytest.mark.parametrize(
    ("scenario", "error", "requests"),
    [
        ("truncated", IncompleteResponseError, 1),
        ("refusal", RefusalError, 1),
        ("auth_error", AuthenticationError, 1),
        # The first attempt and the two default retries (RETRY-1, RETRY-6).
        ("rate_limit", RateLimitError, 3),
        ("server_error", ProviderError, 3),
    ],
)
def test_raises_on_unsuccessful_responses(
    ask: Ask,
    vendor: FakeVendor,
    model: str,
    scenario: str,
    error: type[UniLLMError],
    requests: int,
) -> None:
    vendor.serve(model, *[scenario] * requests)

    with pytest.raises(error) as excinfo:
        ask(model, QUESTION)
    assert excinfo.type is error
    assert len(vendor.requests) == requests


def test_rejects_an_invalid_effort_before_sending(
    ask: Ask, vendor: FakeVendor, model: str
) -> None:
    with pytest.raises(ValueError):
        ask(model, QUESTION, effort="extreme")
    assert vendor.requests == []


@pytest.mark.parametrize("model_id", ["missing-slash", "unknown/model"])
def test_rejects_an_invalid_model_id_before_sending(
    ask: Ask, vendor: FakeVendor, model_id: str
) -> None:
    with pytest.raises(ValueError):
        ask(model_id, QUESTION)
    assert vendor.requests == []


def test_requires_an_api_key(
    ask: Ask, vendor: FakeVendor, model: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = PROVIDERS[model.partition("/")[0]]
    monkeypatch.delenv(provider.api_key_env, raising=False)

    with pytest.raises(AuthenticationError):
        ask(model, QUESTION, api_keys=None)
    assert vendor.requests == []
