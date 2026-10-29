"""Exact aggregate boundaries using synthetic provider records, never real payments."""
from __future__ import annotations

import sqlite3

import pytest

from widemind.economy.reporting import audit_integrity, public_summary
from widemind.economy.service import EconomyError, EconomyService, MAX_MONEY


@pytest.fixture
def service(tmp_path):
    instance = EconomyService(tmp_path / 'accounting.sqlite3')
    instance.create_run('run')
    return instance


def register(service, payment):
    service.register_payment('run', 'fixture-provider', 'fixture-account', payment, 'job', 'register-' + payment)


def transaction(identifier, amount, *, kind='receipt'):
    return {'transaction_id': identifier, 'kind': kind, 'amount_micro_usd': amount,
            'fee_micro_usd': 0, 'net_micro_usd': amount, 'currency': 'USD',
            'settled': True, 'native': {'fixture': True}}


def apply(service, payment, transactions, event):
    current = service.payment('fixture-provider', 'fixture-account', 'test', payment)
    return service.apply_payment_snapshot({
        'provider': 'fixture-provider', 'account': 'fixture-account', 'mode': 'test',
        'payment_id': payment, 'run_id': 'run', 'job_id': 'job', 'status': 'settled',
        'expected_revision': current['revision'], 'transactions': transactions,
    }, event_id=event, event_digest='fixture-digest-' + event)


def dump(service):
    with sqlite3.connect(service.path) as connection:
        return '\n'.join(connection.iterdump())


def assert_projection_limit(error):
    assert error.value.code == 'payment_projection_limit'
    assert error.value.status == 422


def test_aggregate_hold_overflow_rolls_back_credit_components_and_event_identity(service):
    register(service, 'payment')
    original = dump(service)
    receipts = [transaction('receipt-max', MAX_MONEY), transaction('receipt-extra', 1)]
    with pytest.raises(EconomyError) as error:
        apply(service, 'payment', receipts, 'unconsumed-event')
    assert_projection_limit(error)
    assert dump(service) == original
    assert service.balance('run')['posted_micro_usd'] == 5_000_000
    assert service.payment('fixture-provider', 'fixture-account', 'test', 'payment')['revision'] == 0

    # Rejection consumed neither the event identity nor a partial receipt. An
    # admissible authoritative snapshot can subsequently use that same event.
    accepted = apply(service, 'payment', receipts[:1], 'unconsumed-event')
    assert accepted['duplicate'] is False
    assert service.balance('run')['held_micro_usd'] == MAX_MONEY
    after = dump(service)
    assert apply(service, 'payment', receipts[:1], 'unconsumed-event')['duplicate'] is True
    assert dump(service) == after
    assert audit_integrity(service, 'run')['ok'] is True


def test_cumulative_release_overflow_preserves_committed_credit_and_hold(service):
    register(service, 'payment')
    receipt = transaction('receipt', MAX_MONEY)
    apply(service, 'payment', [receipt], 'receipt-event')
    released = service.release_payment_hold('fixture-provider', 'fixture-account', 'test', 'payment', 'first-release')
    assert released['released_micro_usd'] == MAX_MONEY

    # The new hold fits, even though cumulative release would not. Preserve the
    # real extra credit and its hold; reject only the unsupported release.
    adjustment = transaction('adjustment', 1, kind='adjustment')
    apply(service, 'payment', [receipt, adjustment], 'adjustment-event')
    assert service.balance('run')['held_micro_usd'] == 1
    assert service.balance('run')['posted_micro_usd'] == 5_000_000 + MAX_MONEY + 1
    original = dump(service)
    with pytest.raises(EconomyError) as error:
        service.release_payment_hold('fixture-provider', 'fixture-account', 'test', 'payment', 'overflow-release')
    assert_projection_limit(error)
    assert dump(service) == original
    assert service.balance('run')['held_micro_usd'] == 1
    assert audit_integrity(service, 'run')['ok'] is True


def test_wallet_totals_above_int64_remain_exact_across_distinct_payments(service):
    for payment in ('first', 'second'):
        register(service, payment)
        apply(service, payment, [transaction('receipt-' + payment, MAX_MONEY)], 'event-' + payment)
    summary = public_summary(service, 'run')
    assert summary['posted_micro_usd'] == 5_000_000 + 2 * MAX_MONEY
    assert summary['held_micro_usd'] == 2 * MAX_MONEY
    assert summary['available_micro_usd'] == 5_000_000
    for payment in ('first', 'second'):
        released = service.release_payment_hold('fixture-provider', 'fixture-account', 'test', payment, 'fulfill-' + payment)
        assert released['released_micro_usd'] == MAX_MONEY
        before_replay = dump(service)
        assert service.release_payment_hold('fixture-provider', 'fixture-account', 'test', payment, 'fulfill-' + payment) == released
        assert dump(service) == before_replay
    assert service.balance('run')['available_micro_usd'] == 5_000_000 + 2 * MAX_MONEY
    assert audit_integrity(service, 'run')['ok'] is True
