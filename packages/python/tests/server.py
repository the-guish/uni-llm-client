# Copyright 2026 The uni-llm-client Authors
# SPDX-License-Identifier: Apache-2.0

"""Serves the client over HTTP for the language-agnostic tests in the repository root.

Run ``python tests/server.py --vendor-url URL [--client sync|async]``. Every
vendor request goes to `URL`, keeping its path and ``Host`` header. The protocol,
and the JSON form of messages and events, is in ``tests/README.md`` at the
repository root.
"""

import argparse
import asyncio
import dataclasses
import json
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import httpx2

from uni_llm_client import (
    AsyncClient,
    AuthenticationError,
    Client,
    Completed,
    ContentBlock,
    IncompleteResponseError,
    Message,
    ProviderConfig,
    ProviderError,
    RateLimitError,
    ReasoningBlock,
    ReasoningDelta,
    RefusalError,
    Response,
    ServerToolResultBlock,
    ServerToolUseBlock,
    StreamEvent,
    TextBlock,
    TextDelta,
    Tool,
    ToolResultBlock,
    ToolUseBlock,
    ToolUseDelta,
    ToolUseEnd,
    ToolUseStart,
    TransportError,
    VendorContent,
)
from uni_llm_client._client import DEFAULT_TIMEOUT

CALLS = ("ask", "complete", "stream")

BLOCK_TYPES: dict[str, type[ContentBlock]] = {
    "text": TextBlock,
    "reasoning": ReasoningBlock,
    "tool_use": ToolUseBlock,
    "tool_result": ToolResultBlock,
    "server_tool_use": ServerToolUseBlock,
    "server_tool_result": ServerToolResultBlock,
}
BLOCK_NAMES = {cls: name for name, cls in BLOCK_TYPES.items()}

EVENT_NAMES: dict[type[StreamEvent], str] = {
    TextDelta: "text_delta",
    ReasoningDelta: "reasoning_delta",
    ToolUseStart: "tool_use_start",
    ToolUseDelta: "tool_use_delta",
    ToolUseEnd: "tool_use_end",
    Completed: "completed",
}

# Most specific first: the subclasses of ProviderError come before it.
ERROR_TYPES: list[tuple[type[Exception], str]] = [
    (ValueError, "invalid_argument"),
    (AuthenticationError, "authentication"),
    (RateLimitError, "rate_limit"),
    (RefusalError, "refusal"),
    (IncompleteResponseError, "incomplete_response"),
    (TransportError, "transport"),
    (ProviderError, "provider"),
]


def redirect(request: httpx2.Request, vendor_url: httpx2.URL) -> None:
    request.url = request.url.copy_with(
        scheme=vendor_url.scheme, host=vendor_url.host, port=vendor_url.port
    )


class RedirectTransport(httpx2.HTTPTransport):
    def __init__(self, vendor_url: httpx2.URL) -> None:
        super().__init__()
        self.vendor_url = vendor_url

    def handle_request(self, request: httpx2.Request) -> httpx2.Response:
        redirect(request, self.vendor_url)
        return super().handle_request(request)


class AsyncRedirectTransport(httpx2.AsyncHTTPTransport):
    def __init__(self, vendor_url: httpx2.URL) -> None:
        super().__init__()
        self.vendor_url = vendor_url

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        redirect(request, self.vendor_url)
        return await super().handle_async_request(request)


def message_of(data: dict[str, Any]) -> Message:
    content = tuple(
        BLOCK_TYPES[block["type"]](**{k: v for k, v in block.items() if k != "type"})
        for block in data["content"]
    )
    vendor = data.get("vendor")
    if vendor is not None:
        return Message(
            data["role"], content, VendorContent(vendor["kind"], vendor["content"])
        )
    return Message(data["role"], content)


def block_json(block: ContentBlock) -> dict[str, Any]:
    return {"type": BLOCK_NAMES[type(block)], **dataclasses.asdict(block)}


def message_json(message: Message) -> dict[str, Any]:
    vendor = message.vendor
    return {
        "role": message.role,
        "content": [block_json(block) for block in message.content],
        "vendor": vendor and {"kind": vendor.kind, "content": vendor.content},
    }


def response_json(response: Response) -> dict[str, Any]:
    return {
        "message": message_json(response.message),
        "stop_reason": response.stop_reason.value,
        "usage": dataclasses.asdict(response.usage),
    }


def event_json(event: StreamEvent) -> dict[str, Any]:
    name = EVENT_NAMES[type(event)]
    if isinstance(event, Completed):
        return {"type": name, "response": response_json(event.response)}
    if isinstance(event, ToolUseEnd):
        return {"type": name, "block": block_json(event.block)}
    return {"type": name, **dataclasses.asdict(event)}


