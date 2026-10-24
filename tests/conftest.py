# Copyright 2026 The uni-llm-client Authors
# SPDX-License-Identifier: Apache-2.0

"""Runs every case against every implementation listed in implementations.json.

The mock vendor records each request it receives and serves the responses a case
gives it. Each implementation's test server sends its vendor requests there. With
``--live openrouter``, it forwards them to OpenRouter instead, with the real key.
"""

import json
import os
import re
import select
import subprocess
import threading
import time
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

HERE = Path(__file__).parent
REPO = HERE.parent
IMPLEMENTATIONS: dict[str, dict[str, Any]] = json.loads(
    (HERE / "implementations.json").read_text()
)
STARTUP_TIMEOUT = 120.0  # The first `uv run` may install the environment.
LIVE_HOST = "openrouter.ai"
LIVE_KEY_ENV = "OPENROUTER_API_KEY"
LIVE_TIMEOUT = 120.0
# Each live run saves what the vendor answered here, one folder per run.
LIVE_RECORDINGS = HERE / ".live"

# Calls to 127.0.0.1 must not go through a proxy from the environment.
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


@dataclass
class VendorCall:
    method: str
    url: str
    headers: dict[str, str]
    json: Any
    status: int = 0
    """The status the vendor answered with."""
    error_body: str = ""
    """The body of a live error response, to show in failures."""
    response: dict[str, Any] | None = None
    """What the live vendor answered, in the form of a vendor response payload."""


class MockVendor:
    """Records the requests it receives and serves the responses it is given.

    With a `live_key`, it forwards each request to the vendor instead, with that
    key in place of the one the implementation sent.
    """

    def __init__(self, live_key: str | None = None) -> None:
        self.live_key = live_key
        self.recordings = LIVE_RECORDINGS / time.strftime("%Y%m%d-%H%M%S")
        self.calls: list[VendorCall] = []
        self._responses: list[dict[str, Any]] = []
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host!s}:{port}"

    def serve(self, responses: list[dict[str, Any]]) -> None:
        self.calls.clear()
        self._responses = list(responses)

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        vendor = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                headers = {k.lower(): v for k, v in self.headers.items()}
                recorded = VendorCall(
                    method=self.command,
                    # Implementations keep the vendor's Host header and path.
                    url=f"https://{headers.get('host')}{self.path}",
                    headers=headers,
                    json=json.loads(body) if body else None,
                )
                vendor.calls.append(recorded)
                extra_headers: dict[str, str] = {}
                if vendor.live_key is not None:
                    status, content_type, reply = vendor.forward(recorded, body)
                    recorded.response = response_payload(status, content_type, reply)
                    if status >= 400:
                        recorded.error_body = reply.decode(errors="replace")
                else:
                    mocked = vendor.mocked()
                    if mocked is None:
                        # Close the connection without a response, as a network fault.
                        self.close_connection = True
                        return
                    status, content_type, reply, extra_headers = mocked
                recorded.status = status
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(reply)))
                for name, value in extra_headers.items():
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(reply)

            def log_message(self, format: str, *args: Any) -> None:
                pass

        return Handler

    def mocked(self) -> tuple[int, str, bytes, dict[str, str]] | None:
        """The next response to serve: its status, content type, body and extra
        headers; or None for a response that drops the connection."""
        if self._responses:
            response = self._responses.pop(0)
        else:
            response = {"status": 500, "body": {"error": "no response left"}}
        if response.get("disconnect"):
            return None
        headers: dict[str, str] = response.get("headers", {})
        if "events" in response:
            return (
                response["status"],
                "text/event-stream",
                sse(response["events"]),
                headers,
            )
        body = json.dumps(response["body"]).encode()
        return response["status"], "application/json", body, headers

    def forward(self, recorded: VendorCall, body: bytes) -> tuple[int, str, bytes]:
        if not recorded.url.startswith(f"https://{LIVE_HOST}/"):
            error = {"error": f"live mode forwards only to {LIVE_HOST}"}
            return 502, "application/json", json.dumps(error).encode()
        request = urllib.request.Request(
            recorded.url,
            data=body,
            method=recorded.method,
            headers={
                "Content-Type": recorded.headers.get("content-type", ""),
                "Authorization": f"Bearer {self.live_key}",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=LIVE_TIMEOUT) as response:
                content_type = response.headers.get("Content-Type", "")
                return response.status, content_type, response.read()
        except urllib.error.HTTPError as error:
            content_type = error.headers.get("Content-Type", "")
            return error.code, content_type, error.read()


def response_payload(status: int, content_type: str, body: bytes) -> dict[str, Any]:
    """A vendor response payload, as `MockVendor.mocked` serves it."""
    text = body.decode(errors="replace")
    if content_type.startswith("text/event-stream"):
        return {"status": status, "events": sse_events(text)}
    try:
        return {"status": status, "body": json.loads(text)}
    except ValueError:
        return {"status": status, "body": text}


def sse_events(text: str) -> list[dict[str, Any]]:
    """The events of a server-sent event stream, as `sse` writes them."""
    events: list[dict[str, Any]] = []
    for frame in text.replace("\r\n", "\n").split("\n\n"):
        event: dict[str, Any] = {}
        data = []
        for line in frame.splitlines():
            name, _, value = line.partition(":")
            value = value.removeprefix(" ")
            if name == "":
                events.append({"comment": value})
            elif name == "event":
                event["event"] = value
            elif name == "data":
                data.append(value)
        if data:
            joined = "\n".join(data)
            try:
                event["data"] = json.loads(joined)
            except ValueError:
                event["data"] = joined
            events.append(event)
    return events


def live_key() -> str:
    """The OpenRouter key, from the environment or ``~/.env``. Never printed."""
    key = os.environ.get(LIVE_KEY_ENV, "")
    env_file = Path.home() / ".env"
    if not key and env_file.exists():
        for line in env_file.read_text().splitlines():
            name, _, value = line.removeprefix("export ").partition("=")
            if name.strip() == LIVE_KEY_ENV:
                key = value.strip().strip("'\"")
    if not key:
        raise pytest.UsageError(
            f"--live needs {LIVE_KEY_ENV}, in the environment or ~/.env"
        )
    return key


def sse(events: list[dict[str, Any]]) -> bytes:
    """Server-sent events: each has a `data`, an optional `event`, or a `comment`."""
    frames = []
    for event in events:
        if "comment" in event:
            frames.append(f": {event['comment']}\n\n")
            continue
        data = event["data"]
        text = data if isinstance(data, str) else json.dumps(data)
        name = f"event: {event['event']}\n" if "event" in event else ""
        frames.append(f"{name}data: {text}\n\n")
    return "".join(frames).encode()


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--impl",
        action="append",
        choices=sorted(IMPLEMENTATIONS),
        help="Implementation to test; repeat for several. Default: all.",
    )
    parser.addoption(
        "--package",
        action="append",
        choices=sorted({spec["package"] for spec in IMPLEMENTATIONS.values()}),
        help="Test every implementation of a package; repeat for several.",
    )
    parser.addoption(
        "--live",
        choices=["openrouter"],
        help="Run the cases marked live against the real vendor. Costs money.",
    )


