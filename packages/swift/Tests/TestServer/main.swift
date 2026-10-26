// Copyright 2026 The uni-llm-client Authors
// SPDX-License-Identifier: Apache-2.0

// Serves the client over HTTP for the language-agnostic tests in the
// repository root.
//
// Run `TestServer --vendor-url URL [--client sync|async]`. Every vendor request
// goes to `URL`, keeping its path and `Host` header. The protocol, and the JSON
// form of events, is in `tests/README.md` at the repository root. Messages and
// responses take the library's own JSON form (`json`, `init(json:)`).

import Foundation
import UniLLMClient

#if canImport(Glibc)
    import Glibc
#elseif canImport(Darwin)
    import Darwin
#endif

/// A failure of this server or the library, shown to the runner with status 500.
struct ServerError: Error {
    let message: String
}

/// Sends every request to the mock vendor, keeping its path and `Host` header.
struct RedirectTransport: HTTPTransport {
    let vendorURL: URL
    let transport = URLSessionTransport()

    func send(_ request: HTTPRequest) async throws -> HTTPResponse {
        var request = request
        var host = request.url.host ?? ""
        if let port = request.url.port {
            host += ":\(port)"
        }
        var components = URLComponents(url: request.url, resolvingAgainstBaseURL: false)!
        components.scheme = vendorURL.scheme
        components.host = vendorURL.host
        components.port = vendorURL.port
        request.url = components.url!
        request.headers["host"] = host
        return try await transport.send(request)
    }
}

// MARK: Arguments

/// The arguments of a call, read from its JSON. A malformed argument is an
/// `invalidArgument` error, as the library raises for its own.
struct Arguments: Sendable {
    let json: [String: JSONValue]

    /// The value of `key`, absent when null.
    func value(_ key: String) -> JSONValue? {
        guard let value = json[key], value != .null else { return nil }
        return value
    }

    func required(_ key: String) throws -> String {
        guard let value = value(key)?.stringValue else {
            throw ServerError(message: "The arguments have no '\(key)'")
        }
        return value
    }

    func string(_ key: String) throws -> String? {
        try value(key).map { try typed($0.stringValue, key, "a string") }
    }

    func int(_ key: String) throws -> Int? {
        try value(key).map { try typed($0.intValue, key, "an integer") }
    }

    func number(_ key: String) throws -> Double? {
        try value(key).map { try typed($0.doubleValue, key, "a number") }
    }

    func bool(_ key: String) throws -> Bool {
        try value(key).map { try typed($0.boolValue, key, "true or false") } ?? false
    }

    func effort() throws -> Effort? {
        guard let name = try string("effort") else { return nil }
        guard let effort = Effort(rawValue: name) else {
            throw UniLLMError(.invalidArgument, "Unknown effort '\(name)'")
        }
        return effort
    }

    func messages() throws -> [Message] {
        let messages = try value("messages").map { try typed($0.arrayValue, "messages", "an array") } ?? []
        return try messages.map(Message.init(json:))
    }

    func tools() throws -> [Tool] {
        let tools = try value("tools").map { try typed($0.arrayValue, "tools", "an array") } ?? []
        return try tools.map { tool in
            Tool(
                name: try typed(tool["name"]?.stringValue, "tools.name", "a string"),
                description: try typed(tool["description"]?.stringValue, "tools.description", "a string"),
                inputSchema: try typed(tool["input_schema"]?.objectValue, "tools.input_schema", "an object")
            )
        }
    }

    func apiKeys() throws -> [String: String] {
        guard let keys = value("api_keys") else { return [:] }
        return try typed(keys.objectValue, "api_keys", "an object").mapValues {
            try typed($0.stringValue, "api_keys", "strings")
        }
    }

    func providers() throws -> [String: ProviderConfig] {
        guard let providers = value("providers") else { return [:] }
        return try typed(providers.objectValue, "providers", "an object").mapValues { config in
            ProviderConfig(
                kind: try typed(config["kind"]?.stringValue, "providers.kind", "a string"),
                apiKey: config["api_key"]?.stringValue,
                baseURL: config["base_url"]?.stringValue
            )
        }
    }

