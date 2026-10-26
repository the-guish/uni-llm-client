// Copyright 2026 The uni-llm-client Authors
// SPDX-License-Identifier: Apache-2.0

// Anthropic Messages API.

import Foundation

struct AnthropicProvider: Provider {
    static let kind = "anthropic"
    static let apiKeyEnv = "ANTHROPIC_API_KEY"
    static let defaultBaseURL = "https://api.anthropic.com"
    static let path = "/v1/messages"

    static let apiVersion = "2023-06-01"

    /// Output tokens reserved for the answer when the caller sets no limit.
    /// Token-budget models add their thinking budget on top, since the budget
    /// must be lower than max_tokens.
    static let maxTokens = 16_000

    /// Thinking budgets for token-budget models; `max` uses the model's full
    /// output limit.
    static let budgetTokens: [Effort: Int] = [.low: 1_024, .medium: 8_192, .high: 16_384]

    static let stopReasons: [String: StopReason] = [
        "end_turn": .endTurn,
        "stop_sequence": .endTurn,
        "tool_use": .toolUse,
        "max_tokens": .maxTokens,
        "model_context_window_exceeded": .maxTokens,
        "pause_turn": .pauseTurn,
    ]

    let apiKey: String
    let baseURL: String

    func buildRequest(_ call: ModelCall, stream: Bool) throws -> HTTPRequest {
        let info = AnthropicModels.lookup(call.model)
        let maxTokens = call.maxTokens ?? Self.maxTokens
        var body: [String: JSONValue] = ["model": .string(call.model), "max_tokens": .int(maxTokens)]
        if let system = call.system {
            body["system"] = .string(system)
        }
        body["messages"] = .array(call.messages.map { message(of: $0) })
        if var effort = call.effort {
            if let info {
                effort = resolveEffort(effort, supported: info.efforts)
            }
            if let info, info.thinking == .budget {
                let budget = Self.budgetTokens[effort] ?? info.maxOutputTokens - maxTokens
                body["max_tokens"] = .int(budget + maxTokens)
                body["thinking"] = ["type": "enabled", "budget_tokens": .int(budget)]
            } else {
                // Current models leave the reasoning's text out unless asked,
                // and bill it the same either way (EFFORT-7).
                body["thinking"] = ["type": "adaptive", "display": "summarized"]
                body["output_config"] = ["effort": .string(effort.rawValue)]
            }
        }
        var tools: [JSONValue] = []
        if call.webSearch {
            let tool = info?.webSearchTool ?? AnthropicModels.defaultWebSearchTool
            tools.append(["type": .string(tool), "name": "web_search"])
        }
        tools += call.tools.map { tool in
            [
                "name": .string(tool.name),
                "description": .string(tool.description),
                "input_schema": .object(tool.inputSchema),
            ]
        }
        if !tools.isEmpty {
            body["tools"] = .array(tools)
        }
        if stream {
            body["stream"] = true
        }
        let headers = ["x-api-key": apiKey, "anthropic-version": Self.apiVersion]
        return try post(headers: headers, body: .object(body), timeout: call.timeout)
    }

    func parseResponse(status: Int, body: Data) throws -> Response {
        if status >= 400 {
            throw error(status: status, body: body)
        }
        return try response(of: try vendorJSON(body))
    }

    func error(status: Int, body: Data) -> UniLLMError {
        httpError(status, errorMessage(status: status, body: body))
    }

    func streamParser() -> any StreamParser {
        AnthropicStreamParser(provider: self)
    }

    /// The response a complete message is, streamed or not.
    func response(of message: JSONValue) throws -> Response {
        guard let stopReason = message["stop_reason"] else {
            throw malformed("stop_reason")
        }
        if stopReason == "refusal" {
            let explanation = message["stop_details"]?["explanation"]?.stringValue ?? ""
            throw UniLLMError(.refusal, explanation.isEmpty ? "The model declined to answer" : explanation)
        }
        guard let reason = stopReason.stringValue.flatMap({ Self.stopReasons[$0] }) else {
            throw UniLLMError(.provider, "Unknown stop reason \(stopReason)")
        }
        guard let content = message["content"]?.arrayValue else {
            throw malformed("content")
        }
        return Response(
            message: Message(
                role: .assistant,
                content: try content.compactMap(block(of:)),
                vendor: VendorContent(kind: Self.kind, content: .array(content))
            ),
            stopReason: reason,
            usage: usage(of: message["usage"]?.objectValue ?? [:])
        )
    }

