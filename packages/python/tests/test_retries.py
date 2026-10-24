# Copyright 2026 The uni-llm-client Authors
# SPDX-License-Identifier: Apache-2.0

"""Retries (docs/specs/retries.md): the policy's decisions, then both clients."""

import time
from email.utils import formatdate

import httpx2
import pytest
from conftest import Ask, FakeVendor, Stream

from uni_llm_client import (
    AsyncClient,
    AuthenticationError,
    Client,
    Completed,
    IncompleteResponseError,
    ProviderError,
    RateLimitError,
    RefusalError,
    StreamEvent,
    TextDelta,
    TransportError,
    UniLLMError,
)
from uni_llm_client._retry import RetryPolicy, backoff, is_retryable, retry_after

QUESTION = "What is the capital of France?"
ANSWER = "Paris is the capital of France."

NOW = 1_800_000_000.0  # Fri, 15 Jan 2027 08:00:00 GMT
RATE_LIMITED = RateLimitError("Rate limit exceeded", status_code=429)


def delay(headers: dict[str, str]) -> float | None:
    """The policy's delay before the second retry, after a rate limit whose
    response has `headers`; without jitter, so that a backoff is 1 s."""
    response = httpx2.Response(429, headers=headers)
    return RetryPolicy().delay(RATE_LIMITED, 1, response, now=NOW, jitter=0)


@pytest.mark.parametrize(
    ("error", "retryable"),
    [
        pytest.param(RATE_LIMITED, True, id="429"),
        pytest.param(ProviderError("", status_code=408), True, id="408"),
        pytest.param(ProviderError("", status_code=409), True, id="409"),
        pytest.param(ProviderError("", status_code=500), True, id="500"),
        pytest.param(ProviderError("", status_code=502), True, id="502"),
        pytest.param(ProviderError("", status_code=503), True, id="503"),
        pytest.param(ProviderError("", status_code=529), True, id="529"),
        pytest.param(TransportError("Connection refused"), True, id="transport"),
        pytest.param(ProviderError("", status_code=400), False, id="400"),
        pytest.param(ProviderError("", status_code=404), False, id="404"),
        pytest.param(ProviderError("", status_code=413), False, id="413"),
        pytest.param(AuthenticationError("", status_code=401), False, id="401"),
        pytest.param(AuthenticationError("", status_code=403), False, id="403"),
        pytest.param(ProviderError("Unknown stop reason"), False, id="no-status"),
        pytest.param(RefusalError("Declined"), False, id="refusal"),
        pytest.param(IncompleteResponseError("Cut"), False, id="incomplete"),
    ],
)
def test_retries_only_what_may_pass_on_another_attempt(
    error: UniLLMError, retryable: bool
) -> None:
    assert is_retryable(error) is retryable


def test_raises_once_the_retries_are_spent() -> None:
    policy = RetryPolicy(max_retries=2)

    delays = [policy.delay(RATE_LIMITED, n, None, now=NOW, jitter=0) for n in range(4)]

    assert delays == [0.5, 1.0, None, None]


def test_zero_max_retries_disables_retries() -> None:
    policy = RetryPolicy(max_retries=0)

    assert policy.delay(RATE_LIMITED, 0, None, now=NOW, jitter=0) is None


def test_does_not_retry_an_error_that_would_recur() -> None:
    error = AuthenticationError("invalid x-api-key", status_code=401)

    assert RetryPolicy().delay(error, 0, None, now=NOW, jitter=0) is None


@pytest.mark.parametrize("client", [Client, AsyncClient])
def test_rejects_a_negative_max_retries(
    client: type[Client] | type[AsyncClient],
) -> None:
    with pytest.raises(ValueError):
        client(max_retries=-1)


@pytest.mark.parametrize(
    ("retries", "longest"),
    [(0, 0.5), (1, 1.0), (2, 2.0), (3, 4.0), (4, 8.0), (5, 8.0), (10_000, 8.0)],
)
def test_backs_off_exponentially_up_to_8_seconds_with_jitter(
    retries: int, longest: float
) -> None:
    assert backoff(retries, jitter=0) == longest
    for jitter in (0.25, 0.5, 0.999):
        assert 0.75 * longest < backoff(retries, jitter) < longest


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ({"retry-after": "0"}, 0.0),
        ({"retry-after": "7"}, 7.0),
        ({"retry-after": "1.5"}, 1.5),
        ({"retry-after": "60"}, 60.0),
        ({"Retry-After": "7"}, 7.0),
        ({"retry-after-ms": "250"}, 0.25),
        ({"retry-after-ms": "0"}, 0.0),
        ({"retry-after-ms": "250", "retry-after": "7"}, 0.25),
        ({"retry-after-ms": "soon", "retry-after": "7"}, 7.0),
        ({"retry-after": formatdate(NOW + 30, usegmt=True)}, 30.0),
        ({"retry-after": formatdate(NOW + 60, usegmt=True)}, 60.0),
    ],
)
def test_waits_as_long_as_the_vendor_asks(
    headers: dict[str, str], expected: float
) -> None:
    assert delay(headers) == pytest.approx(expected)


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"retry-after": "61"},
        {"retry-after": "3600"},
        {"retry-after-ms": "60001"},
        {"retry-after": "-1"},
        {"retry-after": "nan"},
        {"retry-after": "inf"},
        {"retry-after": "soon"},
        {"retry-after": ""},
        {"retry-after": formatdate(NOW + 61, usegmt=True)},
        {"retry-after": formatdate(NOW - 5, usegmt=True)},
    ],
)
def test_backs_off_when_the_vendor_asks_for_too_long_or_nothing_readable(
    headers: dict[str, str],
) -> None:
    assert delay(headers) == backoff(1, jitter=0)


