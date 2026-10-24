# Copyright 2026 The uni-llm-client Authors
# SPDX-License-Identifier: Apache-2.0

"""Errors raised by uni-llm-client."""


class UniLLMError(Exception):
    """Base class for all errors raised by this library."""


class ProviderError(UniLLMError):
    """The provider rejected the request or failed to handle it."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class AuthenticationError(ProviderError):
    """The API key is missing, invalid, or not allowed to make the request."""


class RateLimitError(ProviderError):
    """The provider is rate limiting requests."""


class IncompleteResponseError(UniLLMError):
    """The provider stopped before producing a complete answer (EFFORT-6)."""


class RefusalError(UniLLMError):
    """The model declined to answer."""


class TransportError(UniLLMError):
    """The vendor could not be reached, or the connection failed or timed out.

    The HTTP library's exception is the ``__cause__`` (RETRY-5).
    """