    private func message(of message: Message) -> JSONValue {
        if message.role == .assistant, let vendor = message.vendor, vendor.kind == Self.kind {
            return ["role": "assistant", "content": vendor.content]
        }
        let blocks = message.content.compactMap(rawBlock)
        // The API requires tool results before any other block (MSG-3); every
        // other block keeps its order.
        let sorted = blocks.filter { $0["type"] == "tool_result" } + blocks.filter { $0["type"] != "tool_result" }
        var content = JSONValue.array(sorted)
        if sorted.count == 1, sorted[0]["type"] == "text", let text = sorted[0]["text"] {
            content = text
        }
        return ["role": .string(message.role.rawValue), "content": content]
    }

    private func rawBlock(_ block: ContentBlock) -> JSONValue? {
        switch block {
        case .text(let block):
            return ["type": "text", "text": .string(block.text)]
        case .toolUse(let block):
            return [
                "type": "tool_use", "id": .string(block.id), "name": .string(block.name), "input": .object(block.input),
            ]
        case .toolResult(let block):
            var raw: [String: JSONValue] = [
                "type": "tool_result",
                "tool_use_id": .string(block.toolUseID),
                "content": .string(block.content),
            ]
            if block.isError {
                raw["is_error"] = true
            }
            return .object(raw)
        case .reasoning, .serverToolUse, .serverToolResult:
            // Reasoning without its signature, and another vendor's tool blocks.
            return nil
        }
    }

    private func block(of raw: JSONValue) throws -> ContentBlock? {
        guard let type = raw["type"]?.stringValue else {
            throw malformed("content block type")
        }
        switch type {
        case "text":
            guard let text = raw["text"]?.stringValue else { throw malformed("text") }
            return .text(TextBlock(text))
        case "thinking":
            return .reasoning(ReasoningBlock(raw["thinking"]?.stringValue ?? ""))
        case "redacted_thinking":
            return .reasoning(ReasoningBlock("", redacted: true))
        case "tool_use":
            let (id, name, input) = try toolCall(raw)
            return .toolUse(ToolUseBlock(id: id, name: name, input: input))
        case "server_tool_use", "mcp_tool_use":
            let (id, name, input) = try toolCall(raw)
            return .serverToolUse(ServerToolUseBlock(id: id, name: name, input: input))
        case _ where type.hasSuffix("_tool_result"):
            return .serverToolResult(ServerToolResultBlock(toolUseID: raw["tool_use_id"]?.stringValue ?? ""))
        default:
            return nil
        }
    }

    private func toolCall(_ raw: JSONValue) throws -> (String, String, [String: JSONValue]) {
        guard let id = raw["id"]?.stringValue, let name = raw["name"]?.stringValue else {
            throw malformed("tool call")
        }
        return (id, name, raw["input"]?.objectValue ?? [:])
    }

    private func usage(of usage: [String: JSONValue]) -> Usage {
        let cached = usage["cache_read_input_tokens"]?.intValue ?? 0
        let written = usage["cache_creation_input_tokens"]?.intValue ?? 0
        return Usage(
            inputTokens: (usage["input_tokens"]?.intValue ?? 0) + cached + written,
            outputTokens: usage["output_tokens"]?.intValue ?? 0,
            cachedInputTokens: cached
        )
    }

    private func errorMessage(status: Int, body: Data) -> String {
        if let message = (try? JSONValue.decode(body))?["error"]?["message"]?.stringValue {
            return message
        }
        let text = String(decoding: body, as: UTF8.self)
        return text.isEmpty ? "HTTP \(status)" : text
    }
}

/// Rebuilds the message from its events, as the vendor would have sent it whole.
struct AnthropicStreamParser: StreamParser {
    /// Error types of events sent mid-stream, where there is no HTTP status to map.
    private static let errorKinds: [String: UniLLMError.Kind] = [
        "authentication_error": .authentication,
        "permission_error": .authentication,
        "rate_limit_error": .rateLimit,
    ]

    /// Blocks whose input streams as partial JSON.
    private static let jsonInputBlocks: Set<String> = ["tool_use", "server_tool_use", "mcp_tool_use"]

    let provider: AnthropicProvider
    private var message: [String: JSONValue]?
    private var blocks: [Int: [String: JSONValue]] = [:]
    private var json: [Int: String] = [:]
    private var stopped = false

    init(provider: AnthropicProvider) {
        self.provider = provider
    }

