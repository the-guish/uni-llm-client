// Copyright 2026 The uni-llm-client Authors
// SPDX-License-Identifier: Apache-2.0

import Foundation

#if canImport(FoundationNetworking)
    import FoundationNetworking
#endif

/// A request to a vendor.
public struct HTTPRequest: Sendable {
    public var method: String
    public var url: URL
    public var headers: [String: String]
    public var body: Data

    /// Seconds to wait for the connection and for each read; the transport's
    /// own when `nil`.
    public var timeout: TimeInterval?

    public init(
        method: String, url: URL, headers: [String: String] = [:], body: Data = Data(),
        timeout: TimeInterval? = nil
    ) {
        self.method = method
        self.url = url
        self.headers = headers
        self.body = body
        self.timeout = timeout
    }
}

/// A vendor's answer, its body read as it arrives.
public struct HTTPResponse: Sendable {
    public var status: Int

    /// The headers, by lowercase name.
    public var headers: [String: String]

    /// The body, in chunks. Dropping the iteration before its end closes the
    /// response.
    public var body: AsyncThrowingStream<Data, any Error>

    public init(status: Int, headers: [String: String], body: AsyncThrowingStream<Data, any Error>) {
        self.status = status
        self.headers = Dictionary(headers.map { ($0.key.lowercased(), $0.value) }, uniquingKeysWith: { $1 })
        self.body = body
    }

    var isError: Bool { status >= 400 }
}

/// Sends the requests a client builds. `URLSessionTransport` is the default;
/// another can route, record or replay the requests.
public protocol HTTPTransport: Sendable {
    /// The response to `request`, once its status and headers arrived.
    ///
    /// A connection failure or a timeout throws; the client raises it as
    /// `UniLLMError` of kind `transport` (RETRY-5).
    func send(_ request: HTTPRequest) async throws -> HTTPResponse
}

/// Sends requests with a `URLSession`, streaming each body as it arrives.
public final class URLSessionTransport: HTTPTransport, Sendable {
    /// Answers can take minutes, especially at high effort.
    public static let defaultTimeout: TimeInterval = 600

    private let session: URLSession
    private let tasks: TaskDelegate

    /// - Parameter configuration: the session's configuration; an ephemeral
    ///   one, without cookies or cache, waiting `defaultTimeout` seconds for
    ///   each read, when `nil`.
    public init(configuration: URLSessionConfiguration? = nil) {
        let configuration = configuration ?? Self.defaultConfiguration()
        tasks = TaskDelegate()
        session = URLSession(configuration: configuration, delegate: tasks, delegateQueue: nil)
    }

    deinit {
        // A stream still read after its client is gone ends as it would have.
        session.finishTasksAndInvalidate()
    }

    public static func defaultConfiguration() -> URLSessionConfiguration {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = defaultTimeout
        configuration.urlCache = nil
        configuration.httpCookieStorage = nil
        return configuration
    }

    public func send(_ request: HTTPRequest) async throws -> HTTPResponse {
        var urlRequest = URLRequest(url: request.url)
        urlRequest.httpMethod = request.method
        for (name, value) in request.headers {
            urlRequest.setValue(value, forHTTPHeaderField: name)
        }
        urlRequest.httpBody = request.body
        if let timeout = request.timeout {
            // URLSession on Linux cuts the timeout to whole seconds, so the
            // delegate keeps the exact one; URLSession's is only a backstop.
            urlRequest.timeoutInterval = timeout.rounded(.up) + 1
        }
        let task = session.dataTask(with: urlRequest)
        return try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { continuation in
                tasks.start(task, timeout: request.timeout, continuation)
            }
        } onCancel: {
            task.cancel()
        }
    }
}

/// Hands each task's response and body to the one waiting for them, and
/// cancels a task that waits longer than its timeout for its response or for
/// a piece of its body.
///
/// The response is handed over with the first piece of its body, or when the
/// task ends for a response without one: the delegate methods it implements
/// take no completion handler.
private final class TaskDelegate: NSObject, URLSessionDataDelegate, @unchecked Sendable {
    private struct Waiting {
        var response: ResponseContinuation?
        var body: BodyContinuation?
        var timeout: TimeInterval?
        var timer: DispatchWorkItem?
        /// Counts the timer's restarts, so that an old timer knows it is one.
        var restarts = 0
        var timedOut = false
    }

