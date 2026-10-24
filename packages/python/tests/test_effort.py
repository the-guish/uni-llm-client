# Copyright 2026 The uni-llm-client Authors
# SPDX-License-Identifier: Apache-2.0

import json

import pytest
from conftest import Ask, FakeVendor

from uni_llm_client import Effort
from uni_llm_client._effort import resolve_effort
from uni_llm_client.providers.anthropic._models import MODELS

LOW, MEDIUM, HIGH, MAX = Effort

BUDGET_MODELS = sorted(
    name for name, info in MODELS.items() if info.thinking == "budget"
)


@pytest.mark.parametrize(
    ("requested", "supported", "expected"),
    [
        (HIGH, {LOW, MEDIUM, HIGH, MAX}, HIGH),
        (LOW, {MEDIUM, HIGH}, MEDIUM),
        (MEDIUM, {LOW, HIGH}, HIGH),
        (HIGH, {LOW, MEDIUM}, MEDIUM),
        (MAX, {LOW, HIGH}, HIGH),
    ],
)
def test_resolves_to_the_nearest_supported_level_rounding_up(
    requested: Effort, supported: set[Effort], expected: Effort
) -> None:
    assert resolve_effort(requested, supported) == expected


@pytest.mark.parametrize("name", BUDGET_MODELS)
def test_a_token_budget_is_sent_without_display(
    ask: Ask, vendor: FakeVendor, name: str
) -> None:
    """A token-budget model shows its reasoning unasked (EFFORT-7)."""
    model = f"anthropic/{name}"
    vendor.serve(model, "answer")

    ask(model, "What is the capital of France?", effort=LOW)

    (request,) = vendor.requests
    thinking = json.loads(request.content)["thinking"]
    assert thinking == {"type": "enabled", "budget_tokens": 1_024}
