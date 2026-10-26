// Copyright 2026 The uni-llm-client Authors
// SPDX-License-Identifier: Apache-2.0

import Foundation

/// Synchronous client, for scripts and command-line tools. Each call blocks
/// the calling thread until it ends, so never call it from async code or from
/// a thread that must stay responsive, such as the main thread of an app:
/// use `AsyncClient` there. Create it once and reuse it.
public final class Client: Sendable {
    private let client: AsyncClient

    /// Takes the arguments of `AsyncClient.init`.
    public init(
        apiKeys: [String: String] = [:],
        providers: [String: ProviderConfig] = [:],
        transport: (any HTTPTransport)? = nil,
        maxRetries: Int = 2,
        initialRetryDelay: TimeInterval = 0.5,
        maxRetryDelay: TimeInterval = 8,
        maxRetryAfter: TimeInterval = 60
    ) throws {
        client = try AsyncClient(
            apiKeys: apiKeys,
            providers: providers,
            transport: transport,
            maxRetries: maxRetries,
            initialRetryDelay: initialRetryDelay,
            maxRetryDelay: maxRetryDelay,
            maxRetryAfter: maxRetryAfter
        )
    }

    /// Ask `model`, a `"provider/model"` id, a question and return the answer.
    public func ask(
        model: String,
        question: String,
        webSearch: Bool = false,
        effort: Effort? = nil,
        timeout: TimeInterval? = nil
    ) throws -> String {
        try blocking { [client] in
            try await client.ask(
                model: model, question: question, webSearch: webSearch, effort: effort, timeout: timeout
            )
        }
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
    ) throws -> Response {
        try blocking { [client] in
            try await client.complete(
                model: model,
                messages: messages,
                system: system,
                tools: tools,
                webSearch: webSearch,
                effort: effort,
                maxTokens: maxTokens,
                timeout: timeout
            )
        }
    }

    /// Send `messages` to `model` and return its response as it arrives.
    ///
    /// The request is sent on the first call of `next()`, and every error is
    /// thrown from it (STREAM-5). `close()`, or releasing the stream, closes
    /// the vendor's response.
    public func stream(
        model: String,
        messages: [Message],
        system: String? = nil,
        tools: [Tool] = [],
        webSearch: Bool = false,
        effort: Effort? = nil,
        maxTokens: Int? = nil,
        timeout: TimeInterval? = nil
    ) -> BlockingEventStream {
        BlockingEventStream(
            client.stream(
                model: model,
                messages: messages,
                system: system,
                tools: tools,
                webSearch: webSearch,
                effort: effort,
                maxTokens: maxTokens,
                timeout: timeout
            )
        )
    }
}

/// The events of one streamed response, each read with a blocking `next()`:
///
///     let events = client.stream(model: model, messages: messages)
///     while let event = try events.next() { ... }
public final class BlockingEventStream: @unchecked Sendable {
    // Used from one thread at a time: each `next()` waits for its read.
    private var iterator: EventStream.Iterator?

    init(_ stream: EventStream) {
        iterator = stream.makeAsyncIterator()
    }

    /// The next event, or `nil` after the last one.
    public func next() throws -> StreamEvent? {
        guard iterator != nil else {
            return nil
        }
        return try blocking { [self] in try await iterator?.next() }
    }

    /// Closes the vendor's response, complete or not (STREAM-6).
    public func close() {
        iterator = nil
    }
}

/// The result of `operation`, waited for on this thread.
private func blocking<T: Sendable>(_ operation: @escaping @Sendable () async throws -> T) throws -> T {
    let outcome = Outcome<T>()
    let done = DispatchSemaphore(value: 0)
    Task.detached {
        do {
            outcome.result = .success(try await operation())
        } catch {
            outcome.result = .failure(error)
        }
        done.signal()
    }
    done.wait()
    return try outcome.result!.get()
}

/// Carries a result from the task to the waiting thread, which reads it only
/// after the semaphore orders the write before.
private final class Outcome<T>: @unchecked Sendable {
    var result: Result<T, any Error>?
}
