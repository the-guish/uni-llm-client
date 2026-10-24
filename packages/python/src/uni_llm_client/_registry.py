# Copyright 2026 The uni-llm-client Authors
# SPDX-License-Identifier: Apache-2.0

"""Resolves ``provider/model`` ids to providers, shared by both clients.

The rules are in docs/specs/providers.md.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import replace

from uni_llm_client._config import ProviderConfig
from uni_llm_client._effort import Effort, parse_effort
from uni_llm_client._errors import AuthenticationError
from uni_llm_client._messages import Message, Tool
from uni_llm_client.providers._base import ModelCall, Provider
from uni_llm_client.providers.anthropic import AnthropicProvider
from uni_llm_client.providers.openrouter import OpenRouterProvider

PROVIDERS: dict[str, type[Provider]] = {
    provider.kind: provider for provider in (AnthropicProvider, OpenRouterProvider)
}
"""The implementation of each kind."""

BUILT_IN: dict[str, ProviderConfig] = {kind: ProviderConfig(kind) for kind in PROVIDERS}
"""The providers every client has: one per kind, under the kind's name (PROV-2)."""


class ProviderRegistry:
    """Checks the providers once, creates each when first used, and validates a
    call before anything is sent."""

    def __init__(
        self,
        api_keys: Mapping[str, str] | None,
        providers: Mapping[str, ProviderConfig] | None = None,
    ) -> None:
        configured = dict(providers or {})
        for name, config in configured.items():
            if not name or "/" in name:
                raise ValueError(f"Invalid provider name {name!r}")
            if config.kind not in PROVIDERS:
                available = ", ".join(sorted(PROVIDERS))
                raise ValueError(
                    f"Unknown kind {config.kind!r} for provider {name!r}; "
                    f"available: {available}"
                )
        configs = {**BUILT_IN, **configured}
        for name, api_key in (api_keys or {}).items():
            if name in configured:
                raise ValueError(
                    f"Provider {name!r} is in both api_keys and providers; "
                    "set its api_key in its ProviderConfig"
                )
            if name not in configs:
                available = ", ".join(sorted(configs))
                raise ValueError(f"Unknown provider {name!r}; available: {available}")
            configs[name] = replace(configs[name], api_key=api_key)
        self._configs = configs
        # Only a built-in that was not replaced reads its environment variable.
        self._reads_env = set(BUILT_IN) - set(configured)
        self._providers: dict[str, Provider] = {}

    def prepare(
        self,
        model_id: str,
        messages: Sequence[Message],
        *,
        system: str | None = None,
        tools: Sequence[Tool] = (),
        web_search: bool = False,
        effort: Effort | str | None = None,
        max_tokens: int | None = None,
        timeout: float | None = None,
    ) -> tuple[Provider, ModelCall]:
        """Validate the arguments and resolve the provider without sending anything."""
        parsed_effort = parse_effort(effort)
        if not messages:
            raise ValueError("Expected at least one message")
        if max_tokens is not None and max_tokens < 1:
            raise ValueError(f"Expected a positive max_tokens, got {max_tokens!r}")
        if timeout is not None and not timeout > 0:
            raise ValueError(f"Expected a positive timeout, got {timeout!r}")
        name, _, model = model_id.partition("/")
        if not model:
            raise ValueError(
                f"Expected a model id like 'anthropic/claude-opus-5', got {model_id!r}"
            )
        provider = self._provider(name)
        call = ModelCall(
            model=model,
            messages=tuple(messages),
            system=system,
            tools=tuple(tools),
            web_search=web_search,
            effort=parsed_effort,
            max_tokens=max_tokens,
            timeout=timeout,
        )
        return provider, call

    def _provider(self, name: str) -> Provider:
        if name not in self._providers:
            config = self._configs.get(name)
            if config is None:
                available = ", ".join(sorted(self._configs))
                raise ValueError(f"Unknown provider {name!r}; available: {available}")
            provider_cls = PROVIDERS[config.kind]
            api_key = config.api_key
            if not api_key and name in self._reads_env:
                api_key = os.environ.get(provider_cls.api_key_env)
            if not api_key:
                how = (
                    f"pass api_keys={{{name!r}: ...}} or set {provider_cls.api_key_env}"
                    if name in self._reads_env
                    else "set api_key in its ProviderConfig"
                )
                raise AuthenticationError(f"No API key for {name!r}: {how}")
            self._providers[name] = provider_cls(api_key, config.base_url)
        return self._providers[name]
