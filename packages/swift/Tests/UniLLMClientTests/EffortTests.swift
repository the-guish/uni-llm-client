// Copyright 2026 The uni-llm-client Authors
// SPDX-License-Identifier: Apache-2.0

import Testing

@testable import UniLLMClient

@Test(arguments: [
    (Effort.high, Set([Effort.low, .medium]), Effort.medium),
    (Effort.max, Set([Effort.low, .high]), Effort.high),
])
func resolvesToTheNearestSupportedLevelRoundingUp(requested: Effort, supported: Set<Effort>, expected: Effort) {
    #expect(resolveEffort(requested, supported: supported) == expected)
}
