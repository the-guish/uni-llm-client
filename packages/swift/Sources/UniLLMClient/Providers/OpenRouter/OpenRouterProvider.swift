// Copyright 2026 The uni-llm-client Authors
// SPDX-License-Identifier: Apache-2.0

// OpenRouter Chat Completions API, which serves models from many vendors.

import Foundation

struct OpenRouterProvider: Provider {
    static let kind = "openrouter"
    static let apiKeyEnv = "OPENROUTER_API_KEY"
    static let defaultBaseURL = "https://openrouter.ai/api/v1"
    static let path = "/chat/completions"

    static let finishReasons: [String: StopReason] = [
        "stop": .endTurn,
        "tool_calls": .toolUse,
        "length": .maxTokens,
    ]

    static let refusalErrorTypes: Set<String> = ["refusal", "content_policy_violation"]

    /// The fields of an assistant message sent back as they came, so that a
    /// vendor behind OpenRouter gets its own reasoning back
    /// (`reasoning_details` carries it).
    static let replayed = ["content", "tool_calls", "reasoning", "reasoning_details"]

    let apiKey: String
    let baseURL: String

    func buildRequest(_ call: ModelCall, stream: Bool) throws -> HTTPRequest {
        var body: [String: JSONValue] = [
            "model": .string(call.model),
            "messages": .array(try messages(call)),
        ]
        if let maxTokens = call.maxTokens {
            body["max_tokens"] = .int(maxTokens)
        }
        if let effort = call.effort {
            // OpenRouter maps the effort to the nearest level the model supports.
            body["reasoning"] = ["effort": .string(effort.rawValue)]
        }
        var tools: [JSONValue] = []
        if call.webSearch {
            tools.append(["type": "openrouter:web_search"])
        }
        tools += call.tools.map { tool in
            [
                "type": "function",
                "function": [
                    "name": .string(tool.name),
                    "description": .string(tool.description),
                    "parameters": .object(tool.inputSchema),
                ],
            ]
        }
        if !tools.isEmpty {
            body["tools"] = .array(tools)
        }
        if stream {
            body["stream"] = true
        }
        return try post(headers: ["authorization": "Bearer \(apiKey)"], body: .object(body), timeout: call.timeout)
    }

    func parseResponse(status: Int, body: Data) throws -> Response {
        if status >= 400 {
            throw error(status: status, body: body)
        }
        return try response(of: try vendorJSON(body))
    }

    func error(status: Int, body: Data) -> UniLLMError {
        let error: JSONValue
        if let found = (try? JSONValue.decode(body))?["error"] {
            error = found.objectValue != nil ? found : ["message": .string(found.stringValue ?? found.description)]
        } else {
            let text = String(decoding: body, as: UTF8.self)
            error = ["message": .string(text.isEmpty ? "HTTP \(status)" : text)]
        }
        return Self.error(of: error, code: status)
    }

    func streamParser() -> any StreamParser {
        OpenRouterStreamParser(provider: self)
    }

    /// The response a complete body is, streamed or not.
    func response(of body: JSONValue) throws -> Response {
        // Failures after generation starts still return HTTP 200.
        if let error = body["error"] {
            throw Self.error(of: error, code: error["code"]?.intValue)
        }
        guard let choice = body["choices"]?.arrayValue?.first, let message = choice["message"] else {
            throw malformed("choices")
        }
        let finishReason = choice["finish_reason"]?.stringValue
        if finishReason == "error" {
            let error = choice["error"] ?? [:]
            throw Self.error(of: error, code: error["code"]?.intValue)
        }
        let refusal = message["refusal"]
        if finishReason == "content_filter" || refusal?.isTruthy == true {
            throw UniLLMError(.refusal, refusal?.stringValue ?? "The model declined to answer")
        }
        guard var stopReason = finishReason.flatMap({ Self.finishReasons[$0] }) else {
            throw UniLLMError(.provider, "Unknown finish reason \(choice["finish_reason"] ?? .null)")
        }
        if message["tool_calls"]?.isTruthy == true {
            // Some vendors behind OpenRouter say "stop" with calls to make.
            stopReason = .toolUse
        }
        var replayed: [String: JSONValue] = [:]
        for key in Self.replayed {
            if let value = message[key], value != .null {
                replayed[key] = value
            }
        }
        return Response(
            message: Message(
                role: .assistant,
                content: try blocks(of: message),
                vendor: VendorContent(kind: Self.kind, content: .object(replayed))
            ),
            stopReason: stopReason,
            usage: usage(of: body["usage"]?.objectValue ?? [:])
        )
    }

