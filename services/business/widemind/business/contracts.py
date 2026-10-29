"""Version 1 host contracts. Economy and Tools are injected, never implemented here.

Adapters MUST persist idempotency keys and reconcile uncertain external effects.
ToolGateway owns credential/capability enforcement, bounded spend, actual charges,
reservation settlement, and account identity verification. Notifications are hints,
never proof of payment or delivery. See docs/BUSINESS.md for the complete contract.
"""
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

JSON = dict[str, Any]


@dataclass(frozen=True)
class Outcome:
    state: Literal["succeeded", "failed", "unknown"]
    data: JSON = field(default_factory=dict)
    reason: str = ""


class Economy(Protocol):
    def reserve(self, *, run_id: str, job_id: str, action_id: str,
                maximum_micro_usd: int) -> Outcome: ...
    def payment_link(self, *, run_id: str, job_id: str, action_id: str,
                     amount_micro_usd: int, currency: str) -> Outcome: ...
    def payment(self, *, run_id: str, job_id: str) -> JSON:
        """Verified snapshot: run/job IDs, revision, mode, status, currency,
        receipts/refunds/fees/earned/operating_cost_micro_usd and costs_final.

        earned is owned by economy and must remain zero until verified fulfillment.
        Snapshots must distinguish test payments from live settlement.
        """
        ...
    def record_fulfillment(self, *, run_id: str, job_id: str, action_id: str,
                           delivery_reference: str) -> Outcome: ...
    def release(self, *, run_id: str, job_id: str, action_id: str,
                reservation_id: str) -> Outcome: ...
    def reconcile(self, *, action_id: str) -> Outcome:
        """Return a durable outcome, or unknown. Never repeat an uncertain action."""
        ...


class Tools(Protocol):
    def available(self, action: str) -> bool: ...
    def execute(self, *, run_id: str, job_id: str, action_id: str, action: str,
                arguments: JSON, reservation_id: str | None,
                maximum_micro_usd: int) -> Outcome: ...
    def reconcile(self, *, action_id: str) -> Outcome: ...


class Runtime(Protocol):
    def state(self, run_id: str) -> str:
        """active/waiting permit work; paused/stopped/exhausted/failed block it."""
        ...


class Clock(Protocol):
    def __call__(self) -> float: ...


@dataclass(frozen=True)
class Plan:
    action: str
    arguments: JSON
    maximum_micro_usd: int


class Service(Protocol):
    name: str
    capability: str
    def plan(self, scope: JSON, allowance: int) -> Plan: ...
    def deliverable(self, scope: JSON, result: JSON) -> JSON:
        """Validate agreed scope; return filename, media_type, content, checks."""
        ...
