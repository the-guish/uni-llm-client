// Copyright 2026 The uni-llm-client Authors
// SPDX-License-Identifier: Apache-2.0

import Testing

@testable import UniLLMClient

// The mock vendor ends every line with `\n`; vendors may also use `\r\n` or `\r`.

@Test func splitsLinesOnEveryLineEnding() {
    var lines = LineSplitter()
    #expect(lines.feed(Array("a\r\nb\rc\n".utf8)) == ["a", "b", "c"])
}

@Test func waitsForTheByteAfterACarriageReturn() {
    var lines = LineSplitter()
    #expect(lines.feed(Array("a\r".utf8)) == [])
    #expect(lines.feed(Array("\nb\r".utf8)) == ["a"])
    #expect(lines.finish() == "b")
}
