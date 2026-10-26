// Copyright 2026 The uni-llm-client Authors
// SPDX-License-Identifier: Apache-2.0

// The conversation model, as specified in docs/specs/messages.md.
//
// Vendor-neutral: every provider translates these to its own API and back.
// Messages and responses have a JSON form of their own, in Serialisation.swift,
// for callers that store them.

public enum Role: String, Sendable, Hashable, CaseIterable {
    case user
    case assistant
}

/// Text the model wrote, or text the user sent.
public struct TextBlock: Sendable, Hashable {
    public var text: String

    public init(_ text: String) {
        self.text = text
    }
}

/// What the model thought before answering.
///
/// A vendor that signs its reasoning gets it back through `Message.vendor`,
/// never from this block.
public struct ReasoningBlock: Sendable, Hashable {
    public var text: String

    /// The vendor withheld the text; `text` is empty.
    public var redacted: Bool

    public init(_ text: String, redacted: Bool = false) {
        self.text = text
        self.redacted = redacted
    }
}

/// The model calling one of the caller's tools.
public struct ToolUseBlock: Sendable, Hashable {
    public var id: String
    public var name: String
    public var input: [String: JSONValue]

    public init(id: String, name: String, input: [String: JSONValue] = [:]) {
        self.id = id
        self.name = name
        self.input = input
    }
}

/// What a tool answered, in a user message, before any text of the user's.
public struct ToolResultBlock: Sendable, Hashable {
    public var toolUseID: String
    public var content: String
    public var isError: Bool

    public init(toolUseID: String, content: String, isError: Bool = false) {
        self.toolUseID = toolUseID
        self.content = content
        self.isError = isError
    }
}

/// The vendor running one of its own tools, such as web search, for the model.
public struct ServerToolUseBlock: Sendable, Hashable {
    public var id: String
    public var name: String
    public var input: [String: JSONValue]

    public init(id: String, name: String, input: [String: JSONValue] = [:]) {
        self.id = id
        self.name = name
        self.input = input
    }
}

/// The vendor's tool answered; the vendor keeps what it said.
public struct ServerToolResultBlock: Sendable, Hashable {
    public var toolUseID: String

    public init(toolUseID: String) {
        self.toolUseID = toolUseID
    }
}

public enum ContentBlock: Sendable, Hashable {
    case text(TextBlock)
    case reasoning(ReasoningBlock)
    case toolUse(ToolUseBlock)
    case toolResult(ToolResultBlock)
    case serverToolUse(ServerToolUseBlock)
    case serverToolResult(ServerToolResultBlock)
}

/// An assistant message as its provider returned it.
///
/// Replayed as it is to any provider of the same kind, so that nothing is
/// lost: signed reasoning, citations, the vendor's own tool blocks. A provider
/// of another kind never sees it.
public struct VendorContent: Sendable, Hashable {
    /// The kind of the provider that returned it.
    public var kind: String
    public var content: JSONValue

    public init(kind: String, content: JSONValue) {
        self.kind = kind
        self.content = content
    }
}

public struct Message: Sendable, Hashable {
    public var role: Role
    public var content: [ContentBlock]
    public var vendor: VendorContent?

    public init(role: Role, content: [ContentBlock], vendor: VendorContent? = nil) {
        self.role = role
        self.content = content
        self.vendor = vendor
    }

    public static func user(_ text: String) -> Message {
        Message(role: .user, content: [.text(TextBlock(text))])
    }

    public static func assistant(_ text: String) -> Message {
        Message(role: .assistant, content: [.text(TextBlock(text))])
    }

    /// The user message that answers the tool calls of an assistant message.
    public static func toolResults(_ results: [ToolResultBlock]) -> Message {
        Message(role: .user, content: results.map(ContentBlock.toolResult))
    }

    /// Every text block, joined.
    public var text: String {
        content.compactMap { block in
            if case .text(let text) = block { return text.text }
            return nil
        }.joined()
    }

    public var toolUses: [ToolUseBlock] {
        content.compactMap { block in
            if case .toolUse(let toolUse) = block { return toolUse }
            return nil
        }
    }
}

/// A tool the model may call; the caller runs it.
public struct Tool: Sendable, Hashable {
    public var name: String
    public var description: String

    /// A JSON Schema object describing the input.
    public var inputSchema: [String: JSONValue]

    public init(name: String, description: String, inputSchema: [String: JSONValue]) {
        self.name = name
        self.description = description
        self.inputSchema = inputSchema
    }
}

/// Why the model stopped.
public enum StopReason: String, Sendable, Hashable, CaseIterable {
    /// The answer is complete.
    case endTurn = "end_turn"
    /// The model waits for the results of the tool calls in the message.
    case toolUse = "tool_use"
    /// The output limit cut the message short.
    case maxTokens = "max_tokens"
    /// A vendor's tool loop paused; send the message back to continue it.
    case pauseTurn = "pause_turn"
}

public struct Usage: Sendable, Hashable {
    /// Every input token, the cached ones included.
    public var inputTokens: Int
    /// Every output token, the reasoning included.
    public var outputTokens: Int
    /// Of `inputTokens`, those read from the vendor's cache.
    public var cachedInputTokens: Int
    /// Of `outputTokens`, those spent reasoning, when the vendor counts them.
    public var reasoningTokens: Int

    public init(
        inputTokens: Int = 0,
        outputTokens: Int = 0,
        cachedInputTokens: Int = 0,
        reasoningTokens: Int = 0
    ) {
        self.inputTokens = inputTokens
        self.outputTokens = outputTokens
        self.cachedInputTokens = cachedInputTokens
        self.reasoningTokens = reasoningTokens
    }
}

/// What `complete` returns and a stream ends with.
public struct Response: Sendable, Hashable {
    public var message: Message
    public var stopReason: StopReason
    public var usage: Usage

    public init(message: Message, stopReason: StopReason, usage: Usage) {
        self.message = message
        self.stopReason = stopReason
        self.usage = usage
    }

    public var text: String { message.text }
    public var toolUses: [ToolUseBlock] { message.toolUses }
}
