# Copyright 2026 The uni-llm-client Authors
# SPDX-License-Identifier: Apache-2.0

"""What each Claude model supports. Update when Anthropic ships or retires models."""

import re
from dataclasses import dataclass
from typing import Literal

from uni_llm_client._effort import Effort


@dataclass(frozen=True)
class ModelInfo:
    thinking: Literal["adaptive", "budget"]
    """``adaptive`` uses ``output_config.effort`` and asks for the reasoning's summary
    (``display``); ``budget`` uses ``budget_tokens`` and shows its reasoning anyway."""

    max_output_tokens: int
    web_search_tool: str
    efforts: frozenset[Effort] = frozenset(Effort)


# Web search tool for models missing from the table.
DEFAULT_WEB_SEARCH_TOOL = "web_search_20250305"

_ADAPTIVE = ModelInfo("adaptive", 128_000, "web_search_20260209")

MODELS: dict[str, ModelInfo] = {
    "claude-fable-5-1": _ADAPTIVE,
    "claude-fable-5": _ADAPTIVE,
    "claude-opus-5": _ADAPTIVE,
    "claude-opus-4-8": _ADAPTIVE,
    "claude-opus-4-7": _ADAPTIVE,
    "claude-opus-4-6": _ADAPTIVE,
    "claude-sonnet-5": _ADAPTIVE,
    "claude-sonnet-4-6": _ADAPTIVE,
    "claude-haiku-4-5": ModelInfo("budget", 64_000, DEFAULT_WEB_SEARCH_TOOL),
}

_SNAPSHOT_SUFFIX = re.compile(r"-\d{8}$")


def lookup_model(model: str) -> ModelInfo | None:
    """Return what `model` supports, or `None` if it is not in the table."""
    return MODELS.get(_SNAPSHOT_SUFFIX.sub("", model))
