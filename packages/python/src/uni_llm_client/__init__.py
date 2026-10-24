# Copyright 2026 The uni-llm-client Authors
# SPDX-License-Identifier: Apache-2.0

"""Abstract LLM APIs behind a common signature."""

from importlib.metadata import PackageNotFoundError, version

from uni_llm_client._async_client import AsyncClient
from uni_llm_client._client import Client
from uni_llm_client._config import ProviderConfig
from uni_llm_client._effort import Effort
from uni_llm_client._errors import (
    AuthenticationError,
    IncompleteResponseError,
    ProviderError,
    RateLimitError,
    RefusalError,
    TransportError,
    UniLLMError,
)
from uni_llm_client._events import (
    Completed,
    ReasoningDelta,
    StreamEvent,
    TextDelta,
    ToolUseDelta,
    ToolUseEnd,
    ToolUseStart,
)
from uni_llm_client._messages import (
    ContentBlock,
    Message,
    ReasoningBlock,
    Response,
    Role,
    ServerToolResultBlock,
    ServerToolUseBlock,
    StopReason,
    TextBlock,
    Tool,
    ToolResultBlock,
    ToolUseBlock,
    Usage,
    VendorContent,
)

try:
    __version__ = version("uni-llm-client")
except PackageNotFoundError:  # a source checkout on sys.path, not installed
    __version__ = "0+unknown"

__all__ = [
    "AsyncClient",
    "AuthenticationError",
    "Client",
    "Completed",
    "ContentBlock",
    "Effort",
    "IncompleteResponseError",
    "Message",
    "ProviderConfig",
    "ProviderError",
    "RateLimitError",
    "ReasoningBlock",
    "ReasoningDelta",
    "RefusalError",
    "Response",
    "Role",
    "ServerToolResultBlock",
    "ServerToolUseBlock",
    "StopReason",
    "StreamEvent",
    "TextBlock",
    "TextDelta",
    "Tool",
    "ToolResultBlock",
    "ToolUseBlock",
    "ToolUseDelta",
    "ToolUseEnd",
    "ToolUseStart",
    "TransportError",
    "UniLLMError",
    "Usage",
    "VendorContent",
    "__version__",
]