    private let lock = NSLock()
    private var waiting: [Int: Waiting] = [:]

    func start(
        _ task: URLSessionDataTask, timeout: TimeInterval?,
        _ continuation: ResponseContinuation
    ) {
        locked {
            waiting[task.taskIdentifier] = Waiting(response: continuation, timeout: timeout)
            restartTimer(task)
        }
        task.resume()
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
        let (waiter, body, stream) = locked { () -> (ResponseContinuation?, BodyContinuation?, Body?) in
            restartTimer(dataTask)
            guard let waiter = waiting[dataTask.taskIdentifier]?.response else {
                return (nil, waiting[dataTask.taskIdentifier]?.body, nil)
            }
            let (stream, body) = Self.body(of: dataTask)
            waiting[dataTask.taskIdentifier]?.response = nil
            waiting[dataTask.taskIdentifier]?.body = body
            return (waiter, body, stream)
        }
        body?.yield(data)
        if let waiter, let stream {
            waiter.resume(returning: Self.response(of: dataTask, body: stream))
        }
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: (any Error)?) {
        let ended = locked { waiting.removeValue(forKey: task.taskIdentifier) }
        ended?.timer?.cancel()
        var failure = error.map(transportError)
        if let ended, ended.timedOut, let timeout = ended.timeout {
            failure = UniLLMError(.transport, "The vendor sent nothing for \(timeout) seconds")
        }
        if let waiter = ended?.response {
            if let failure {
                waiter.resume(throwing: failure)
            } else {
                // A response without a body.
                let (stream, body) = Self.body(of: task)
                body.finish()
                waiter.resume(returning: Self.response(of: task, body: stream))
            }
        } else if let body = ended?.body {
            body.finish(throwing: failure)
        }
    }

    typealias ResponseContinuation = CheckedContinuation<HTTPResponse, any Error>
    private typealias Body = AsyncThrowingStream<Data, any Error>
    private typealias BodyContinuation = Body.Continuation

    /// A new body for `task`, which ending its iteration early cancels.
    private static func body(of task: URLSessionTask) -> (Body, BodyContinuation) {
        let (stream, continuation) = Body.makeStream()
        continuation.onTermination = { termination in
            if case .cancelled = termination {
                task.cancel()
            }
        }
        return (stream, continuation)
    }

    private static func response(of task: URLSessionTask, body: Body) -> HTTPResponse {
        let http = task.response as? HTTPURLResponse
        var headers: [String: String] = [:]
        for (name, value) in http?.allHeaderFields ?? [:] {
            headers[String(describing: name)] = String(describing: value)
        }
        return HTTPResponse(status: http?.statusCode ?? 0, headers: headers, body: body)
    }

    /// Starts the task's timeout again, under the lock.
    private func restartTimer(_ task: URLSessionTask) {
        let id = task.taskIdentifier
        guard let timeout = waiting[id]?.timeout else {
            return
        }
        waiting[id]?.timer?.cancel()
        waiting[id]?.restarts += 1
        let restarts = waiting[id]?.restarts
        let timer = DispatchWorkItem { [weak self, weak task] in
            guard let self, let task else { return }
            let expired = self.locked { () -> Bool in
                guard self.waiting[id]?.restarts == restarts else {
                    return false
                }
                self.waiting[id]?.timedOut = true
                return true
            }
            if expired {
                task.cancel()
            }
        }
        waiting[id]?.timer = timer
        DispatchQueue.global().asyncAfter(deadline: .now() + timeout, execute: timer)
    }

    private func locked<T>(_ body: () throws -> T) rethrows -> T {
        lock.lock()
        defer { lock.unlock() }
        return try body()
    }
}

/// The error a client raises for a failure of the HTTP library.
func transportError(_ error: any Error) -> any Error {
    if error is UniLLMError || error is CancellationError {
        return error
    }
    if let error = error as? URLError, error.code == .cancelled {
        return CancellationError()
    }
    return UniLLMError(.transport, error.localizedDescription)
}
