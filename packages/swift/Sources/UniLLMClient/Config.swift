// Copyright 2026 The uni-llm-client Authors
// SPDX-License-Identifier: Apache-2.0

/// A provider: an endpoint speaking one kind of API, reached with one key.
///
/// The built-in providers, `anthropic` and `openrouter`, need no
/// configuration. Configure one to reach a gateway or another deployment of
/// the same API under a name of your own, or to replace a built-in.
public struct ProviderConfig: Sendable, Hashable {
    /// The API the endpoint speaks: `"anthropic"` or `"openrouter"`.
    public var kind: String

    /// The key sent to this endpoint. A configured provider never reads an
    /// environment variable, so a key never reaches a host it was not given
    /// for.
    public var apiKey: String?

    /// Where the API is served, without the kind's path; the kind's public URL
    /// when `nil`.
    public var baseURL: String?

    public init(kind: String, apiKey: String? = nil, baseURL: String? = nil) {
        self.kind = kind
        self.apiKey = apiKey
        self.baseURL = baseURL
    }
}
