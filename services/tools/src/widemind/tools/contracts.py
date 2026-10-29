"""Version-one adapter contracts shared by the gateway and provider modules.

Money is integer micro-USD. Tool outputs are private, untrusted task data.
Only owner-loaded configuration supplies capabilities, prices and accounts.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol


class ToolError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False, usage: Usage | None = None):
        super().__init__(message)
        self.code, self.message, self.retryable = code, message, retryable
        self.usage = usage


class Unavailable(ToolError):
    def __init__(self, message: str):
        super().__init__("unavailable", message)


class Uncertain(ToolError):
    def __init__(self, message: str, *, usage: Usage | None = None):
        super().__init__("uncertain", message)
        self.usage = usage


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    effect: str = "read"  # read, local, external; adapters enforce operation details
    version: int = 1


@dataclass(frozen=True)
class Quote:
    maximum_microusd: int = 0
    provider: str = "local"
    paid: bool = False
    limits: Mapping[str, Any] = field(default_factory=dict)
    currency: str = "USD"


@dataclass(frozen=True)
class Usage:
    cost_microusd: int | None = 0  # null means unknown liability, never free
    units: Mapping[str, int] = field(default_factory=dict)
    provider_request_id: str | None = None
    provisional: bool = False  # measured units priced by configured tariff, not invoice
    details: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolResult:
    data: Mapping[str, Any]
    usage: Usage = field(default_factory=Usage)
    artifacts: list[dict] = field(default_factory=list)
    references: list[dict] = field(default_factory=list)
    trust: str = "untrusted"


@dataclass(frozen=True)
class Reconciliation:
    outcome: str  # completed, not_executed, uncertain
    result: ToolResult | None = None
    detail: str = ""


@dataclass(frozen=True)
class HTTPResponse:
    status: int
    headers: Mapping[str, str]  # transport normalizes names to lowercase
    body: bytes
    url: str


class Transport(Protocol):
    def request(self, method: str, url: str, *, headers: Mapping[str, str] | None = None,
                body: bytes | None = None, timeout: float = 30,
                max_bytes: int = 2_000_000,
                cancel: Callable[[], None] | None = None,
                public_only: bool = False) -> HTTPResponse: ...


class Secrets(Protocol):
    def get(self, name: str) -> str | None: ...
    def set(self, name: str, value: str) -> None: ...


class Workspace(Protocol):
    root: Any  # pathlib.Path; never contains control DB, credentials or host files

    def read_bytes(self, path: str, *, max_bytes: int = 8_000_000) -> bytes: ...
    def write_bytes(self, path: str, data: bytes, *, overwrite: bool = False) -> dict: ...
    def list_files(self, path: str = "", *, limit: int = 100) -> list[dict]: ...
    def delete(self, path: str) -> dict: ...


class ActionState(Protocol):
    def load(self, action_id: str) -> dict: ...
    def update(self, action_id: str, patch: Mapping[str, Any]) -> None: ...
    def dispatched(self, action_id: str, intent: Mapping[str, Any]) -> None: ...


@dataclass
class ToolContext:
    action_id: str
    run_id: str
    reservation_id: str
    config: Mapping[str, Any]  # owner-loaded config for this adapter only
    policy: Mapping[str, Any]  # owner-loaded runtime profile, not tool inputs
    workspace: Workspace
    secrets: Secrets
    transport: Transport
    state: ActionState
    deadline: float
    authorize: Callable[[], None]
    cancelled: Callable[[], bool] = lambda: False
    job_id: str | None = None
    child_executor: Any = None  # trusted integrated workflow capability, never tool input

    def check(self) -> None:
        if self.cancelled():
            raise ToolError("cancelled", "Action cancelled by its operator.")
        if time.monotonic() >= self.deadline:
            raise ToolError("timeout", "The action exceeded its execution deadline.")
        self.authorize()

    def remaining(self, maximum: float = 30) -> float:
        self.check()
        return max(.001, min(maximum, self.deadline - time.monotonic()))

    def load_state(self) -> dict:
        return self.state.load(self.action_id)

    def save_state(self, patch: Mapping[str, Any]) -> None:
        self.state.update(self.action_id, patch)

    def mark_dispatched(self, intent: Mapping[str, Any]) -> None:
        """Durably persist intent BEFORE any potentially effective provider call."""
        self.check()
        self.state.dispatched(self.action_id, intent)


class Adapter(Protocol):
    spec: ToolSpec

    def quote(self, arguments: Mapping[str, Any], config: Mapping[str, Any]) -> Quote: ...
    def availability(self, config: Mapping[str, Any], secrets: Secrets) -> dict: ...
    def execute(self, arguments: Mapping[str, Any], ctx: ToolContext) -> ToolResult: ...
    def reconcile(self, arguments: Mapping[str, Any], ctx: ToolContext) -> Reconciliation: ...
