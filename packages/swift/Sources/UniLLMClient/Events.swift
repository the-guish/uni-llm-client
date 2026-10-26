// Copyright 2026 The uni-llm-client Authors
// SPDX-License-Identifier: Apache-2.0

/// An event of a stream, as specified in docs/specs/streaming.md.
public enum StreamEvent: Sendable, Hashable {
    /// A piece of a text block.
    case textDelta(String)
    /// A piece of a reasoning block.
    case reasoningDelta(String)
    /// The model started a tool call; its input follows as `toolUseDelta`s.
    case toolUseStart(id: String, name: String)
    /// A piece of the JSON text of a tool call's input.
    case toolUseDelta(id: String, partialJSON: String)
    /// A tool call whose input is complete.
    case toolUseEnd(ToolUseBlock)
    /// The last event of a stream: the response `complete` would have returned.
    case completed(Response)
}
