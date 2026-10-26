// Copyright 2026 The uni-llm-client Authors
// SPDX-License-Identifier: Apache-2.0

// The JSON form of messages and responses (docs/specs/messages.md,
// Serialisation): `json` gives it and `init(json:)` reads it back. `Codable`
// reads and writes the same form.

import Foundation

extension Message {
    /// The message's JSON form: every field, defaults included (MSG-9).
    public var json: JSONValue {
        [
            "role": .string(role.rawValue),
            "content": .array(content.map(\.json)),
            "vendor": vendor.map { ["kind": .string($0.kind), "content": $0.content] } ?? .null,
        ]
    }

    /// The message whose JSON form is `json` (MSG-11).
    ///
    /// - Throws: `UniLLMError` of kind `invalidArgument` when `json` is not
    ///   the JSON form of a message (MSG-12).
    public init(json: JSONValue) throws {
        self = try messageOf(json, "message")
    }
}

extension Response {
    /// The response's JSON form: every field, defaults included (MSG-9).
    public var json: JSONValue {
        [
            "message": message.json,
            "stop_reason": .string(stopReason.rawValue),
            "usage": [
                "input_tokens": .int(usage.inputTokens),
                "output_tokens": .int(usage.outputTokens),
                "cached_input_tokens": .int(usage.cachedInputTokens),
                "reasoning_tokens": .int(usage.reasoningTokens),
            ],
        ]
    }

    /// The response whose JSON form is `json` (MSG-11).
    ///
    /// - Throws: `UniLLMError` of kind `invalidArgument` when `json` is not
    ///   the JSON form of a response (MSG-12).
    public init(json: JSONValue) throws {
        let fields = try object(json, "response", required: ["message", "stop_reason", "usage"])
        let stopReason = try checked(fields["stop_reason"]!, \.stringValue, "response.stop_reason", "a string")
        guard let reason = StopReason(rawValue: stopReason) else {
            throw invalid("Unknown stop reason '\(stopReason)'")
        }
        let usage = try object(
            fields["usage"]!,
            "response.usage",
            optional: ["input_tokens", "output_tokens", "cached_input_tokens", "reasoning_tokens"]
        )
        func count(_ name: String) throws -> Int {
            guard let value = usage[name] else { return 0 }
            return try checked(value, \.intValue, "response.usage.\(name)", "an integer")
        }
        self.init(
            message: try messageOf(fields["message"]!, "response.message"),
            stopReason: reason,
            usage: Usage(
                inputTokens: try count("input_tokens"),
                outputTokens: try count("output_tokens"),
                cachedInputTokens: try count("cached_input_tokens"),
                reasoningTokens: try count("reasoning_tokens")
            )
        )
    }
}

extension ContentBlock {
    /// The block's JSON form, with its `type` (MSG-10).
    public var json: JSONValue {
        switch self {
        case .text(let block):
            ["type": "text", "text": .string(block.text)]
        case .reasoning(let block):
            ["type": "reasoning", "text": .string(block.text), "redacted": .bool(block.redacted)]
        case .toolUse(let block):
            ["type": "tool_use", "id": .string(block.id), "name": .string(block.name), "input": .object(block.input)]
        case .toolResult(let block):
            [
                "type": "tool_result",
                "tool_use_id": .string(block.toolUseID),
                "content": .string(block.content),
                "is_error": .bool(block.isError),
            ]
        case .serverToolUse(let block):
            [
                "type": "server_tool_use",
                "id": .string(block.id),
                "name": .string(block.name),
                "input": .object(block.input),
            ]
        case .serverToolResult(let block):
            ["type": "server_tool_result", "tool_use_id": .string(block.toolUseID)]
        }
    }

    /// The block whose JSON form is `json`.
    ///
    /// - Throws: `UniLLMError` of kind `invalidArgument` when `json` is not
    ///   the JSON form of a block (MSG-12).
    public init(json: JSONValue) throws {
        self = try blockOf(json, "block")
    }
}

extension Message: Codable {
    public init(from decoder: any Decoder) throws {
        let json = try JSONValue(from: decoder)
        do {
            try self.init(json: json)
        } catch let error as UniLLMError {
            throw DecodingError.dataCorrupted(
                DecodingError.Context(codingPath: decoder.codingPath, debugDescription: error.message)
            )
        }
    }

    public func encode(to encoder: any Encoder) throws {
        try json.encode(to: encoder)
    }
}

extension Response: Codable {
    public init(from decoder: any Decoder) throws {
        let json = try JSONValue(from: decoder)
        do {
            try self.init(json: json)
        } catch let error as UniLLMError {
            throw DecodingError.dataCorrupted(
                DecodingError.Context(codingPath: decoder.codingPath, debugDescription: error.message)
            )
        }
    }

