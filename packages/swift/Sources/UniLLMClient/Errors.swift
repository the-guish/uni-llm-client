// Copyright 2026 The uni-llm-client Authors
// SPDX-License-Identifier: Apache-2.0

/// The error every call of this library throws, besides `CancellationError`.
public struct UniLLMError: Error, Sendable, Equatable, CustomStringConvertible {
    /// What went wrong. The raw values are the error types the ports share.
    public enum Kind: String, Sendable, CaseIterable {
        /// An argument is invalid, such as the model id or the effort.
        case invalidArgument = "invalid_argument"
        /// The API key is missing, invalid, or not allowed to make the request.
        case authentication
        /// The provider is rate limiting requests.
        case rateLimit = "rate_limit"
        /// The model declined to answer.
        case refusal
        /// The provider stopped before producing a complete answer (EFFORT-6).
        case incompleteResponse = "incomplete_response"
        /// The vendor could not be reached, or the connection failed or timed
        /// out (RETRY-5).
        case transport
        /// The provider rejected the request or failed to handle it.
        case provider
    }

    public let kind: Kind
    public let message: String

    /// The HTTP status of the vendor's answer, or the code of an error in the
    /// body of a successful one, for the errors a vendor sends.
    public let statusCode: Int?

    public init(_ kind: Kind, _ message: String, statusCode: Int? = nil) {
        self.kind = kind
        self.message = message
        self.statusCode = statusCode
    }

    public var description: String { message }
}

/// The error for an HTTP error status.
func httpError(_ statusCode: Int, _ message: String) -> UniLLMError {
    switch statusCode {
    case 401, 403: UniLLMError(.authentication, message, statusCode: statusCode)
    case 429: UniLLMError(.rateLimit, message, statusCode: statusCode)
    default: UniLLMError(.provider, message, statusCode: statusCode)
    }
}