    mutating func feed(_ event: ServerSentEvent) throws -> [StreamEvent] {
        let data = try vendorJSON(event.data)
        switch data["type"]?.stringValue {
        case "message_start":
            guard let started = data["message"]?.objectValue else { throw malformed("message") }
            message = started
        case "content_block_start":
            guard let block = data["content_block"]?.objectValue else { throw malformed("content block") }
            return try start(index(of: data), block)
        case "content_block_delta":
            guard let delta = data["delta"] else { throw malformed("delta") }
            return try self.delta(index(of: data), delta)
        case "content_block_stop":
            return try stop(index(of: data))
        case "message_delta":
            guard var started = message else {
                throw UniLLMError(.provider, "The stream sent content before its message started")
            }
            for (key, value) in data["delta"]?.objectValue ?? [:] {
                started[key] = value
            }
            var usage = started["usage"]?.objectValue ?? [:]
            for (key, value) in data["usage"]?.objectValue ?? [:] {
                usage[key] = value
            }
            started["usage"] = .object(usage)
            message = started
        case "message_stop":
            stopped = true
        case "error":
            throw streamError(data["error"] ?? .null)
        default:
            // `ping`, and events this version does not know, carry nothing to keep.
            break
        }
        return []
    }

    func finish() throws -> Response {
        guard var message, stopped, message["stop_reason"] != nil else {
            throw UniLLMError(.incompleteResponse, "The stream ended before the message was complete")
        }
        message["content"] = .array(blocks.keys.sorted().map { .object(blocks[$0]!) })
        return try provider.response(of: .object(message))
    }

    private func index(of data: JSONValue) throws -> Int {
        guard let index = data["index"]?.intValue else { throw malformed("index") }
        return index
    }

    private func block(_ index: Int) throws -> [String: JSONValue] {
        guard let block = blocks[index] else { throw malformed("content block index") }
        return block
    }

    private mutating func start(_ index: Int, _ block: [String: JSONValue]) throws -> [StreamEvent] {
        // Fields the vendor leaves empty would be sent back as such on replay.
        let block = block.filter { $0.value != .null }
        blocks[index] = block
        guard let type = block["type"]?.stringValue, Self.jsonInputBlocks.contains(type) else {
            return []
        }
        json[index] = ""
        guard type == "tool_use" else {
            return []
        }
        guard let id = block["id"]?.stringValue, let name = block["name"]?.stringValue else {
            throw malformed("tool call")
        }
        return [.toolUseStart(id: id, name: name)]
    }

    private mutating func delta(_ index: Int, _ delta: JSONValue) throws -> [StreamEvent] {
        var block = try self.block(index)
        defer { blocks[index] = block }
        func appended(_ field: String, _ key: String) throws -> String {
            guard let piece = delta[key]?.stringValue else { throw malformed(key) }
            block[field] = .string((block[field]?.stringValue ?? "") + piece)
            return piece
        }
        switch delta["type"]?.stringValue {
        case "text_delta":
            let text = try appended("text", "text")
            return text.isEmpty ? [] : [.textDelta(text)]
        case "thinking_delta":
            let thinking = try appended("thinking", "thinking")
            return thinking.isEmpty ? [] : [.reasoningDelta(thinking)]
        case "signature_delta":
            _ = try appended("signature", "signature")
        case "input_json_delta":
            guard let piece = delta["partial_json"]?.stringValue else { throw malformed("partial_json") }
            json[index, default: ""] += piece
            if block["type"] == "tool_use", !piece.isEmpty, let id = block["id"]?.stringValue {
                return [.toolUseDelta(id: id, partialJSON: piece)]
            }
        case "citations_delta":
            var citations = block["citations"]?.arrayValue ?? []
            citations.append(delta["citation"] ?? .null)
            block["citations"] = .array(citations)
        default:
            break
        }
        return []
    }

    private mutating func stop(_ index: Int) throws -> [StreamEvent] {
        var block = try self.block(index)
        guard let text = json.removeValue(forKey: index) else {
            return []
        }
        let input = try parseToolInput(text)
        block["input"] = .object(input)
        blocks[index] = block
        guard block["type"] == "tool_use", let id = block["id"]?.stringValue,
            let name = block["name"]?.stringValue
        else {
            return []
        }
        return [.toolUseEnd(ToolUseBlock(id: id, name: name, input: input))]
    }

    private func streamError(_ error: JSONValue) -> UniLLMError {
        let kind = error["type"]?.stringValue.flatMap { Self.errorKinds[$0] } ?? .provider
        let message = error["message"]?.stringValue ?? ""
        return UniLLMError(kind, message.isEmpty ? "Unknown error" : message)
    }
}
