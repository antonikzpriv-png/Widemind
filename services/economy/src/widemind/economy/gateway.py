"""The only supported dispatch path for future billable runtime/tool adapters.

Adapters are trusted installed code, not supplied by the model. An adapter must
quote an enforceable upper bound and pass the identical limits to its provider.
This module supplies no inference provider and does not claim live tool execution.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Protocol

from .service import EconomyError, EconomyService, encode, money


@dataclass(frozen=True)
class UsageReceipt:
    external_charge_id: str
    actual_micro_usd: int | None
    usage: dict[str, Any] = field(default_factory=dict)
    outcome: str = 'succeeded'


class BoundedAdapter(Protocol):
    provider: str
    account: str
    mode: str

    def quote(self, limits: dict[str, int]) -> int:
        """Offline upper bound for the entire attempt, including input and retries.

        Reject unsupported limits; include provider rounding and all charged units.
        Returning a bound asserts that execute enforces these limits at the provider.
        Never put an internal retry behind a single attempt without including its cap.
        """
        ...

    def execute(self, *, limits: dict[str, int], idempotency_key: str) -> UsageReceipt:
        """Execute once with enforced limits; return authoritative usage/charge data.

        If final cost is unavailable return None/unknown. A failed paid call may
        return a known charge and outcome='failed'. The idempotency key is scoped
        to this one reservation, not to a new attempt after an uncertain outcome.
        """
        ...


class PaidGateway:
    def __init__(self, service: EconomyService, adapters: list[BoundedAdapter]):
        self.service = service
        self.adapters = {}
        for adapter in adapters:
            mode = getattr(adapter, 'mode', None)
            if mode not in ('test', 'live'):
                raise ValueError('Paid adapters must explicitly declare test or live mode')
            key = (adapter.provider, adapter.account, mode)
            if key in self.adapters:
                raise ValueError('Duplicate paid adapter registration')
            self.adapters[key] = adapter

    def execute(self, reservation_id: str) -> dict:
        row = self.service.reservation(reservation_id)
        mode = self.service.balance(row['run_id'])['mode']
        adapter = self.adapters.get((row['provider'], row['account'], mode))
        if adapter is None:
            raise EconomyError('adapter_unavailable', 'No trusted bounded adapter is registered', 503)
        if (adapter.provider, adapter.account, getattr(adapter, 'mode', None)) != (row['provider'], row['account'], mode):
            raise EconomyError('profile_mismatch', 'Paid adapter configuration changed after registration')
        # Quote before dispatch. Model-controlled limits are validated by trusted adapter code.
        limits = deepcopy(row['limits'])
        bound = money(adapter.quote(deepcopy(limits)), positive=True)
        if bound > row['amount_micro_usd']:
            raise EconomyError('under_reserved', 'Reservation is below the adapter-enforced upper bound')
        self.service.start_reservation(reservation_id)
        try:
            receipt = adapter.execute(limits=limits, idempotency_key=reservation_id)
            if not isinstance(receipt, UsageReceipt):
                raise TypeError('Adapter returned no authoritative usage receipt')
            # Optional provider diagnostics must never suppress a known liability.
            # Leave room for the journal/event envelope around the usage object.
            try:
                usage = receipt.usage
                encoded_usage = encode(usage)
                if not isinstance(usage, dict) or len(encoded_usage.encode()) > 16_384:
                    raise ValueError('Oversized or non-object usage')
            except (EconomyError, ValueError):
                usage = {'usage_omitted': True, 'reason': 'invalid_or_oversized_provider_diagnostics'}
            return self.service.settle(reservation_id, receipt.actual_micro_usd, row['provider'], row['account'],
                                       receipt.external_charge_id, usage=usage, outcome=receipt.outcome)
        except BaseException:
            # Dispatch was durable before the call. Timeout/cancellation/bad response is
            # not evidence of free execution; preserve authorization for reconciliation.
            current = self.service.reservation(reservation_id)
            if current['state'] != 'settled':
                self.service.settle(reservation_id, None, row['provider'], row['account'], reservation_id,
                                    usage={'reconciliation_required': True}, outcome='unknown')
            raise
