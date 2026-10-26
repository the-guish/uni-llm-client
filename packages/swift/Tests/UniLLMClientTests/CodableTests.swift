// Copyright 2026 The uni-llm-client Authors
// SPDX-License-Identifier: Apache-2.0

import Foundation
import Testing

import UniLLMClient

// The HTTP tests read and write the JSON form through `json`; `Codable` is the
// same form for callers that store conversations with an encoder.

@Test func aMessageEncodesToItsJSONFormAndBack() throws {
    let message = Message(
        role: .assistant,
        content: [.toolUse(ToolUseBlock(id: "toolu_1", name: "get_weather", input: ["city": "Paris"]))],
        vendor: VendorContent(kind: "anthropic", content: [["type": "tool_use", "id": "toolu_1"]])
    )
    let data = try JSONEncoder().encode(message)
    #expect(try JSONValue.decode(data) == message.json)
    #expect(try JSONDecoder().decode(Message.self, from: data) == message)
}

@Test func aMalformedMessageIsADecodingError() {
    let data = Data(#"{"role": "system", "content": []}"#.utf8)
    #expect(throws: DecodingError.self) {
        try JSONDecoder().decode(Message.self, from: data)
    }
}

@Test func aResponseEncodesToItsJSONFormAndBack() throws {
    let response = Response(
        message: .assistant("Paris."),
        stopReason: .endTurn,
        usage: Usage(inputTokens: 14, outputTokens: 3)
    )
    let data = try JSONEncoder().encode(response)
    #expect(try JSONDecoder().decode(Response.self, from: data) == response)
}
