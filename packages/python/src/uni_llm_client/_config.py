# Copyright 2026 The uni-llm-client Authors
# SPDX-License-Identifier: Apache-2.0

"""How a client reaches a vendor, as specified in docs/specs/providers.md."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProviderConfig:
    """A provider: an endpoint speaking one kind of API, reached with one key.

    The built-in providers, ``anthropic`` and ``openrouter``, need no
    configuration. Configure one to reach a gateway or another deployment of the
    same API under a name of your own, or to replace a built-in.
    """

    kind: str
    """The API the endpoint speaks: ``"anthropic"`` or ``"openrouter"``."""

    api_key: str | None = None
    """The key sent to this endpoint. A configured provider never reads an
    environment variable, so a key never reaches a host it was not given for."""

    base_url: str | None = None
    """Where the API is served, without the kind's path; the kind's public URL
    when ``None``."""
