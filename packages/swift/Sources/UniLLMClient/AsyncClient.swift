// Copyright 2026 The uni-llm-client Authors
// SPDX-License-Identifier: Apache-2.0

import Foundation

/// Asynchronous client. Create it once and reuse it: its transport holds a
/// connection pool.
public final class AsyncClient: Sendable {
    private let registry: ProviderRegistry
    private let retry: RetryPolicy
    private let transport: any HTTPTransport

    /// - Parameters:
    ///   - apiKeys: API key per built-in provider, such as
    ///     `["anthropic": "..."]`. A built-in without one reads its environment
    ///     variable, such as `ANTHROPIC_API_KEY`.
    ///   - providers: Providers of your own by name, such as a gateway speaking
    ///     the Anthropic API:
    ///     `["gateway": ProviderConfig(kind: "anthropic", apiKey: key, baseURL: url)]`.
    ///     One named like a built-in replaces it.
    ///   - transport: Sends the requests; a `URLSessionTransport` when `nil`.
    ///   - maxRetries: Retries of a request that failed in a way that may pass,
    ///     such as a rate limit, a vendor error or a lost connection; 0
    ///     disables them.
    ///   - initialRetryDelay: Seconds before the first retry, doubled for each
    ///     one after, when the vendor asks for no wait.
    ///   - maxRetryDelay: The longest of those waits, in seconds.
    ///   - maxRetryAfter: The longest wait a vendor can ask for, in seconds. A
    ///     vendor asking for longer gets the doubling wait instead.
    /// - Throws: `UniLLMError` of kind `invalidArgument` for an invalid
    ///   provider or retry option.
    public init(
        apiKeys: [String: String] = [:],
        providers: [String: ProviderConfig] = [:],
        transport: (any HTTPTransport)? = nil,
        maxRetries: Int = 2,
        initialRetryDelay: TimeInterval = 0.5,
        maxRetryDelay: TimeInterval = 8,
        maxRetryAfter: TimeInterval = 60
    ) throws {
        retry = try RetryPolicy(
            maxRetries: maxRetries,
            initialRetryDelay: initialRetryDelay,
            maxRetryDelay: maxRetryDelay,
            maxRetryAfter: maxRetryAfter
        )
        registry = try ProviderRegistry(apiKeys: apiKeys, providers: providers)
        self.transport = transport ?? URLSessionTransport()
    }

    /// Ask `model`, a `"provider/model"` id, a question and return the answer.
    public func ask(
        model: String,
        question: String,
        webSearch: Bool = false,
        effort: Effort? = nil,
        timeout: TimeInterval? = nil
    ) async throws -> String {
        var (provider, call) = try registry.prepare(
            model: model, messages: [.user(question)], webSearch: webSearch, effort: effort, timeout: timeout
        )
        for _ in 0..<maxRequests {
            // Each request retries on its own (RETRY-8).
            let response = try await complete(provider, call)
            if response.stopReason != .pauseTurn {
                return try answer(of: response)
            }
            // A server tool loop hit its iteration limit: re-send the turn so
            // far and the server resumes where it left off.
            call.messages.append(response.message)
        }
        throw UniLLMError(.incompleteResponse, "No answer after \(maxRequests) requests")
    }

    /// Send `messages` to `model` and return its whole response.
    public func complete(
        model: String,
        messages: [Message],
        system: String? = nil,
        tools: [Tool] = [],
        webSearch: Bool = false,
        effort: Effort? = nil,
        maxTokens: Int? = nil,
        timeout: TimeInterval? = nil
    ) async throws -> Response {
        let (provider, call) = try registry.prepare(
            model: model,
            messages: messages,
            system: system,
            tools: tools,
            webSearch: webSearch,
            effort: effort,
            maxTokens: maxTokens,
            timeout: timeout
        )
        return try await complete(provider, call)
    }

    /// Send `messages` to `model` and return its response as it arrives.
    ///
    /// The request is sent on the first iteration, and every error is thrown
    /// from the iteration (STREAM-5). Ending the iteration early closes the
    /// vendor's response.
    public func stream(
        model: String,
        messages: [Message],
        system: String? = nil,
        tools: [Tool] = [],
        webSearch: Bool = false,
        effort: Effort? = nil,
        maxTokens: Int? = nil,
        timeout: TimeInterval? = nil
    ) -> EventStream {
        EventStream { [registry] in
            let (provider, call) = try registry.prepare(
                model: model,
                messages: messages,
                system: system,
                tools: tools,
                webSearch: webSearch,
                effort: effort,
                maxTokens: maxTokens,
                timeout: timeout
            )
            let request = try provider.buildRequest(call, stream: true)
            // The vendor accepted the stream once this returns: nothing is
            // retried from here (RETRY-7).
            let response = try await self.send(request) { response in
                if response.isError {
                    throw provider.error(status: response.status, body: try await collect(response.body))
                }
                return response
            }
            return (response.body, provider.streamParser())
        }
    }

