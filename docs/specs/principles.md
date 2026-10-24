# Principles

Users should be able to instantiate the library client once, naming the
providers it reaches ([providers.md](providers.md)).

The library should provide a synchronous client and an asynchronous client.

The client should expose methods for sending requests to different LLM vendors,
such as Anthropic, OpenAI, Gemini, or others.

One method of the client should receive as inputs:

- a provider/model following the LiteLLM convention, e.g. `"openai/gpt-4o"`
- a plain-text question (`str`)
- `web_search` (`bool`, default `False`)
- `reasoning` (`str`, from an enum)

That method should return a `str`.

A second method, `complete`, should receive a conversation of messages, with
an optional system prompt and tools, and return the model's whole response:
its message, why it stopped, and the tokens used ([messages.md](messages.md)).
A third, `stream`, should receive the same and yield the response as it
arrives ([streaming.md](streaming.md)). `ask` is `complete` with one user
message.

The client should send a call again when it failed in a way that may pass on
another attempt, such as a rate limit or a lost connection, twice by default
([retries.md](retries.md)).

The library internals should have a single abstract base class (ABC) that can be
implemented for each provider.

Each provider's code should live in a sub-directory.
