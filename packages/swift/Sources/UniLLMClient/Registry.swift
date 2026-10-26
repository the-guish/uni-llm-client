// Copyright 2026 The uni-llm-client Authors
// SPDX-License-Identifier: Apache-2.0

// Resolves `provider/model` ids to providers, shared by both clients.

import Foundation

/// Checks the providers once, and validates a call before anything is sent.
struct ProviderRegistry: Sendable {
    /// The implementation of each kind.
    static let kinds: [String: any Provider.Type] = [
        AnthropicProvider.kind: AnthropicProvider.self,
        OpenRouterProvider.kind: OpenRouterProvider.self,
    ]

    private let configs: [String: ProviderConfig]
    /// The built-ins that were not replaced: only they read their environment
    /// variable.
    private let readsEnvironment: Set<String>
    private let environment: [String: String]

    init(
        apiKeys: [String: String],
        providers: [String: ProviderConfig],
        environment: [String: String] = ProcessInfo.processInfo.environment
    ) throws {
        for name in providers.keys.sorted() {
            let config = providers[name]!
            if name.isEmpty || name.contains("/") {
                throw UniLLMError(.invalidArgument, "Invalid provider name '\(name)'")
            }
            if Self.kinds[config.kind] == nil {
                let available = Self.kinds.keys.sorted().joined(separator: ", ")
                throw UniLLMError(
                    .invalidArgument,
                    "Unknown kind '\(config.kind)' for provider '\(name)'; available: \(available)"
                )
            }
        }
        var configs = Dictionary(uniqueKeysWithValues: Self.kinds.keys.map { ($0, ProviderConfig(kind: $0)) })
        configs.merge(providers) { _, configured in configured }
        for name in apiKeys.keys.sorted() {
            if providers[name] != nil {
                throw UniLLMError(
                    .invalidArgument,
                    "Provider '\(name)' is in both apiKeys and providers; set its apiKey in its ProviderConfig"
                )
            }
            if configs[name] == nil {
                let available = configs.keys.sorted().joined(separator: ", ")
                throw UniLLMError(.invalidArgument, "Unknown provider '\(name)'; available: \(available)")
            }
            configs[name]!.apiKey = apiKeys[name]
        }
        self.configs = configs
        readsEnvironment = Set(Self.kinds.keys).subtracting(providers.keys)
        self.environment = environment
    }

    /// Validate the arguments and resolve the provider without sending anything.
    func prepare(
        model modelID: String,
        messages: [Message],
        system: String? = nil,
        tools: [Tool] = [],
        webSearch: Bool = false,
        effort: Effort? = nil,
        maxTokens: Int? = nil,
        timeout: TimeInterval? = nil
    ) throws -> (any Provider, ModelCall) {
        if messages.isEmpty {
            throw UniLLMError(.invalidArgument, "Expected at least one message")
        }
        if let maxTokens, maxTokens < 1 {
            throw UniLLMError(.invalidArgument, "Expected a positive max_tokens, got \(maxTokens)")
        }
        if let timeout, !(timeout > 0 && timeout.isFinite) {
            throw UniLLMError(.invalidArgument, "Expected a positive timeout, got \(timeout)")
        }
        let parts = modelID.split(separator: "/", maxSplits: 1, omittingEmptySubsequences: false)
        guard parts.count == 2, !parts[1].isEmpty else {
            throw UniLLMError(
                .invalidArgument, "Expected a model id like 'anthropic/claude-opus-5', got '\(modelID)'"
            )
        }
        let call = ModelCall(
            model: String(parts[1]),
            messages: messages,
            system: system,
            tools: tools,
            webSearch: webSearch,
            effort: effort,
            maxTokens: maxTokens,
            timeout: timeout
        )
        return (try provider(String(parts[0])), call)
    }

    private func provider(_ name: String) throws -> any Provider {
        guard let config = configs[name] else {
            let available = configs.keys.sorted().joined(separator: ", ")
            throw UniLLMError(.invalidArgument, "Unknown provider '\(name)'; available: \(available)")
        }
        let kind = Self.kinds[config.kind]!
        var apiKey = config.apiKey ?? ""
        if apiKey.isEmpty, readsEnvironment.contains(name) {
            apiKey = environment[kind.apiKeyEnv] ?? ""
        }
        if apiKey.isEmpty {
            let how =
                readsEnvironment.contains(name)
                ? "pass apiKeys: [\"\(name)\": ...] or set \(kind.apiKeyEnv)"
                : "set apiKey in its ProviderConfig"
            throw UniLLMError(.authentication, "No API key for '\(name)': \(how)")
        }
        return kind.init(apiKey: apiKey, baseURL: config.baseURL)
    }
}