def test_reads_an_http_date_in_any_zone() -> None:
    in_utc = formatdate(NOW + 30, usegmt=True)  # "... GMT"
    unknown_zone = formatdate(NOW + 30)  # "... -0000", which is UTC
    in_paris = "Fri, 15 Jan 2027 09:00:30 +0100"

    assert retry_after({"retry-after": in_utc}, NOW) == 30
    assert retry_after({"retry-after": unknown_zone}, NOW) == 30
    assert retry_after({"retry-after": in_paris}, NOW) == 30


def test_retries_until_the_vendor_answers(
    ask: Ask, vendor: FakeVendor, model: str, sleeps: list[float]
) -> None:
    # OpenRouter's server error is an error in the body of a 200 (RETRY-2).
    vendor.serve(model, "rate_limit", "server_error", "answer")

    assert ask(model, QUESTION) == ANSWER
    first, second, third = vendor.requests
    assert first.url == second.url == third.url
    assert first.content == second.content == third.content
    assert len(sleeps) == 2


def test_raises_the_last_error_once_the_retries_are_spent(
    ask: Ask, vendor: FakeVendor, model: str
) -> None:
    vendor.serve(model, "server_error", "server_error", "rate_limit", "answer")

    with pytest.raises(RateLimitError):
        ask(model, QUESTION)
    assert len(vendor.requests) == 3


def test_max_retries_sets_the_number_of_retries(
    ask: Ask, vendor: FakeVendor, model: str
) -> None:
    vendor.serve(model, *["rate_limit"] * 4, "answer")

    assert ask(model, QUESTION, max_retries=4) == ANSWER
    assert len(vendor.requests) == 5


def test_max_retries_zero_sends_once(
    ask: Ask, vendor: FakeVendor, model: str, sleeps: list[float]
) -> None:
    vendor.serve(model, "rate_limit", "answer")

    with pytest.raises(RateLimitError):
        ask(model, QUESTION, max_retries=0)
    assert len(vendor.requests) == 1
    assert sleeps == []


def test_waits_as_long_as_retry_after_asks(
    ask: Ask, vendor: FakeVendor, model: str, sleeps: list[float]
) -> None:
    vendor.serve(model, "retry_after", "answer")

    assert ask(model, QUESTION) == ANSWER
    assert sleeps == [7.0]


def test_backs_off_without_sleeping_for_real(
    ask: Ask, vendor: FakeVendor, model: str, sleeps: list[float]
) -> None:
    vendor.serve(model, *["rate_limit"] * 5, "answer")

    start = time.monotonic()
    assert ask(model, QUESTION, max_retries=5) == ANSWER
    elapsed = time.monotonic() - start

    for waited, longest in zip(sleeps, [0.5, 1.0, 2.0, 4.0, 8.0], strict=True):
        assert 0.75 * longest < waited <= longest
    # Over 11 seconds of waits, and none of them slept.
    assert elapsed < sum(sleeps)


@pytest.mark.parametrize(
    "error",
    [
        httpx2.ConnectError,
        httpx2.ConnectTimeout,
        httpx2.ReadTimeout,
        httpx2.WriteError,
        httpx2.RemoteProtocolError,
    ],
)
def test_retries_a_lost_connection_or_a_timeout(
    ask: Ask, vendor: FakeVendor, model: str, error: type[httpx2.TransportError]
) -> None:
    vendor.fail(error("The network failed"))
    vendor.serve(model, "answer")

    assert ask(model, QUESTION) == ANSWER
    first, second = vendor.requests
    assert first.content == second.content


def test_raises_a_transport_error_caused_by_the_http_library(
    ask: Ask, vendor: FakeVendor, model: str
) -> None:
    errors = [httpx2.ConnectError("Connection refused") for _ in range(3)]
    for error in errors:
        vendor.fail(error)

    with pytest.raises(TransportError, match="Connection refused") as excinfo:
        ask(model, QUESTION)
    assert isinstance(excinfo.value, UniLLMError)
    assert excinfo.value.__cause__ is errors[-1]
    assert len(vendor.requests) == 3


def test_stream_retries_until_the_vendor_accepts_it(
    stream: Stream, vendor: FakeVendor, model: str
) -> None:
    vendor.fail(httpx2.ConnectError("Connection refused"))
    vendor.serve(model, "rate_limit", "stream_answer")

    events = stream(model, QUESTION)

    assert events[0] == TextDelta(ANSWER)
    assert isinstance(events[-1], Completed)
    assert events[-1].response.message.text == ANSWER
    first, second, third = vendor.requests
    assert first.content == second.content == third.content
    assert all(response.is_closed for response in vendor.responses)


def test_stream_does_not_retry_once_an_event_is_yielded(
    stream: Stream, vendor: FakeVendor, model: str
) -> None:
    vendor.serve(model, "stream_interrupted", "stream_answer")
    received: list[StreamEvent] = []

    with pytest.raises(TransportError) as excinfo:
        stream(model, QUESTION, received=received)
    assert received == [TextDelta("Par")]
    assert isinstance(excinfo.value.__cause__, httpx2.RemoteProtocolError)
    assert len(vendor.requests) == 1
    assert all(response.is_closed for response in vendor.responses)