    private func complete(_ provider: any Provider, _ call: ModelCall) async throws -> Response {
        let request = try provider.buildRequest(call, stream: false)
        return try await send(request) { response in
            try provider.parseResponse(status: response.status, body: try await collect(response.body))
        }
    }

    /// What `accept` makes of the response to `request`. While an attempt fails
    /// in a way that may pass, the same request is sent again (RETRY-2,
    /// RETRY-8); once the retries are spent, the last attempt's error is thrown
    /// (RETRY-6).
    private func send<T>(
        _ request: HTTPRequest, accept: (HTTPResponse) async throws -> T
    ) async throws -> T {
        var retries = 0
        while true {
            var headers: [String: String]?
            do {
                let response: HTTPResponse
                do {
                    response = try await transport.send(request)
                } catch {
                    throw transportError(error)
                }
                headers = response.headers
                return try await accept(response)
            } catch let error as UniLLMError {
                guard
                    let delay = retry.delay(
                        error,
                        retries: retries,
                        headers: headers,
                        now: Date().timeIntervalSince1970,
                        jitter: Double.random(in: 0..<1)
                    )
                else {
                    throw error
                }
                try await Task.sleep(nanoseconds: UInt64(delay * 1_000_000_000))
            }
            retries += 1
        }
    }
}

/// The whole body, its transport errors thrown as `UniLLMError`.
func collect(_ body: AsyncThrowingStream<Data, any Error>) async throws -> Data {
    var data = Data()
    do {
        for try await chunk in body {
            data.append(chunk)
        }
    } catch {
        throw transportError(error)
    }
    return data
}

/// The answer `ask` returns: the text after the last tool block, skipping any
/// preamble. Throws instead of returning nothing (EFFORT-6).
func answer(of response: Response) throws -> String {
    if response.stopReason == .maxTokens {
        throw UniLLMError(.incompleteResponse, "Reached the output token limit before the answer was complete")
    }
    var texts: [String] = []
    for block in response.message.content {
        switch block {
        case .text(let text): texts.append(text.text)
        case .reasoning: break
        default: texts.removeAll()
        }
    }
    let answer = texts.joined()
    if answer.isEmpty {
        throw UniLLMError(.incompleteResponse, "The response contained no answer text")
    }
    return answer
}

/// The events of one streamed response. Iterate it once.
public struct EventStream: AsyncSequence, Sendable {
    public typealias Element = StreamEvent

    typealias Start = @Sendable () async throws -> (AsyncThrowingStream<Data, any Error>, any StreamParser)

    private let start: Start

    init(start: @escaping Start) {
        self.start = start
    }

    public func makeAsyncIterator() -> Iterator {
        Iterator(start: start)
    }

    /// Releasing the iterator before the end closes the vendor's response
    /// (STREAM-6).
    public final class Iterator: AsyncIteratorProtocol {
        private var start: Start?
        private var body: AsyncThrowingStream<Data, any Error>.AsyncIterator?
        private var parser: (any StreamParser)?
        private var lines = LineSplitter()
        private var decoder = SSEDecoder()
        private var pending: [StreamEvent] = []
        private var failure: (any Error)?
        private var finished = false

        init(start: @escaping Start) {
            self.start = start
        }

        public func next() async throws -> StreamEvent? {
            do {
                return try await advance()
            } catch {
                close()
                throw error
            }
        }

        private func advance() async throws -> StreamEvent? {
            if let start {
                self.start = nil
                let (body, parser) = try await start()
                self.body = body.makeAsyncIterator()
                self.parser = parser
            }
            while pending.isEmpty {
                // The events before an error come first.
                if let failure {
                    self.failure = nil
                    throw failure
                }
                guard !finished, var parser else {
                    return nil
                }
                do {
                    try await read(&parser)
                } catch {
                    failure = error
                }
                if !finished {
                    self.parser = parser
                }
            }
            return pending.removeFirst()
        }

        /// Reads the next chunk of the body into `pending`.
        private func read(_ parser: inout any StreamParser) async throws {
            let chunk: Data?
            do {
                chunk = try await body?.next()
            } catch {
                throw transportError(error)
            }
            guard let chunk else {
                // An event the stream cut short is dropped, as the vendor never
                // finished it.
                if let line = lines.finish(), let event = decoder.feed(line) {
                    pending += try parser.feed(event)
                }
                pending.append(.completed(try parser.finish()))
                close()
                return
            }
            for line in lines.feed(chunk) {
                if let event = decoder.feed(line) {
                    pending += try parser.feed(event)
                }
            }
        }

        private func close() {
            finished = true
            start = nil
            body = nil
            parser = nil
        }
    }
}