    /// The error a vendor's error object is; `code` is its HTTP status or the
    /// code of an error in the body.
    static func error(of error: JSONValue, code: Int?) -> UniLLMError {
        let text = error["message"]?.stringValue ?? ""
        let message = text.isEmpty ? "Unknown error" : text
        let errorType = error["metadata"]?["error_type"]?.stringValue
        // OpenRouter uses 403 for guardrail, moderation and model refusals.
        if code == 403 || errorType.map(refusalErrorTypes.contains) == true {
            return UniLLMError(.refusal, message)
        }
        guard let code else {
            return UniLLMError(.provider, message)
        }
        return httpError(code, message)
    }

    private func messages(_ call: ModelCall) throws -> [JSONValue] {
        var messages: [JSONValue] = []
        if let system = call.system {
            messages.append(["role": "system", "content": .string(system)])
        }
        for message in call.messages {
            if message.role == .assistant {
                messages.append(try assistant(message))
            } else {
                messages += user(message)
            }
        }
        return messages
    }

    private func assistant(_ message: Message) throws -> JSONValue {
        if let vendor = message.vendor, vendor.kind == Self.kind, var raw = vendor.content.objectValue {
            raw["role"] = "assistant"
            return .object(raw)
        }
        let text = message.text
        var raw: [String: JSONValue] = ["role": "assistant", "content": text.isEmpty ? .null : .string(text)]
        let calls = try message.toolUses.map { block -> JSONValue in
            let arguments = String(decoding: try JSONValue.object(block.input).encoded(), as: UTF8.self)
            return [
                "id": .string(block.id),
                "type": "function",
                "function": ["name": .string(block.name), "arguments": .string(arguments)],
            ]
        }
        if !calls.isEmpty {
            raw["tool_calls"] = .array(calls)
        }
        return .object(raw)
    }

    /// Tool results are messages of their own on this API; they come first.
    private func user(_ message: Message) -> [JSONValue] {
        var messages: [JSONValue] = []
        var texts: [String] = []
        for block in message.content {
            switch block {
            case .toolResult(let result):
                messages.append([
                    "role": "tool", "tool_call_id": .string(result.toolUseID), "content": .string(result.content),
                ])
            case .text(let text):
                texts.append(text.text)
            default:
                break
            }
        }
        if texts.count == 1 {
            messages.append(["role": "user", "content": .string(texts[0])])
        } else if !texts.isEmpty {
            messages.append(["role": "user", "content": .array(texts.map { ["type": "text", "text": .string($0)] })])
        }
        return messages
    }

    private func blocks(of message: JSONValue) throws -> [ContentBlock] {
        var blocks: [ContentBlock] = []
        if let reasoning = message["reasoning"], reasoning.isTruthy {
            blocks.append(.reasoning(ReasoningBlock(reasoning.stringValue ?? "\(reasoning)")))
        }
        blocks += texts(message["content"]).map { .text(TextBlock($0)) }
        for call in message["tool_calls"]?.arrayValue ?? [] {
            blocks.append(.toolUse(try toolUse(of: call)))
        }
        return blocks
    }

    private func usage(of usage: [String: JSONValue]) -> Usage {
        Usage(
            inputTokens: usage["prompt_tokens"]?.intValue ?? 0,
            outputTokens: usage["completion_tokens"]?.intValue ?? 0,
            cachedInputTokens: usage["prompt_tokens_details"]?["cached_tokens"]?.intValue ?? 0,
            reasoningTokens: usage["completion_tokens_details"]?["reasoning_tokens"]?.intValue ?? 0
        )
    }
}

/// The tool call of an OpenRouter `tool_calls` entry.
func toolUse(of call: JSONValue) throws -> ToolUseBlock {
    let function = call["function"]
    return ToolUseBlock(
        id: call["id"]?.stringValue ?? "",
        name: function?["name"]?.stringValue ?? "",
        input: try parseToolInput(function?["arguments"]?.stringValue ?? "")
    )
}

/// The texts of a message's `content`: a string, or text parts.
func texts(_ content: JSONValue?) -> [String] {
    switch content {
    case .string(let text):
        return text.isEmpty ? [] : [text]
    case .array(let parts):
        return parts.compactMap { part in
            guard part["type"] == "text", let text = part["text"]?.stringValue, !text.isEmpty else {
                return nil
            }
            return text
        }
    default:
        return []
    }
}

/// Rebuilds the choice from its chunks, as the vendor would have sent it whole.
struct OpenRouterStreamParser: StreamParser {
    /// Streamed `reasoning_details` whose text comes in pieces, joined by `index`.
    private static let joinedDetailFields: Set<String> = ["text", "summary", "data"]

    let provider: OpenRouterProvider
    private var content = ""
    private var reasoning = ""
    private var details: [[String: JSONValue]] = []
    private var detailAt: [Int: Int] = [:]
    private var calls: [Int: (id: String, name: String, arguments: String)] = [:]
    private var announced: Set<Int> = []
    private var refusal = ""
    private var finishReason: String?
    private var error: JSONValue?
    private var usage: JSONValue?
    private var ended = false

