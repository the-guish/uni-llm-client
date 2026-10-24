# Language-agnostic tests

These tests check that every port sends the same vendor requests. Each port
ships a test server that exposes `ask`, `complete` and `stream` over HTTP. The
tests start a mock vendor, start the port's test server, and call it. The mock vendor records each
request it receives, and each test compares those requests with its case.

## Running

The tests need [uv](https://docs.astral.sh/uv/). Run every implementation, or
one with `--impl`:

    cd tests
    uv run pytest
    uv run pytest --impl python-async

`--live openrouter` runs the cases marked `"live": true` against the real
OpenRouter, with `openai/gpt-oss-20b`. It costs a fraction of a cent. The key
comes from `OPENROUTER_API_KEY`, in the environment or in `~/.env`:

    uv run pytest --live openrouter

In live mode, the mock vendor forwards each request to OpenRouter and puts the
real key in its `Authorization` header. The test servers keep `test-key`. A
live step checks that the vendor accepted every request. A step that sends back
no earlier reply also matches its vendor requests exactly. Its reply matches the
mocked one on what a real model must keep:

- The stop reason, and the names of the tools called.
- The keys of each tool's input. A text value contains the expected one.
- Some text, when the mocked reply has text.
- For a stream, the order of the events, and the pieces that add up to the
  response.

A live case prompts the model to do what it checks, such as calling a tool.

Each live run saves its exchanges in `tests/.live/<time>/<case>/<implementation>/`,
which git ignores. A file per step holds the arguments, each vendor request and
response, and the test server's reply. A saved response has the form of a vendor
response payload, so it can refresh a mocked one. The files hold no real key.

## Cases

Cases live in `cases/*.json`. Each case has a `name`, the test id, and `steps`,
which run in order. A step names payloads instead of holding them:

| Key | Meaning |
|---|---|
| `ask`, `complete` or `stream` | The call, and its arguments. |
| `vendor` | The vendor exchanges expected, in order. Each has a `request` and a `response`. |
| `reply` | The reply of the test server expected. |

A case marked `"live": true` also runs in live mode.

A step can send back the message an earlier step returned. In its arguments,
`{"$reply_message": N}` stands for the assistant message of step `N`'s reply,
as the implementation returned it.

Payloads live in `reusable_payloads/<party>/<request or response>/`, one JSON
file each. A case names a payload by its path, without `.json`. The parties are
`uni-llm-client` and one directory per vendor.

| Payload | Content |
|---|---|
| `uni-llm-client/request` | The arguments of `ask`: `model`, `question`, and optionally `web_search`, `effort`, `timeout`, `api_keys` and `providers`. |
| `uni-llm-client/response` | The reply of the test server, without error messages. |
| `<vendor>/request` | A vendor request: `method`, `url`, `headers` and `json`. |
| `<vendor>/response` | The `status`, optional `headers`, and the `body` or the `events` the mock vendor serves; or `disconnect`. |

A vendor request matches when its `method`, `url` and `json` are equal. Its
`headers` must be present with these values, and other headers are allowed.
JSON text under an `arguments` key is compared parsed, since ports format it
differently. An error matches on its type alone, since messages differ between
ports. Every case passes the API key `test-key` for each provider, unless its
arguments set `api_keys`.

A vendor response with `events` is a stream of server-sent events. Each event
has a `data`, a JSON value or text, and an optional `event` name. An event with
a `comment` is a comment line. A stream that stops early is cut short.

The mock vendor sends a response's `headers` besides `Content-Type` and
`Content-Length`. Errors that are retried carry `retry-after: 0` where a vendor
would send the header, so that the tests do not wait for them. A response
`{"disconnect": true}` closes the connection without an answer, as a network
fault would. A request that finds no response left gets a 500.

A new behaviour usually needs a new case. Reuse a payload when one fits.

## Test server protocol

Each port's test server lives in its own `tests/` directory and is listed in
`implementations.json`. The runner starts the `command` in `cwd`, relative to
the repository root, and appends `--vendor-url URL`.

1. The server listens on a free port of `127.0.0.1`. Its first line on stdout
   is `listening on http://127.0.0.1:PORT`.
2. The server sends every vendor request to `URL`. Each request keeps its
   method, path, query, headers and body. The `Host` header keeps the vendor's
   host, such as `api.anthropic.com`.
3. `POST /ask`, `POST /complete` and `POST /stream` take the arguments of the
   call as a JSON object. Each calls it with a new client created with the
   given `api_keys` and `providers`, and `max_retries` when given.
4. The server replies with status 200. A library error replies
   `{"error": {"type": "...", "message": "..."}}`.
5. `ask` replies `{"answer": "..."}`. `complete` replies
   `{"response": RESPONSE}`.
6. `stream` reads the whole stream and replies `{"events": [EVENT, ...]}`. An
   error during the stream replies the events received so far, plus `error`.
7. Any other failure replies with status 500. Its body helps debugging.

The runner removes every environment variable ending in `_API_KEY` before it
starts a server.

Error types are the same in every port:

| Type | Raised when |
|---|---|
| `invalid_argument` | An argument is invalid, such as the model id or the effort. |
| `authentication` | The API key is missing or rejected. |
| `rate_limit` | The vendor rate-limits the request. |
| `refusal` | The model declines to answer. |
| `incomplete_response` | The response contains no complete answer. |
| `transport` | The connection failed or timed out. |
| `provider` | Any other vendor error. |

## JSON form

The arguments and replies of `complete` and `stream` use the names of
[messages.md](../docs/specs/messages.md) and
[streaming.md](../docs/specs/streaming.md). Messages and responses take the
library's own JSON form, specified in
[Serialisation](../docs/specs/messages.md#serialisation) (MSG-9 to MSG-12), and
each test server converts them with its library. Replies hold every field,
defaults included. Arguments may leave out a field that has a default, and a
message of another form is an `invalid_argument` error.

| Value | JSON |
|---|---|
| Arguments | `model`, `messages`, and optionally `system`, `tools`, `web_search`, `effort`, `max_tokens`, `timeout`, `api_keys`, `providers` and `max_retries`. |
| Providers | By name: `kind`, and optionally `api_key` and `base_url` ([providers.md](../docs/specs/providers.md)). |
| Message | `role`, `content`, a list of blocks, and `vendor`: `{"kind": "...", "content": ...}` or `null`. |
| Block | `type` and the block's fields. The types are `text`, `reasoning`, `tool_use`, `tool_result`, `server_tool_use` and `server_tool_result`. |
| Tool | `name`, `description` and `input_schema`. |
| `RESPONSE` | `message`, `stop_reason`, and `usage` with its four counts. |
| `EVENT` | `type` and the event's fields. The types are `text_delta`, `reasoning_delta`, `tool_use_start`, `tool_use_delta`, `tool_use_end` with its `block`, and `completed` with its `response`. |