RECORDINGS_KEY = pytest.StashKey[Path]()


def pytest_terminal_summary(
    terminalreporter: pytest.TerminalReporter, config: pytest.Config
) -> None:
    recordings = config.stash.get(RECORDINGS_KEY, None)
    if recordings is not None:
        terminalreporter.write_line(f"live exchanges saved in {recordings}")


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    if not config.getoption("live"):
        return
    selected: list[pytest.Item] = []
    deselected: list[pytest.Item] = []
    for item in items:
        callspec = getattr(item, "callspec", None)
        case = callspec.params.get("case") if callspec else None
        (selected if case and case.get("live") else deselected).append(item)
    config.hook.pytest_deselected(items=deselected)
    items[:] = selected


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    if "implementation" in metafunc.fixturenames:
        names = metafunc.config.getoption("impl") or []
        for package in metafunc.config.getoption("package") or []:
            names += [n for n, s in IMPLEMENTATIONS.items() if s["package"] == package]
        names = sorted(set(names)) or sorted(IMPLEMENTATIONS)
        metafunc.parametrize("implementation", names, indirect=True, scope="session")


@pytest.fixture(scope="session")
def mock_vendor(request: pytest.FixtureRequest) -> Iterator[MockVendor]:
    live = request.config.getoption("live")
    vendor = MockVendor(live_key() if live else None)
    yield vendor
    vendor.close()
    if live and vendor.recordings.exists():
        request.config.stash[RECORDINGS_KEY] = vendor.recordings


@pytest.fixture
def vendor(mock_vendor: MockVendor) -> MockVendor:
    mock_vendor.serve([])
    return mock_vendor


@pytest.fixture(scope="session")
def implementation(
    request: pytest.FixtureRequest, mock_vendor: MockVendor
) -> Iterator[str]:
    """Start the implementation's test server and return its URL."""
    spec = IMPLEMENTATIONS[request.param]
    # Real API keys stay out of reach: the cases pass the keys they need.
    env = {k: v for k, v in os.environ.items() if not k.endswith("_API_KEY")}
    process = subprocess.Popen(
        [*spec["command"], "--vendor-url", mock_vendor.url],
        cwd=REPO / spec["cwd"],
        env=env,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None
    try:
        ready, _, _ = select.select([process.stdout], [], [], STARTUP_TIMEOUT)
        line = process.stdout.readline() if ready else ""
        match = re.fullmatch(r"listening on (\S+)\n", line)
        if not match:
            pytest.fail(f"{request.param} did not start; first line: {line!r}")
        yield match[1]
    finally:
        process.terminate()
        process.wait(timeout=10)


def call(server_url: str, name: str, args: dict[str, Any]) -> dict[str, Any]:
    """POST `args` to the test server's `/name` and return its reply."""
    request = urllib.request.Request(
        f"{server_url}/{name}",
        data=json.dumps(args).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with _opener.open(request) as response:
            reply: dict[str, Any] = json.load(response)
    except urllib.error.HTTPError as error:
        pytest.fail(f"The test server failed:\n{error.read().decode()}")
    return reply
