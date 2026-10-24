# uni-llm-client

Another Python library to abstract LLM APIs behind a common signature

## Installation

```sh
pip install uni-llm-client
```

## Usage

One question, one answer:

```python
from uni_llm_client import Client

with Client() as client:  # reads ANTHROPIC_API_KEY, OPENROUTER_API_KEY
    print(client.ask("anthropic/claude-opus-5", "Capital of France?", effort="low"))
```

A call that fails with a rate limit, a vendor error (5xx), a timeout or a lost
connection is sent again, twice by default, after waiting as long as the vendor
asks, or from half a second up to 8 seconds. `Client(max_retries=5)` changes
the number of retries and `max_retries=0` turns them off. A connection failure
or a timeout raises `TransportError`, a `UniLLMError` like the vendor errors.

A conversation with tools, streamed:

```python
from contextlib import closing

from uni_llm_client import (
    Client,
    Completed,
    Message,
    StopReason,
    TextDelta,
    Tool,
    ToolResultBlock,
    ToolUseEnd,
)

weather = Tool(
    "get_weather",
    "Weather in a city",
    {
        "type": "object",
        "properties": {"city": {"type": "string"}},
        "required": ["city"],
    },
)
messages = [Message.user("Weather in Paris?")]
with Client() as client:
    while True:
        with closing(
            client.stream("anthropic/claude-opus-5", messages, tools=[weather])
        ) as events:
            for event in events:
                match event:
                    case TextDelta(text):
                        print(text, end="")
                    case ToolUseEnd(block):
                        print(f"[calling {block.name} with {block.input}]")
                    case Completed(response):
                        pass
        messages.append(response.message)
        if response.stop_reason is not StopReason.TOOL_USE:
            break
        messages.append(
            Message.tool_results(
                *(ToolResultBlock(call.id, "22C, sunny") for call in response.tool_uses)
            )
        )
```

`complete` takes the same arguments and returns the `Response` whole. The
asynchronous `AsyncClient` has the same methods. Its streams close with
`contextlib.aclosing`.

A provider of your own, such as a gateway speaking the Anthropic API, under a
name of your own, and a timeout for one call:

```python
from uni_llm_client import Client, ProviderConfig

gateway = ProviderConfig(
    "anthropic", api_key="...", base_url="https://llm.corp/anthropic"
)
with Client(providers={"gateway": gateway}) as client:
    print(client.ask("gateway/claude-opus-5", "Capital of France?", timeout=30))
```

## Contributing

See [CONTRIBUTING.md](https://github.com/open-shipyard/uni-llm-client/blob/main/CONTRIBUTING.md).

## License

Licensed under the [Apache License, Version 2.0](LICENSE).