    private func typed<T>(_ value: T?, _ key: String, _ expected: String) throws -> T {
        guard let value else {
            throw UniLLMError(.invalidArgument, "\(key) must be \(expected)")
        }
        return value
    }
}

// MARK: Calls

/// The three calls, made with a new client created from the arguments: with
/// `api_keys`, `providers`, and the retry options when given.
protocol Calls: Sendable {
    func ask(_ arguments: Arguments) throws -> String
    func complete(_ arguments: Arguments) throws -> Response
    /// The events received, and the error that ended the stream if one did.
    func stream(_ arguments: Arguments) -> ([StreamEvent], (any Error)?)
}

struct SyncCalls: Calls {
    let transport: RedirectTransport

    func client(_ arguments: Arguments) throws -> Client {
        try Client(
            apiKeys: arguments.apiKeys(),
            providers: arguments.providers(),
            transport: transport,
            maxRetries: try arguments.int("max_retries") ?? 2,
            initialRetryDelay: try arguments.number("initial_retry_delay") ?? 0.5,
            maxRetryDelay: try arguments.number("max_retry_delay") ?? 8,
            maxRetryAfter: try arguments.number("max_retry_after") ?? 60
        )
    }

    func ask(_ arguments: Arguments) throws -> String {
        try client(arguments).ask(
            model: try arguments.required("model"),
            question: try arguments.required("question"),
            webSearch: try arguments.bool("web_search"),
            effort: try arguments.effort(),
            timeout: try arguments.number("timeout")
        )
    }

    func complete(_ arguments: Arguments) throws -> Response {
        try client(arguments).complete(
            model: try arguments.required("model"),
            messages: try arguments.messages(),
            system: try arguments.string("system"),
            tools: try arguments.tools(),
            webSearch: try arguments.bool("web_search"),
            effort: try arguments.effort(),
            maxTokens: try arguments.int("max_tokens"),
            timeout: try arguments.number("timeout")
        )
    }

    func stream(_ arguments: Arguments) -> ([StreamEvent], (any Error)?) {
        var events: [StreamEvent] = []
        do {
            let stream = try client(arguments).stream(
                model: try arguments.required("model"),
                messages: try arguments.messages(),
                system: try arguments.string("system"),
                tools: try arguments.tools(),
                webSearch: try arguments.bool("web_search"),
                effort: try arguments.effort(),
                maxTokens: try arguments.int("max_tokens"),
                timeout: try arguments.number("timeout")
            )
            while let event = try stream.next() {
                events.append(event)
            }
        } catch {
            return (events, error)
        }
        return (events, nil)
    }
}

struct AsyncCalls: Calls {
    let transport: RedirectTransport

    func client(_ arguments: Arguments) throws -> AsyncClient {
        try AsyncClient(
            apiKeys: arguments.apiKeys(),
            providers: arguments.providers(),
            transport: transport,
            maxRetries: try arguments.int("max_retries") ?? 2,
            initialRetryDelay: try arguments.number("initial_retry_delay") ?? 0.5,
            maxRetryDelay: try arguments.number("max_retry_delay") ?? 8,
            maxRetryAfter: try arguments.number("max_retry_after") ?? 60
        )
    }

    func ask(_ arguments: Arguments) throws -> String {
        try waitFor {
            try await client(arguments).ask(
                model: try arguments.required("model"),
                question: try arguments.required("question"),
                webSearch: try arguments.bool("web_search"),
                effort: try arguments.effort(),
                timeout: try arguments.number("timeout")
            )
        }
    }

    func complete(_ arguments: Arguments) throws -> Response {
        try waitFor {
            try await client(arguments).complete(
                model: try arguments.required("model"),
                messages: try arguments.messages(),
                system: try arguments.string("system"),
                tools: try arguments.tools(),
                webSearch: try arguments.bool("web_search"),
                effort: try arguments.effort(),
                maxTokens: try arguments.int("max_tokens"),
                timeout: try arguments.number("timeout")
            )
        }
    }

