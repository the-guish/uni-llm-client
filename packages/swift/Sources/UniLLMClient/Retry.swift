// Copyright 2026 The uni-llm-client Authors
// SPDX-License-Identifier: Apache-2.0

// When a failed attempt is sent again, and how long the client waits first.
//
// Decisions only, without I/O or sleeping (docs/specs/retries.md).

import Foundation

/// Whether a client sends a failed request again, and when.
struct RetryPolicy: Sendable {
    static let defaultMaxRetries = 2

    // RETRY-3 by default: from half a second, doubling, up to 8 seconds.
    static let defaultInitialRetryDelay: TimeInterval = 0.5
    static let defaultMaxRetryDelay: TimeInterval = 8

    // RETRY-4 by default: the longest wait a vendor can ask for and get.
    static let defaultMaxRetryAfter: TimeInterval = 60

    // RETRY-2, besides every 5xx: a request timeout, a conflict and a rate limit.
    static let retryableStatuses: Set<Int> = [408, 409, 429]

    /// Retries after the first attempt; 0 disables them (RETRY-1).
    let maxRetries: Int
    /// Seconds before the first retry, doubled for each one after (RETRY-3).
    let initialRetryDelay: TimeInterval
    /// The longest wait the doubling reaches (RETRY-3).
    let maxRetryDelay: TimeInterval
    /// The longest wait a vendor can ask for and get (RETRY-4).
    let maxRetryAfter: TimeInterval

    init(
        maxRetries: Int, initialRetryDelay: TimeInterval, maxRetryDelay: TimeInterval,
        maxRetryAfter: TimeInterval
    ) throws {
        if maxRetries < 0 {
            throw UniLLMError(.invalidArgument, "Expected a max_retries of 0 or more, got \(maxRetries)")
        }
        for (name, value) in [
            ("initial_retry_delay", initialRetryDelay),
            ("max_retry_delay", maxRetryDelay),
            ("max_retry_after", maxRetryAfter),
        ] where !(value >= 0 && value.isFinite) {
            throw UniLLMError(.invalidArgument, "Expected a finite \(name) of 0 or more, got \(value)")
        }
        self.maxRetries = maxRetries
        self.initialRetryDelay = initialRetryDelay
        self.maxRetryDelay = maxRetryDelay
        self.maxRetryAfter = maxRetryAfter
    }

    /// The seconds to wait before sending the request again, or nil when the
    /// client throws `error` instead (RETRY-6).
    ///
    /// - Parameters:
    ///   - error: What the last attempt threw.
    ///   - retries: The retries already made.
    ///   - headers: The last attempt's response headers, if it received a response.
    ///   - now: The current time as a POSIX timestamp, for a retry-after date.
    ///   - jitter: A random number in [0, 1).
    func delay(
        _ error: UniLLMError, retries: Int, headers: [String: String]?, now: TimeInterval, jitter: Double
    ) -> TimeInterval? {
        if retries >= maxRetries || !isRetryable(error) {
            return nil
        }
        if let asked = headers.flatMap({ retryAfter($0, now: now) }), asked >= 0, asked <= maxRetryAfter {
            return asked
        }
        return backoff(retries: retries, jitter: jitter)
    }

    /// The wait before the next retry when the vendor asks for none:
    /// `initialRetryDelay` after the first attempt, doubling up to
    /// `maxRetryDelay`, less up to a quarter (RETRY-3).
    func backoff(retries: Int, jitter: Double) -> TimeInterval {
        // A capped exponent keeps a large max_retries from overflowing.
        let longest = min(initialRetryDelay * pow(2, Double(min(retries, 32))), maxRetryDelay)
        return longest * (1 - jitter / 4)
    }
}

/// Whether another attempt may pass where one threw `error` (RETRY-2).
///
/// The decision is on the status code the error carries, which is the HTTP
/// status or the code of an error in the body of a successful response, as
/// OpenRouter sends: both shapes get the same answer.
func isRetryable(_ error: UniLLMError) -> Bool {
    if error.kind == .transport {
        return true
    }
    guard let status = error.statusCode else {
        return false
    }
    return RetryPolicy.retryableStatuses.contains(status) || (500...599).contains(status)
}

/// The seconds the vendor asks to wait (RETRY-4): `retry-after-ms`, else
/// `retry-after` in seconds or as an HTTP date, counted from `now`. Nil when it
/// asks for nothing readable.
func retryAfter(_ headers: [String: String], now: TimeInterval) -> TimeInterval? {
    if let milliseconds = number(headers["retry-after-ms"]) {
        return milliseconds / 1000
    }
    guard let value = headers["retry-after"] else {
        return nil
    }
    if let seconds = number(value) {
        return seconds
    }
    return httpDate(value).map { $0.timeIntervalSince1970 - now }
}

private func number(_ value: String?) -> Double? {
    guard let value, let number = Double(value.trimmingCharacters(in: .whitespaces)) else {
        return nil
    }
    return number.isFinite ? number : nil
}

/// The date of an HTTP date, such as `Wed, 21 Oct 2015 07:28:00 GMT`. A zone of
/// `-0000` is UTC.
private func httpDate(_ value: String) -> Date? {
    let formatter = DateFormatter()
    formatter.locale = Locale(identifier: "en_US_POSIX")
    formatter.timeZone = TimeZone(identifier: "UTC")
    for format in ["EEE, dd MMM yyyy HH:mm:ss zzz", "EEE, dd MMM yyyy HH:mm:ss Z", "EEE, dd MMM yyyy HH:mm:ss"] {
        formatter.dateFormat = format
        if let date = formatter.date(from: value.trimmingCharacters(in: .whitespaces)) {
            return date
        }
    }
    return nil
}
