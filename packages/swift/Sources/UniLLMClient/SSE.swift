// Copyright 2026 The uni-llm-client Authors
// SPDX-License-Identifier: Apache-2.0

// Server-sent events, decoded line by line and without I/O.

struct ServerSentEvent: Sendable, Equatable {
    /// The `event` field, or `"message"` when the event has none.
    var event: String
    /// The `data` lines, joined with newlines.
    var data: String
}

/// Feed it each line of the response, without its line ending.
struct SSEDecoder {
    private var event = ""
    private var data: [String] = []

    /// The event the line completes, if it completes one.
    mutating func feed(_ line: String) -> ServerSentEvent? {
        if line.isEmpty {
            return dispatch()
        }
        if line.hasPrefix(":") {
            return nil  // a comment, such as a vendor's keep-alive
        }
        let name: Substring
        var value: Substring
        if let colon = line.firstIndex(of: ":") {
            name = line[..<colon]
            value = line[line.index(after: colon)...]
            if value.hasPrefix(" ") {
                value = value.dropFirst()
            }
        } else {
            name = line[...]
            value = ""
        }
        if name == "event" {
            event = String(value)
        } else if name == "data" {
            data.append(String(value))
        }
        // `id` and `retry` are for reconnecting, which this library does not do.
        return nil
    }

    private mutating func dispatch() -> ServerSentEvent? {
        defer {
            event = ""
            data = []
        }
        if data.isEmpty {
            return nil
        }
        return ServerSentEvent(event: event.isEmpty ? "message" : event, data: data.joined(separator: "\n"))
    }
}

/// Splits bytes into lines ended by `\n`, `\r\n` or `\r`.
struct LineSplitter {
    private var buffer: [UInt8] = []

    /// The lines `bytes` completes.
    mutating func feed(_ bytes: some Sequence<UInt8>) -> [String] {
        buffer.append(contentsOf: bytes)
        var lines: [String] = []
        var start = 0
        var index = 0
        while index < buffer.count {
            let byte = buffer[index]
            if byte == UInt8(ascii: "\n") {
                lines.append(String(decoding: buffer[start..<index], as: UTF8.self))
                start = index + 1
            } else if byte == UInt8(ascii: "\r") {
                // A `\r` last may be the start of a `\r\n`: wait for the next byte.
                if index + 1 == buffer.count {
                    break
                }
                lines.append(String(decoding: buffer[start..<index], as: UTF8.self))
                if buffer[index + 1] == UInt8(ascii: "\n") {
                    index += 1
                }
                start = index + 1
            }
            index += 1
        }
        buffer.removeFirst(start)
        return lines
    }

    /// The last line, when the bytes end without a line ending.
    mutating func finish() -> String? {
        defer { buffer = [] }
        if buffer.last == UInt8(ascii: "\r") {
            buffer.removeLast()
            return String(decoding: buffer, as: UTF8.self)
        }
        return buffer.isEmpty ? nil : String(decoding: buffer, as: UTF8.self)
    }
}
