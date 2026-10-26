// Copyright 2026 The uni-llm-client Authors
// SPDX-License-Identifier: Apache-2.0

// The interface every vendor implements.

import Foundation

/// Upper bound on requests per question, including follow-ups for paused
/// turns. The retries of a failed request do not count (RETRY-8).
let maxRequests = 6

/// One call to a model, validated and vendor-neutral.
struct ModelCall: Sendable {
    var model: String
    var messages: [Message]
    var system: String?
    var tools: [Tool] = []
    var webSearch = false
    var effort: Effort?
    var maxTokens: Int?
    /// Seconds to wait for the connection and for each read; the transport's
    /// when `nil`.
    var timeout: TimeInterval?
}

/// Turns one vendor's server-sent events into the stream's events.
///
/// Fed each event in order, it returns the events it completes; `finish` is
/// called once the response ends and returns the whole response.
protocol StreamParser {
    /// The events `event` completes. Throws the error a vendor's error event
    /// maps to.
    mutating func feed(_ event: ServerSentEvent) throws -> [StreamEvent]

    /// The complete response, or `incompleteResponse` if the stream ended
    /// before the message was complete.
    func finish() throws -> Response
}

/// Translates the generic API into one kind of vendor HTTP API.
///
/// An instance is one provider: the kind's API at one base URL, with one key.
/// Implementations do no I/O: the clients send the requests they build and read
/// the responses.
protocol Provider: Sendable {
    /// The API this type speaks, and the name of its built-in provider.
    static var kind: String { get }

    /// Environment variable the built-in provider reads when given no key.
    static var apiKeyEnv: String { get }

    /// Where the vendor serves the API, without `path`.
    static var defaultBaseURL: String { get }

    /// The path of the endpoint under the base URL.
    static var path: String { get }

    var apiKey: String { get }
    var baseURL: String { get }

    init(apiKey: String, baseURL: String)

    /// The request for `call`; with `stream`, one the vendor answers with
    /// server-sent events.
    func buildRequest(_ call: ModelCall, stream: Bool) throws -> HTTPRequest

    /// The response to a request built without `stream`, its body read in full.
    /// Throws the error a vendor error maps to.
    func parseResponse(status: Int, body: Data) throws -> Response

    /// The error for a response with an error status, its body read in full.
    func error(status: Int, body: Data) -> UniLLMError

    /// A parser for the events of one streamed response.
    func streamParser() -> any StreamParser
}

extension Provider {
    init(apiKey: String, baseURL: String?) {
        var base = baseURL ?? Self.defaultBaseURL
        // One trailing slash or none, both common in configuration.
        while base.hasSuffix("/") {
            base.removeLast()
        }
        self.init(apiKey: apiKey, baseURL: base)
    }

    /// The request to the endpoint; `timeout` bounds each of its operations.
    func post(headers: [String: String], body: JSONValue, timeout: TimeInterval?) throws -> HTTPRequest {
        guard let url = URL(string: baseURL + Self.path), url.host != nil else {
            throw UniLLMError(.invalidArgument, "Invalid base URL '\(baseURL)'")
        }
        var headers = headers
        headers["content-type"] = "application/json"
        return HTTPRequest(method: "POST", url: url, headers: headers, body: try body.encoded(), timeout: timeout)
    }
}

/// The JSON of a vendor's body, or a provider error naming what it is not.
func vendorJSON(_ data: Data) throws -> JSONValue {
    do {
        return try JSONValue.decode(data)
    } catch {
        throw UniLLMError(.provider, "The vendor sent a body that is not JSON")
    }
}

/// The JSON of a server-sent event's data.
func vendorJSON(_ text: String) throws -> JSONValue {
    try vendorJSON(Data(text.utf8))
}

/// A provider error for a vendor answer that lacks what it must hold.
func malformed(_ what: String) -> UniLLMError {
    UniLLMError(.provider, "The vendor's answer has no valid \(what)")
}

/// The JSON object of a tool call's input, from its JSON text.
func parseToolInput(_ text: String) throws -> [String: JSONValue] {
    if text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
        return [:]
    }
    guard let parsed = try? JSONValue.decode(Data(text.utf8)) else {
        throw UniLLMError(.provider, "The tool call's input is not valid JSON")
    }
    guard let object = parsed.objectValue else {
        throw UniLLMError(.provider, "The tool call's input is not a JSON object")
    }
    return object
}