    public func encode(to encoder: any Encoder) throws {
        try json.encode(to: encoder)
    }
}

private func messageOf(_ json: JSONValue, _ what: String) throws -> Message {
    let fields = try object(json, what, required: ["role", "content"], optional: ["vendor"])
    let roleName = try checked(fields["role"]!, \.stringValue, "\(what).role", "a string")
    guard let role = Role(rawValue: roleName) else {
        throw invalid("Unknown role '\(roleName)'")
    }
    let content = try checked(fields["content"]!, \.arrayValue, "\(what).content", "an array")
    let blocks = try content.enumerated().map { index, block in
        try blockOf(block, "\(what).content[\(index)]")
    }
    guard let vendorJSON = fields["vendor"], vendorJSON != .null else {
        return Message(role: role, content: blocks)
    }
    let vendor = try object(vendorJSON, "\(what).vendor", required: ["kind", "content"])
    return Message(
        role: role,
        content: blocks,
        vendor: VendorContent(
            kind: try checked(vendor["kind"]!, \.stringValue, "\(what).vendor.kind", "a string"),
            content: vendor["content"]!
        )
    )
}

private func blockOf(_ json: JSONValue, _ what: String) throws -> ContentBlock {
    guard let fields = json.objectValue else {
        throw invalid("\(what) must be an object")
    }
    guard let type = fields["type"] else {
        throw invalid("\(what) has no 'type'")
    }
    let reader = BlockReader(fields: fields, what: what)
    switch type.stringValue {
    case "text":
        try reader.check(required: ["text"])
        return .text(TextBlock(try reader.string("text")))
    case "reasoning":
        try reader.check(required: ["text"], optional: ["redacted"])
        return .reasoning(ReasoningBlock(try reader.string("text"), redacted: try reader.bool("redacted") ?? false))
    case "tool_use":
        try reader.check(required: ["id", "name"], optional: ["input"])
        return .toolUse(
            ToolUseBlock(id: try reader.string("id"), name: try reader.string("name"), input: try reader.input())
        )
    case "tool_result":
        try reader.check(required: ["tool_use_id", "content"], optional: ["is_error"])
        return .toolResult(
            ToolResultBlock(
                toolUseID: try reader.string("tool_use_id"),
                content: try reader.string("content"),
                isError: try reader.bool("is_error") ?? false
            )
        )
    case "server_tool_use":
        try reader.check(required: ["id", "name"], optional: ["input"])
        return .serverToolUse(
            ServerToolUseBlock(id: try reader.string("id"), name: try reader.string("name"), input: try reader.input())
        )
    case "server_tool_result":
        try reader.check(required: ["tool_use_id"])
        return .serverToolResult(ServerToolResultBlock(toolUseID: try reader.string("tool_use_id")))
    default:
        throw invalid("\(what) has an unknown type \(type)")
    }
}

/// Reads the fields of a block, each of its JSON type.
private struct BlockReader {
    let fields: [String: JSONValue]
    let what: String

    func check(required: [String], optional: [String] = []) throws {
        _ = try object(.object(fields), what, required: ["type"] + required, optional: optional)
    }

    func string(_ name: String) throws -> String {
        try checked(fields[name]!, \.stringValue, "\(what).\(name)", "a string")
    }

    func bool(_ name: String) throws -> Bool? {
        guard let value = fields[name] else { return nil }
        return try checked(value, \.boolValue, "\(what).\(name)", "true or false")
    }

    func input() throws -> [String: JSONValue] {
        guard let value = fields["input"] else { return [:] }
        return try checked(value, \.objectValue, "\(what).input", "an object")
    }
}

/// The fields of `json`, if it is an object with every key `required` and no
/// unknown one.
private func object(
    _ json: JSONValue, _ what: String, required: [String] = [], optional: [String] = []
) throws -> [String: JSONValue] {
    guard let fields = json.objectValue else {
        throw invalid("\(what) must be an object")
    }
    for key in required where fields[key] == nil {
        throw invalid("\(what) has no '\(key)'")
    }
    for key in fields.keys.sorted() where !required.contains(key) && !optional.contains(key) {
        throw invalid("\(what) has an unknown field '\(key)'")
    }
    return fields
}

/// The value of `json`, if it is of the JSON type `value` reads.
private func checked<T>(
    _ json: JSONValue, _ value: KeyPath<JSONValue, T?>, _ what: String, _ expected: String
) throws -> T {
    guard let result = json[keyPath: value] else {
        throw invalid("\(what) must be \(expected)")
    }
    return result
}

private func invalid(_ message: String) -> UniLLMError {
    UniLLMError(.invalidArgument, message)
}
