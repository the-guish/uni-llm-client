# Messages

Status: Draft

## Scope

The conversation model every port shares, with its JSON form, and `complete`,
the method that sends a conversation to a model and returns its whole response.
`stream` sends the same conversation and is specified in
[streaming.md](streaming.md). `ask` is `complete` with one user message.

## Model

A **message** has a `role`, `user` or `assistant`, and `content`, a sequence of
blocks. An assistant message a provider returned also carries `vendor`: the
provider's kind and the content as the provider returned it.

| Block | Fields | Meaning |
|---|---|---|
| `TextBlock` | `text` | Text the model wrote, or the user sent. |
| `ReasoningBlock` | `text`, `redacted` | What the model thought. `redacted`: the vendor withheld it and `text` is empty. |
| `ToolUseBlock` | `id`, `name`, `input` | The model calling one of the caller's tools. `input` is a JSON object. |
| `ToolResultBlock` | `tool_use_id`, `content`, `is_error` | What a tool answered. In a user message. |
| `ServerToolUseBlock` | `id`, `name`, `input` | The vendor running one of its own tools, such as web search. |
| `ServerToolResultBlock` | `tool_use_id` | The vendor's tool answered; the vendor keeps what it said. |

A **tool** has a `name`, a `description` and an `input_schema`, a JSON Schema
object. The caller runs the tool and answers with a `ToolResultBlock`.

A **response** has the assistant `message`, a `stop_reason` and the `usage`.

| `stop_reason` | Meaning |
|---|---|
| `end_turn` | The answer is complete. |
| `tool_use` | The model waits for the results of the tool calls in the message. |
| `max_tokens` | The output limit cut the message short. |
| `pause_turn` | A vendor's tool loop paused; send the message back to continue it. |

**Usage** counts `input_tokens` (every input token, the cached ones included),
`output_tokens` (the reasoning included), `cached_input_tokens` (of the input,
those read from the vendor's cache) and `reasoning_tokens` (of the output,
those spent reasoning, when the vendor counts them; else 0).

## `complete`

Inputs: a `provider/model` id ([providers.md](providers.md)), `messages`, and
optionally `system`, `tools`, `web_search`, `effort` ([effort.md](effort.md)),
`max_tokens` and `timeout`. Output: a response.

## Invariants

- **MSG-1**: Before anything is sent, `messages` MUST be non-empty, `max_tokens`
  MUST be positive when given, and `effort` MUST be valid (EFFORT-1); otherwise
  `ValueError`.
- **MSG-2**: An assistant message is replayed to a provider of the kind that
  returned it as that provider returned it, `vendor.content` whole, so that
  signed reasoning, citations and the vendor's tool blocks are never lost
  ([providers.md](providers.md)). A provider of another kind receives the
  message's blocks; reasoning blocks and the vendor's tool blocks are left out,
  since they cannot be made valid for it.
- **MSG-3**: In a user message, tool results come before text. On an API where
  tool results are messages of their own, each becomes one, before the text.
- **MSG-4**: A message of one text block is sent as plain text where the API
  allows it, so that `complete` with one user message sends what `ask` sends.
- **MSG-5**: A refusal raises `RefusalError`. `max_tokens` is a stop reason, not
  an error: the message holds what was produced. `ask` raises instead
  (EFFORT-6).
- **MSG-6**: A stop reason the port does not know raises `ProviderError`
  rather than passing for an answer.
- **MSG-7**: Without `max_tokens`, the port sends what it sent before this
  specification: Anthropic 16,000, OpenRouter nothing.
- **MSG-8**: `ask` sends one user message, re-sends the turn on `pause_turn`,
  and returns the text after the last tool block.

## Mapping

| | Anthropic | OpenRouter |
|---|---|---|
| `system` | `system` | A first `system` message |
| `TextBlock` | `text` | `content`, a string or text parts |
| `ReasoningBlock` | `thinking`, `redacted_thinking` | `reasoning`; `reasoning_details` is replayed through `vendor` |
| `ToolUseBlock` | `tool_use` | `tool_calls[]` with `function.arguments` as JSON text |
| `ToolResultBlock` | `tool_result` in the user message | A `tool` message |
| `ServerToolUseBlock` | `server_tool_use`, `mcp_tool_use` | Not exposed |
| `ServerToolResultBlock` | `*_tool_result` | Not exposed |
| `Tool` | `{name, description, input_schema}` | `{type: function, function: {name, description, parameters}}` |
| `tool_use` | `tool_use` | `tool_calls`, or `stop` with `tool_calls` present |
| `max_tokens` | `max_tokens`, `model_context_window_exceeded` | `length` |
| `pause_turn` | `pause_turn` | Does not occur |
| Refusal | `refusal` | `content_filter`, `refusal` set, or a 403 |
| `input_tokens` | `input_tokens` + cache read + cache creation | `prompt_tokens` |
| `cached_input_tokens` | `cache_read_input_tokens` | `prompt_tokens_details.cached_tokens` |
| `reasoning_tokens` | 0 | `completion_tokens_details.reasoning_tokens` |

## Serialisation

Messages and responses have a JSON form of their own, so that callers can
store a conversation between calls. `to_dict` gives it and the class method
`from_dict` reads it back, on `Message` and on `Response`; each port spells
them in its language's way.

| Value | JSON |
|---|---|
| Message | `role`, `content`, a list of blocks, and `vendor`: `{"kind": ..., "content": ...}` or `null`. |
| Block | `type`, then the block's fields. |
| Response | `message`, `stop_reason`, and `usage` with its four counts. |

| `type` | Block |
|---|---|
| `text` | `TextBlock` |
| `reasoning` | `ReasoningBlock` |
| `tool_use` | `ToolUseBlock` |
| `tool_result` | `ToolResultBlock` |
| `server_tool_use` | `ServerToolUseBlock` |
| `server_tool_result` | `ServerToolResultBlock` |

- **MSG-9**: `to_dict` holds every field, defaults included, and JSON values
  alone: objects with string keys, arrays, strings, numbers, booleans and
  `null`. A tool's `input` and the vendor's content become plain objects and
  arrays, copied, so that changing the result never changes the message. A
  value JSON cannot hold raises `ValueError`.
- **MSG-10**: Each block carries its `type`, from the table above, so that
  blocks with the same fields, such as `ToolUseBlock` and `ServerToolUseBlock`,
  stay apart.
- **MSG-11**: `from_dict(x.to_dict())` equals `x` for every message and response
  made of JSON values. `vendor` comes back whole, so that a stored assistant
  message replays as it came (MSG-2).
- **MSG-12**: `from_dict` raises `ValueError` for anything outside this form: an
  unknown block type, role or stop reason; a missing field, a field of another
  JSON type, or a field the value does not have. A field with a default may be
  left out and takes it, so that a message written by hand needs no `vendor` or
  `is_error`.

## Non-goals

- Choosing the tool (`tool_choice`) or forbidding parallel calls.
- Citations as data; they travel in `vendor`.
- Images, documents and other non-text input.

## Open questions

- Whether `ServerToolUseBlock` should stream, so that a UI can show a vendor's
  tool at work.
