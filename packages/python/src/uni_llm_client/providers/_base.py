# Copyright 2026 The uni-llm-client Authors
# SPDX-License-Identifier: Apache-2.0

"""The interface every vendor implements."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass
from typing import ClassVar

import httpx2

from uni_llm_client._effort import Effort
from uni_llm_client._errors import (
    AuthenticationError,
    ProviderError,
    RateLimitError,
    UniLLMError,
)
from uni_llm_client._events import StreamEvent
from uni_llm_client._messages import Message, Response, Tool
from uni_llm_client._sse import ServerSentEvent

# Upper bound on requests per question, including follow-ups for paused turns.
# The retries of a failed request do not count (RETRY-8).
MAX_REQUESTS = 6


@dataclass(frozen=True, slots=True)
class ModelCall:
    """One call to a model, validated and vendor-neutral."""

    model: str
    messages: tuple[Message, ...]
    system: str | None = None
    tools: tuple[Tool, ...] = ()
    web_search: bool = False
    effort: Effort | None = None
    max_tokens: int | None = None


class StreamParser(ABC):
    """Turns one vendor's server-sent events into the stream's events.

    Fed each event in order, it yields the events it completes; `finish` is called
    once the response ends and returns the whole message.
    """

    @abstractmethod
    def feed(self, event: ServerSentEvent) -> Iterator[StreamEvent]:
        """The events `event` completes. Raises the library error a vendor's error
        event maps to."""

    @abstractmethod
    def finish(self) -> Response:
        """The complete response, or `IncompleteResponseError` if the stream ended
        before the message was complete."""


class Provider(ABC):
    """Translates the generic API into one vendor's HTTP API.

    Implementations do no I/O: `Client` and `AsyncClient` send the requests
    they build and read the responses, so a single implementation serves both.
    """

    name: ClassVar[str]
    """Model id prefix, e.g. ``"anthropic"`` in ``"anthropic/claude-opus-5"``."""

    api_key_env: ClassVar[str]
    """Environment variable read when no API key is passed to the client."""

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    @abstractmethod
    def build_request(self, call: ModelCall, *, stream: bool = False) -> httpx2.Request:
        """Build the request for `call`; with `stream`, one the vendor answers with
        server-sent events."""

    @abstractmethod
    def parse_response(self, response: httpx2.Response) -> Response:
        """The response to a request built without `stream`, read in full. Raises
        the library error a vendor error maps to."""

    @abstractmethod
    def error_of(self, response: httpx2.Response) -> UniLLMError:
        """The error for a response with an error status, read in full."""

    @abstractmethod
    def stream_parser(self) -> StreamParser:
        """A parser for the events of one streamed response."""


def http_error(status_code: int, message: str) -> ProviderError:
    """Map an HTTP error status to the matching library error."""
    if status_code in (401, 403):
        return AuthenticationError(message, status_code=status_code)
    if status_code == 429:
        return RateLimitError(message, status_code=status_code)
    return ProviderError(message, status_code=status_code)
