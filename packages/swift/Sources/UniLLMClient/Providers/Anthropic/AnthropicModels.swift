// Copyright 2026 The uni-llm-client Authors
// SPDX-License-Identifier: Apache-2.0

// What each Claude model supports. Update when Anthropic ships or retires models.

struct AnthropicModelInfo: Sendable {
    enum Thinking: Sendable {
        /// Uses `output_config.effort` and asks for the reasoning's summary
        /// (`display`).
        case adaptive
        /// Uses `budget_tokens` and shows its reasoning anyway.
        case budget
    }

    var thinking: Thinking
    var maxOutputTokens: Int
    var webSearchTool: String
    var efforts: Set<Effort> = Set(Effort.allCases)
}

enum AnthropicModels {
    /// Web search tool for models missing from the table.
    static let defaultWebSearchTool = "web_search_20250305"

    private static let adaptive = AnthropicModelInfo(
        thinking: .adaptive, maxOutputTokens: 128_000, webSearchTool: "web_search_20260209"
    )

    static let models: [String: AnthropicModelInfo] = [
        "claude-fable-5-1": adaptive,
        "claude-fable-5": adaptive,
        "claude-opus-5": adaptive,
        "claude-opus-4-8": adaptive,
        "claude-opus-4-7": adaptive,
        "claude-opus-4-6": adaptive,
        "claude-sonnet-5": adaptive,
        "claude-sonnet-4-6": adaptive,
        "claude-haiku-4-5": AnthropicModelInfo(
            thinking: .budget, maxOutputTokens: 64_000, webSearchTool: defaultWebSearchTool
        ),
    ]

    /// What `model` supports, or `nil` if it is not in the table.
    static func lookup(_ model: String) -> AnthropicModelInfo? {
        models[withoutSnapshot(model)]
    }

    /// `model` without a snapshot date such as `-20251001`.
    private static func withoutSnapshot(_ model: String) -> String {
        let suffix = model.suffix(9)
        guard suffix.count == 9, suffix.first == "-", suffix.dropFirst().allSatisfy(\.isASCIIDigit) else {
            return model
        }
        return String(model.dropLast(9))
    }
}

extension Character {
    fileprivate var isASCIIDigit: Bool { isASCII && isNumber }
}