def ask_kwargs(args: dict[str, Any]) -> dict[str, Any]:
    return {
        "model": args["model"],
        "question": args["question"],
        "web_search": args.get("web_search", False),
        "effort": args.get("effort"),
        "timeout": args.get("timeout"),
    }


def client_kwargs(args: dict[str, Any]) -> dict[str, Any]:
    """The arguments of the client: `api_keys`, `providers`, and `max_retries`
    when given."""
    providers = args.get("providers") or {}
    kwargs: dict[str, Any] = {
        "api_keys": args.get("api_keys"),
        "providers": {
            name: ProviderConfig(**config) for name, config in providers.items()
        },
    }
    if "max_retries" in args:
        kwargs["max_retries"] = args["max_retries"]
    return kwargs


def model_kwargs(args: dict[str, Any]) -> dict[str, Any]:
    """The arguments of `complete` and `stream`."""
    return {
        "model": args["model"],
        "messages": [message_of(message) for message in args["messages"]],
        "system": args.get("system"),
        "tools": [Tool(**tool) for tool in args.get("tools", [])],
        "web_search": args.get("web_search", False),
        "effort": args.get("effort"),
        "max_tokens": args.get("max_tokens"),
        "timeout": args.get("timeout"),
    }


def error_reply(error: Exception) -> dict[str, Any]:
    """The reply for a library error; any other error is re-raised."""
    for cls, name in ERROR_TYPES:
        if isinstance(error, cls):
            return {"error": {"type": name, "message": str(error)}}
    raise error


def call_sync(vendor_url: httpx2.URL, call: str, args: dict[str, Any]) -> Any:
    # trust_env=False: a proxy from the environment would bypass the transport.
    http_client = httpx2.Client(
        transport=RedirectTransport(vendor_url),
        timeout=DEFAULT_TIMEOUT,
        trust_env=False,
    )
    try:
        client = Client(http_client=http_client, **client_kwargs(args))
    except Exception as error:
        # A refused configuration (PROV-3, RETRY-1) is a library error like any other.
        http_client.close()
        return error_reply(error)
    with client:
        try:
            if call == "ask":
                return {"answer": client.ask(**ask_kwargs(args))}
            if call == "complete":
                return {
                    "response": response_json(client.complete(**model_kwargs(args)))
                }
        except Exception as error:
            return error_reply(error)
        events: list[dict[str, Any]] = []
        try:
            for event in client.stream(**model_kwargs(args)):
                events.append(event_json(event))
        except Exception as error:
            return {"events": events, **error_reply(error)}
        return {"events": events}


def call_async(vendor_url: httpx2.URL, call: str, args: dict[str, Any]) -> Any:
    async def run() -> Any:
        http_client = httpx2.AsyncClient(
            transport=AsyncRedirectTransport(vendor_url),
            timeout=DEFAULT_TIMEOUT,
            trust_env=False,
        )
        try:
            client = AsyncClient(http_client=http_client, **client_kwargs(args))
        except Exception as error:
            await http_client.aclose()
            return error_reply(error)
        async with client:
            try:
                if call == "ask":
                    return {"answer": await client.ask(**ask_kwargs(args))}
                if call == "complete":
                    response = await client.complete(**model_kwargs(args))
                    return {"response": response_json(response)}
            except Exception as error:
                return error_reply(error)
            events: list[dict[str, Any]] = []
            try:
                async for event in client.stream(**model_kwargs(args)):
                    events.append(event_json(event))
            except Exception as error:
                return {"events": events, **error_reply(error)}
            return {"events": events}

    return asyncio.run(run())


def make_handler(vendor_url: httpx2.URL, client: str) -> type[BaseHTTPRequestHandler]:
    call_client = call_sync if client == "sync" else call_async

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            call = self.path.removeprefix("/")
            if call not in CALLS:
                self.send_error(404)
                return
            length = int(self.headers.get("Content-Length", 0))
            args = json.loads(self.rfile.read(length))
            status = 200
            try:
                reply = call_client(vendor_url, call, args)
            except Exception:
                # A bug in this server or the library: show it to the runner.
                status, reply = 500, {"traceback": traceback.format_exc()}
            body = json.dumps(reply).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:
            pass

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vendor-url", required=True)
    parser.add_argument("--client", choices=["sync", "async"], default="sync")
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()

    handler = make_handler(httpx2.URL(args.vendor_url), args.client)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    host, port = server.server_address[:2]
    print(f"listening on http://{host!s}:{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