    init(provider: OpenRouterProvider) {
        self.provider = provider
    }

    mutating func feed(_ event: ServerSentEvent) throws -> [StreamEvent] {
        if event.data.trimmingCharacters(in: .whitespacesAndNewlines) == "[DONE]" {
            return []
        }
        let data = try vendorJSON(event.data)
        if let error = data["error"] {
            throw OpenRouterProvider.error(of: error, code: error["code"]?.intValue)
        }
        if let usage = data["usage"], usage.isTruthy {
            self.usage = usage
        }
        guard let choice = data["choices"]?.arrayValue?.first else {
            return []
        }
        var events: [StreamEvent] = []
        let delta = choice["delta"] ?? [:]
        for text in texts(delta["content"]) {
            content += text
            events.append(.textDelta(text))
        }
        if let piece = delta["reasoning"]?.stringValue, !piece.isEmpty {
            reasoning += piece
            events.append(.reasoningDelta(piece))
        }
        for detail in delta["reasoning_details"]?.arrayValue ?? [] {
            if let detail = detail.objectValue {
                merge(detail)
            }
        }
        for call in delta["tool_calls"]?.arrayValue ?? [] {
            events += toolCall(call)
        }
        if let piece = delta["refusal"]?.stringValue {
            refusal += piece
        }
        if let reason = choice["finish_reason"]?.stringValue, !reason.isEmpty {
            finishReason = reason
            if reason == "error" {
                error = choice["error"] ?? [:]
            }
            events += try ends()
        }
        return events
    }

    func finish() throws -> Response {
        guard let finishReason else {
            throw UniLLMError(.incompleteResponse, "The stream ended before the message was complete")
        }
        var message: [String: JSONValue] = [
            "role": "assistant",
            "content": content.isEmpty ? .null : .string(content),
        ]
        if !reasoning.isEmpty {
            message["reasoning"] = .string(reasoning)
        }
        if !details.isEmpty {
            message["reasoning_details"] = .array(details.map(JSONValue.object))
        }
        if !calls.isEmpty {
            message["tool_calls"] = .array(calls.keys.sorted().map { Self.raw(calls[$0]!) })
        }
        if !refusal.isEmpty {
            message["refusal"] = .string(refusal)
        }
        var choice: [String: JSONValue] = ["finish_reason": .string(finishReason), "message": .object(message)]
        if let error {
            choice["error"] = error
        }
        var body: [String: JSONValue] = ["choices": [.object(choice)]]
        if let usage {
            body["usage"] = usage
        }
        return try provider.response(of: .object(body))
    }

    private static func raw(_ call: (id: String, name: String, arguments: String)) -> JSONValue {
        [
            "id": .string(call.id),
            "type": "function",
            "function": ["name": .string(call.name), "arguments": .string(call.arguments)],
        ]
    }

    private mutating func toolCall(_ call: JSONValue) -> [StreamEvent] {
        let index = call["index"]?.intValue ?? 0
        var entry = calls[index] ?? (id: "", name: "", arguments: "")
        if let id = call["id"]?.stringValue, !id.isEmpty {
            entry.id = id
        }
        let function = call["function"]
        if let name = function?["name"]?.stringValue {
            entry.name += name
        }
        let arguments = function?["arguments"]?.stringValue ?? ""
        entry.arguments += arguments
        calls[index] = entry
        var events: [StreamEvent] = []
        if !announced.contains(index), !entry.id.isEmpty, !entry.name.isEmpty {
            announced.insert(index)
            events.append(.toolUseStart(id: entry.id, name: entry.name))
        }
        if !arguments.isEmpty, announced.contains(index) {
            events.append(.toolUseDelta(id: entry.id, partialJSON: arguments))
        }
        return events
    }

    private mutating func ends() throws -> [StreamEvent] {
        if ended {
            return []
        }
        ended = true
        return try calls.keys.sorted().map { .toolUseEnd(try toolUse(of: Self.raw(calls[$0]!))) }
    }

    private mutating func merge(_ detail: [String: JSONValue]) {
        let index = detail["index"]?.intValue
        if let index, let at = detailAt[index] {
            var existing = details[at]
            for (key, value) in detail {
                if Self.joinedDetailFields.contains(key), let piece = value.stringValue {
                    existing[key] = .string((existing[key]?.stringValue ?? "") + piece)
                } else if existing[key] == nil {
                    existing[key] = value
                }
            }
            details[at] = existing
            return
        }
        details.append(detail)
        if let index {
            detailAt[index] = details.count - 1
        }
    }
}
