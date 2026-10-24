# Copyright 2026 The uni-llm-client Authors
# SPDX-License-Identifier: Apache-2.0

"""Asynchronous client."""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from dataclasses import replace
from functools import partial
from types import TracebackType
from typing import Self, TypeVar

import httpx2

from uni_llm_client._client import DEFAULT_TIMEOUT, accepted, answer_of
from uni_llm_client._config import ProviderConfig
from uni_llm_client._effort import Effort
from uni_llm_client._errors import IncompleteResponseError, UniLLMError
from uni_llm_client._events import Completed, StreamEvent
from uni_llm_client._messages import Message, Response, StopReason, Tool
from uni_llm_client._registry import ProviderRegistry
from uni_llm_client._retry import (
    DEFAULT_MAX_RETRIES,
    RetryPolicy,
    wrap_transport_errors,
)
from uni_llm_client._sse import SSEDecoder
from uni_llm_client.providers._base import MAX_REQUESTS

T = TypeVar("T")

# How the client waits before a retry; the unit tests replace it.
_sleep = asyncio.sleep


class AsyncClient:
    """Asynchronous client with the same API as `Client`.

    Args:
        api_keys: API key per built-in provider, e.g. ``{"anthropic": "..."}``.
            A built-in without one reads its environment variable, e.g.
            ``ANTHROPIC_API_KEY``.
        providers: Providers of your own by name, e.g. a gateway speaking the
            Anthropic API: ``{"gateway": ProviderConfig("anthropic", key, url)}``.
            One named like a built-in replaces it. See docs/specs/providers.md.
        http_client: HTTP client used to send requests; closed with this client.
        max_retries: Retries of a request that failed in a way that may pass, such
            as a rate limit, a vendor error or a lost connection; 0 disables them.
    """

    def __init__(
        self,
        *,
        api_keys: Mapping[str, str] | None = None,
        providers: Mapping[str, ProviderConfig] | None = None,
        http_client: httpx2.AsyncClient | None = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> None:
        self._retry = RetryPolicy(max_retries)
        self._registry = ProviderRegistry(api_keys, providers)
        self._http = http_client or httpx2.AsyncClient(timeout=DEFAULT_TIMEOUT)

    async def ask(
        self,
        model: str,
        question: str,
        *,
        web_search: bool = False,
        effort: Effort | str | None = None,
        timeout: float | None = None,
    ) -> str:
        """Ask `model`, a ``"provider/model"`` id, a question and return the answer."""
        provider, call = self._registry.prepare(
            model,
            [Message.user(question)],
            web_search=web_search,
            effort=effort,
            timeout=timeout,
        )
        for _ in range(MAX_REQUESTS):
            # Each request retries on its own (RETRY-8).
            request = provider.build_request(call)
            response = await self._send(request, provider.parse_response)
            if response.stop_reason is not StopReason.PAUSE_TURN:
                return answer_of(response)
            # A server tool loop hit its iteration limit: re-send the turn so far
            # and the server resumes where it left off.
            call = replace(call, messages=(*call.messages, response.message))
        raise IncompleteResponseError(f"No answer after {MAX_REQUESTS} requests")

    async def complete(
        self,
        model: str,
        messages: Sequence[Message],
        *,
        system: str | None = None,
        tools: Sequence[Tool] = (),
        web_search: bool = False,
        effort: Effort | str | None = None,
        max_tokens: int | None = None,
        timeout: float | None = None,
    ) -> Response:
        """Send `messages` to `model` and return its whole response."""
        provider, call = self._registry.prepare(
            model,
            messages,
            system=system,
            tools=tools,
            web_search=web_search,
            effort=effort,
            max_tokens=max_tokens,
            timeout=timeout,
        )
        return await self._send(provider.build_request(call), provider.parse_response)

    async def stream(
        self,
        model: str,
        messages: Sequence[Message],
        *,
        system: str | None = None,
        tools: Sequence[Tool] = (),
        web_search: bool = False,
        effort: Effort | str | None = None,
        max_tokens: int | None = None,
        timeout: float | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Send `messages` to `model` and yield its response as it arrives.

        The request is sent on the first iteration. Closing the iterator early
        closes the vendor's response.
        """
        provider, call = self._registry.prepare(
            model,
            messages,
            system=system,
            tools=tools,
            web_search=web_search,
            effort=effort,
            max_tokens=max_tokens,
            timeout=timeout,
        )
        request = provider.build_request(call, stream=True)
        response = await self._send(request, partial(accepted, provider), stream=True)
        try:
            parser = provider.stream_parser()
            decoder = SSEDecoder()
            # The vendor accepted the stream: nothing is retried from here (RETRY-7).
            with wrap_transport_errors():
                async for line in response.aiter_lines():
                    event = decoder.feed(line)
                    if event is not None:
                        for item in parser.feed(event):
                            yield item
            yield Completed(parser.finish())
        finally:
            await response.aclose()

    async def close(self) -> None:
        await self._http.aclose()

    async def _send(
        self,
        request: httpx2.Request,
        accept: Callable[[httpx2.Response], T],
        *,
        stream: bool = False,
    ) -> T:
        """What `accept` makes of the response to `request`. While an attempt fails
        in a way that may pass, the same request is sent again (RETRY-2, RETRY-8);
        once the retries are spent, the last attempt's error is raised (RETRY-6)."""
        retries = 0
        while True:
            response: httpx2.Response | None = None
            try:
                with wrap_transport_errors():
                    response = await self._http.send(request, stream=stream)
                    if response.is_error:
                        # Its error is in the body; a streamed one is read here.
                        try:
                            await response.aread()
                        finally:
                            await response.aclose()
                return accept(response)
            except UniLLMError as error:
                delay = self._retry.delay(
                    error, retries, response, now=time.time(), jitter=random.random()
                )
                if delay is None:
                    raise
            await _sleep(delay)
            retries += 1

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()
