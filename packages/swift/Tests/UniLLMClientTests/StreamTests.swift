// Copyright 2026 The uni-llm-client Authors
// SPDX-License-Identifier: Apache-2.0

import Foundation
import Testing

@testable import UniLLMClient

/// Answers every request with a stream that sends one text delta and then
/// waits, and records whether the client closed it.
final class OpenStreamTransport: HTTPTransport, @unchecked Sendable {
    private let lock = NSLock()
    private var isClosed = false

    var closed: Bool {
        lock.lock()
        defer { lock.unlock() }
        return isClosed
    }

    func send(_ request: HTTPRequest) async throws -> HTTPResponse {
        let (body, continuation) = AsyncThrowingStream<Data, any Error>.makeStream()
        continuation.onTermination = { _ in
            self.lock.lock()
            self.isClosed = true
            self.lock.unlock()
        }
        let events = """
            event: content_block_start
            data: {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}

            event: content_block_delta
            data: {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Paris"}}


            """
        continuation.yield(Data(events.utf8))
        return HTTPResponse(status: 200, headers: ["content-type": "text/event-stream"], body: body)
    }
}

// STREAM-6: no HTTP case stops reading before the end.

@Test func endingTheIterationEarlyClosesTheResponse() async throws {
    let transport = OpenStreamTransport()
    let client = try AsyncClient(apiKeys: ["anthropic": "test-key"], transport: transport)
    for try await event in client.stream(model: "anthropic/claude-opus-5", messages: [.user("Capital of France?")]) {
        #expect(event == .textDelta("Paris"))
        break
    }
    #expect(transport.closed)
}

@Test func closingABlockingStreamClosesTheResponse() throws {
    let transport = OpenStreamTransport()
    let client = try Client(apiKeys: ["anthropic": "test-key"], transport: transport)
    let events = client.stream(model: "anthropic/claude-opus-5", messages: [.user("Capital of France?")])
    #expect(try events.next() == .textDelta("Paris"))
    events.close()
    #expect(transport.closed)
    #expect(try events.next() == nil)
}
