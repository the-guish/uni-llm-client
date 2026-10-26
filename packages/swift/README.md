# uni-llm-client for Swift

The Swift port of uni-llm-client: one signature for the LLM APIs of several
vendors, as the specs in [`docs/specs`](../../docs/specs) define it. It passes
the language-agnostic tests in [`tests`](../../tests/README.md).

It needs Swift 6 and Foundation alone. It runs on macOS 13, iOS 16, tvOS 16,
watchOS 9 and visionOS 1 or newer, and on Linux.

## Use

```swift
import UniLLMClient

// Create it once and reuse it. Keys come from ANTHROPIC_API_KEY and
// OPENROUTER_API_KEY, or from `apiKeys`.
let client = try AsyncClient()

let answer = try await client.ask(
    model: "anthropic/claude-opus-5",
    question: "What is the capital of France?",
    effort: .low
)
```

`complete` sends a conversation, with an optional system prompt and tools, and
returns the whole response: its message, why it stopped, and the tokens used.

```swift
let weather = Tool(
    name: "get_weather",
    description: "Current weather in a city.",
    inputSchema: ["type": "object", "properties": ["city": ["type": "string"]], "required": ["city"]]
)
var messages: [Message] = [.user("What is the weather in Paris?")]
let response = try await client.complete(model: "anthropic/claude-opus-5", messages: messages, tools: [weather])
if response.stopReason == .toolUse {
    messages.append(response.message)
    messages.append(.toolResults(response.toolUses.map { ToolResultBlock(toolUseID: $0.id, content: "18°C and sunny") }))
}
```

`stream` takes the same arguments and returns the response as it arrives. The
request is sent on the first iteration; ending the loop early closes it.

```swift
for try await event in client.stream(model: "openrouter/openai/gpt-oss-20b", messages: messages) {
    switch event {
    case .textDelta(let text): print(text, terminator: "")
    case .completed(let response): print("\n", response.usage)
    default: break
    }
}
```

`Client` has the same methods without `async`, for scripts and command-line
tools. Each call blocks its thread, so use `AsyncClient` in apps and in async
code.

### Providers

The built-in providers are `anthropic` and `openrouter`. A provider of your own
reaches a gateway or another deployment of one of their APIs, under a name of
your own:

```swift
let client = try AsyncClient(providers: [
    "gateway": ProviderConfig(kind: "anthropic", apiKey: key, baseURL: "https://llm.example.com"),
])
let answer = try await client.ask(model: "gateway/claude-opus-5", question: "Hello?")
```

### Errors

Every call throws `UniLLMError`, whose `kind` is one of the error types every
port shares: `invalidArgument`, `authentication`, `rateLimit`, `refusal`,
`incompleteResponse`, `transport` and `provider`. Failures that may pass on
another attempt are retried, twice by default (`maxRetries`).

### Storing a conversation

`Message` and `Response` have the JSON form of
[messages.md](../../docs/specs/messages.md#serialisation): `json` gives it,
`init(json:)` reads it back, and `Codable` writes and reads the same form.

```swift
let data = try JSONEncoder().encode(messages)
let stored = try JSONDecoder().decode([Message].self, from: data)
```

## Layout

| Path | What it is |
|---|---|
| `Sources/UniLLMClient/` | The library: the clients, messages, events, errors and retries |
| `Sources/UniLLMClient/Providers/` | `Provider`, the protocol every vendor implements, and one folder per vendor |
| `Tests/UniLLMClientTests/` | Unit tests for what the HTTP tests cannot reach |
| `Tests/TestServer/` | The test server the language-agnostic tests call |
| `Scripts/coverage.sh` | The coverage of the test servers after the HTTP tests |

See [CONTRIBUTING.md](CONTRIBUTING.md) to work on it.