    func stream(_ arguments: Arguments) -> ([StreamEvent], (any Error)?) {
        // The events and the error are a result, never thrown.
        try! waitFor { () async -> ([StreamEvent], (any Error)?) in
            var events: [StreamEvent] = []
            do {
                let stream = try client(arguments).stream(
                    model: try arguments.required("model"),
                    messages: try arguments.messages(),
                    system: try arguments.string("system"),
                    tools: try arguments.tools(),
                    webSearch: try arguments.bool("web_search"),
                    effort: try arguments.effort(),
                    maxTokens: try arguments.int("max_tokens"),
                    timeout: try arguments.number("timeout")
                )
                for try await event in stream {
                    events.append(event)
                }
            } catch {
                return (events, error)
            }
            return (events, nil)
        }
    }
}

/// The result of `operation`, waited for on this thread.
func waitFor<T>(_ operation: @escaping @Sendable () async throws -> T) throws -> T {
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
final class Outcome<T>: @unchecked Sendable {
    var result: Result<T, any Error>?
}

// MARK: Replies

/// The reply for a library error; any other error is thrown again.
func errorReply(_ error: any Error) throws -> [String: JSONValue] {
    guard let error = error as? UniLLMError else {
        throw error
    }
    return ["error": ["type": .string(error.kind.rawValue), "message": .string(error.message)]]
}

/// The response's JSON form, which must read back as the same response.
func responseJSON(_ response: Response) throws -> JSONValue {
    let json = response.json
    // Every response the tests see checks the round trip (MSG-11).
    guard try Response(json: json) == response else {
        throw ServerError(message: "The response does not read back as itself: \(json)")
    }
    return json
}

func eventJSON(_ event: StreamEvent) throws -> JSONValue {
    switch event {
    case .textDelta(let text):
        ["type": "text_delta", "text": .string(text)]
    case .reasoningDelta(let text):
        ["type": "reasoning_delta", "text": .string(text)]
    case .toolUseStart(let id, let name):
        ["type": "tool_use_start", "id": .string(id), "name": .string(name)]
    case .toolUseDelta(let id, let partialJSON):
        ["type": "tool_use_delta", "id": .string(id), "partial_json": .string(partialJSON)]
    case .toolUseEnd(let block):
        // The block has the form it has in a message.
        ["type": "tool_use_end", "block": ContentBlock.toolUse(block).json]
    case .completed(let response):
        ["type": "completed", "response": try responseJSON(response)]
    }
}

func reply(_ calls: any Calls, _ call: String, _ arguments: Arguments) throws -> JSONValue {
    switch call {
    case "ask":
        do {
            return ["answer": .string(try calls.ask(arguments))]
        } catch {
            return .object(try errorReply(error))
        }
    case "complete":
        let response: Response
        do {
            response = try calls.complete(arguments)
        } catch {
            return .object(try errorReply(error))
        }
        return ["response": try responseJSON(response)]
    default:
        let (events, error) = calls.stream(arguments)
        var reply: [String: JSONValue] = ["events": .array(try events.map(eventJSON))]
        if let error {
            reply.merge(try errorReply(error)) { _, new in new }
        }
        return .object(reply)
    }
}

// MARK: HTTP

/// Answers one connection: one `POST /ask`, `/complete` or `/stream`.
func handle(_ connection: Int32, _ calls: any Calls) {
    defer { close(connection) }
    guard let (path, body) = readRequest(connection) else {
        return
    }
    let call = String(path.dropFirst())
    guard ["ask", "complete", "stream"].contains(call) else {
        writeResponse(connection, status: 404, body: Data("{}".utf8))
        return
    }
    do {
        guard let arguments = try JSONValue.decode(body).objectValue else {
            throw ServerError(message: "The arguments are not a JSON object")
        }
        let reply = try reply(calls, call, Arguments(json: arguments))
        writeResponse(connection, status: 200, body: try reply.encoded())
    } catch {
        // A bug in this server or the library: show it to the runner.
        let failure: JSONValue = ["error": .string(String(describing: error))]
        writeResponse(connection, status: 500, body: (try? failure.encoded()) ?? Data())
    }
}

/// The path and body of the request on `connection`.
func readRequest(_ connection: Int32) -> (String, Data)? {
    var received = Data()
    var buffer = [UInt8](repeating: 0, count: 65_536)
    let separator = Data("\r\n\r\n".utf8)
    func receive() -> Bool {
        let count = recv(connection, &buffer, buffer.count, 0)
        if count <= 0 {
            return false
        }
        received.append(contentsOf: buffer[0..<count])
        return true
    }
    var headerEnd: Range<Data.Index>?
    while headerEnd == nil {
        guard receive() else { return nil }
        headerEnd = received.range(of: separator)
    }
    let head = String(decoding: received[..<headerEnd!.lowerBound], as: UTF8.self)
    let lines = head.components(separatedBy: "\r\n")
    let requestLine = lines[0].split(separator: " ")
    guard requestLine.count >= 2 else { return nil }
    var length = 0
    for line in lines.dropFirst() {
        let parts = line.split(separator: ":", maxSplits: 1)
        if parts.count == 2, parts[0].lowercased() == "content-length" {
            length = Int(parts[1].trimmingCharacters(in: .whitespaces)) ?? 0
        }
    }
    while received.count - headerEnd!.upperBound < length {
        guard receive() else { return nil }
    }
    return (String(requestLine[1]), received[headerEnd!.upperBound...].prefix(length))
}

func writeResponse(_ connection: Int32, status: Int, body: Data) {
    let reason = [200: "OK", 404: "Not Found", 500: "Internal Server Error"][status] ?? ""
    let head =
        "HTTP/1.1 \(status) \(reason)\r\nContent-Type: application/json\r\n"
        + "Content-Length: \(body.count)\r\nConnection: close\r\n\r\n"
    var bytes = Data(head.utf8)
    bytes.append(body)
    bytes.withUnsafeBytes { raw in
        var sent = 0
        while sent < raw.count {
            let count = send(connection, raw.baseAddress! + sent, raw.count - sent, noSignal)
            if count <= 0 {
                return
            }
            sent += count
        }
    }
}

#if os(Linux)
    let noSignal = Int32(MSG_NOSIGNAL)
    let streamSocket = Int32(SOCK_STREAM.rawValue)
#else
    let noSignal: Int32 = 0
    let streamSocket = SOCK_STREAM
#endif

/// A socket listening on a free port of 127.0.0.1, and the port.
func listenOnLoopback() -> (Int32, UInt16) {
    let server = socket(AF_INET, streamSocket, 0)
    precondition(server >= 0, "socket failed")
    var address = sockaddr_in()
    address.sin_family = sa_family_t(AF_INET)
    address.sin_port = 0
    address.sin_addr.s_addr = inet_addr("127.0.0.1")
    var length = socklen_t(MemoryLayout<sockaddr_in>.size)
    let bound = withUnsafeMutablePointer(to: &address) { pointer in
        pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) { address in
            bind(server, address, length) == 0 && listen(server, 128) == 0
                && getsockname(server, address, &length) == 0
        }
    }
    precondition(bound, "Could not listen on 127.0.0.1")
    return (server, UInt16(bigEndian: address.sin_port))
}

// MARK: Main

func option(_ name: String) -> String? {
    let arguments = CommandLine.arguments
    guard let index = arguments.firstIndex(of: name), index + 1 < arguments.count else {
        return nil
    }
    return arguments[index + 1]
}

guard let vendorURL = option("--vendor-url").flatMap(URL.init(string:)) else {
    FileHandle.standardError.write(Data("usage: TestServer --vendor-url URL [--client sync|async]\n".utf8))
    exit(2)
}
let transport = RedirectTransport(vendorURL: vendorURL)
let calls: any Calls =
    option("--client") == "async" ? AsyncCalls(transport: transport) : SyncCalls(transport: transport)

// A client that gave up must not end the server.
signal(SIGPIPE, SIG_IGN)
// The runner stops the server with SIGTERM; a normal exit writes the coverage.
signal(SIGTERM, SIG_IGN)
let terminate = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .global())
terminate.setEventHandler { exit(0) }
terminate.resume()

let (server, port) = listenOnLoopback()
FileHandle.standardOutput.write(Data("listening on http://127.0.0.1:\(port)\n".utf8))
while true {
    let connection = accept(server, nil, nil)
    if connection < 0 {
        continue
    }
    Thread.detachNewThread { handle(connection, calls) }
}
