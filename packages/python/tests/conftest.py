# Copyright 2026 The uni-llm-client Authors
# SPDX-License-Identifier: Apache-2.0

"""Fixtures that run each test for every model in MODELS, sync and async.

Tests only use the public API. Vendor-specific data lives in
``fixtures/<provider>/<scenario>.json``: the responses a vendor returns for a
scenario, served in order. Each has a ``status``, optional ``headers``, and a
``body`` or the ``events`` of a stream; with ``disconnect``, the connection
drops after the events. Tests are skipped for vendors without that scenario.

The clients never sleep before a retry: `sleeps` records how long they would.
"""

import asyncio
import json
from collections.abc import AsyncIterator, Callable, Iterator, Mapping
from pathlib import Path
from typing import Any

import httpx2
import pytest

from uni_llm_client import AsyncClient, Client, Message, StreamEvent
from uni_llm_client import _async_client as async_client_module
from uni_llm_client import _client as client_module
from uni_llm_client._registry import PROVIDERS

MODELS = [
    "anthropic/claude-opus-5",
    "anthropic/claude-haiku-4-5",
    "openrouter/anthropic/claude-haiku-4.5",
]

FIXTURES = Path(__file__).parent / "fixtures"
TEST_API_KEYS = {name: "test-key" for name in PROVIDERS}

Ask = Callable[..., str]
Stream = Callable[..., list[StreamEvent]]


class EventStream(httpx2.SyncByteStream, httpx2.AsyncByteStream):
    """The body of a streamed response; with `disconnect`, the connection drops
    after it, as a network fault would."""

    def __init__(self, events: list[dict[str, Any]], disconnect: bool) -> None:
        frames = []
        for event in events:
            data = event["data"]
            text = data if isinstance(data, str) else json.dumps(data)
            name = f"event: {event['event']}\n" if "event" in event else ""
            frames.append(f"{name}data: {text}\n\n")
        self._content = "".join(frames).encode()
        self._disconnect = disconnect

    def __iter__(self) -> Iterator[bytes]:
        yield self._content
        self._end()

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield self._content
        self._end()

    def _end(self) -> None:
        if self._disconnect:
            raise httpx2.RemoteProtocolError(
                "peer closed connection without sending complete message body"
            )


class FakeVendor:
    """Serves recorded vendor responses and records the requests it receives."""

    def __init__(self) -> None:
        self.requests: list[httpx2.Request] = []
        self.responses: list[httpx2.Response] = []
        self._served: list[dict[str, Any] | httpx2.TransportError] = []

    def serve(self, model: str, *scenarios: str) -> None:
        """Serve each scenario's responses, after those not served yet."""
        provider = model.partition("/")[0]
        for scenario in scenarios:
            path = FIXTURES / provider / f"{scenario}.json"
            if not path.exists():
                pytest.skip(f"{provider} has no {scenario!r} scenario")
            self._served.extend(json.loads(path.read_text()))

    def fail(self, error: httpx2.TransportError) -> None:
        """Answer the next request with `error` instead of a response."""
        self._served.append(error)

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        recorded = self._served.pop(0)
        if isinstance(recorded, httpx2.TransportError):
            raise recorded
        headers = recorded.get("headers", {})
        if "events" in recorded:
            response = httpx2.Response(
                recorded["status"],
                headers={"content-type": "text/event-stream", **headers},
                stream=EventStream(
                    recorded["events"], recorded.get("disconnect", False)
                ),
            )
        else:
            response = httpx2.Response(
                recorded["status"], headers=headers, json=recorded["body"]
            )
        self.responses.append(response)
        return response


@pytest.fixture(autouse=True)
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """The seconds the clients waited before each retry, recorded, not slept."""
    delays: list[float] = []

    def sleep(seconds: float) -> None:
        delays.append(seconds)

    async def async_sleep(seconds: float) -> None:
        delays.append(seconds)

    monkeypatch.setattr(client_module, "_sleep", sleep)
    monkeypatch.setattr(async_client_module, "_sleep", async_sleep)
    return delays


@pytest.fixture(params=MODELS)
def model(request: pytest.FixtureRequest) -> str:
    return str(request.param)


@pytest.fixture
def vendor() -> FakeVendor:
    return FakeVendor()


def client_options(
    api_keys: Mapping[str, str] | None, max_retries: int | None
) -> dict[str, Any]:
    """The client's arguments; without `max_retries`, the client's default."""
    options: dict[str, Any] = {"api_keys": api_keys}
    if max_retries is not None:
        options["max_retries"] = max_retries
    return options


@pytest.fixture(params=["sync", "async"])
def ask(request: pytest.FixtureRequest, vendor: FakeVendor) -> Ask:
    """Call `ask` on a `Client` or `AsyncClient` wired to the fake vendor.

    `api_keys` and `max_retries` go to the client, other keywords to `ask`.
    """
    transport = httpx2.MockTransport(vendor.handle)

    def ask_sync(
        model: str,
        question: str,
        api_keys: Mapping[str, str] | None = TEST_API_KEYS,
        max_retries: int | None = None,
        **kwargs: Any,
    ) -> str:
        http_client = httpx2.Client(transport=transport)
        options = client_options(api_keys, max_retries)
        with Client(http_client=http_client, **options) as client:
            return client.ask(model, question, **kwargs)

    def ask_async(
        model: str,
        question: str,
        api_keys: Mapping[str, str] | None = TEST_API_KEYS,
        max_retries: int | None = None,
        **kwargs: Any,
    ) -> str:
        async def run() -> str:
            http_client = httpx2.AsyncClient(transport=transport)
            options = client_options(api_keys, max_retries)
            async with AsyncClient(http_client=http_client, **options) as client:
                return await client.ask(model, question, **kwargs)

        return asyncio.run(run())

    return ask_sync if request.param == "sync" else ask_async


@pytest.fixture(params=["sync", "async"])
def stream(request: pytest.FixtureRequest, vendor: FakeVendor) -> Stream:
    """Stream the answer to a question from a `Client` or `AsyncClient` wired to
    the fake vendor, and return the events.

    Each event is appended to `received` as it arrives, so that a test sees the
    events before an error. Other arguments are those of the `ask` fixture.
    """
    transport = httpx2.MockTransport(vendor.handle)

    def stream_sync(
        model: str,
        question: str,
        received: list[StreamEvent] | None = None,
        api_keys: Mapping[str, str] | None = TEST_API_KEYS,
        max_retries: int | None = None,
        **kwargs: Any,
    ) -> list[StreamEvent]:
        events = [] if received is None else received
        http_client = httpx2.Client(transport=transport)
        options = client_options(api_keys, max_retries)
        with Client(http_client=http_client, **options) as client:
            for event in client.stream(model, [Message.user(question)], **kwargs):
                events.append(event)
        return events

    def stream_async(
        model: str,
        question: str,
        received: list[StreamEvent] | None = None,
        api_keys: Mapping[str, str] | None = TEST_API_KEYS,
        max_retries: int | None = None,
        **kwargs: Any,
    ) -> list[StreamEvent]:
        events = [] if received is None else received

        async def run() -> None:
            http_client = httpx2.AsyncClient(transport=transport)
            options = client_options(api_keys, max_retries)
            async with AsyncClient(http_client=http_client, **options) as client:
                messages = [Message.user(question)]
                async for event in client.stream(model, messages, **kwargs):
                    events.append(event)

        asyncio.run(run())
        return events

    return stream_sync if request.param == "sync" else stream_async
