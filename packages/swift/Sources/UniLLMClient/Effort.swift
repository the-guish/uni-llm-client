// Copyright 2026 The uni-llm-client Authors
// SPDX-License-Identifier: Apache-2.0

/// How much the model should reason before answering, as specified in
/// docs/specs/effort.md. The type admits only valid levels (EFFORT-1);
/// `Effort(rawValue:)` reads one from its string.
public enum Effort: String, Sendable, CaseIterable, Comparable {
    case low
    case medium
    case high
    case max

    public static func < (lhs: Effort, rhs: Effort) -> Bool {
        allCases.firstIndex(of: lhs)! < allCases.firstIndex(of: rhs)!
    }
}

/// The nearest supported level, rounding up (EFFORT-4).
func resolveEffort(_ requested: Effort, supported: Set<Effort>) -> Effort {
    let ranked = supported.sorted()
    return ranked.first { $0 >= requested } ?? ranked.last ?? requested
}
