"""Synthetic adapter receipts; these tests never make real paid calls."""
import pytest

from widemind.economy.gateway import PaidGateway, UsageReceipt
from widemind.economy.service import EconomyError, EconomyService


class FixtureAdapter:
    provider, account = 'fixture', 'sandbox'

    def __init__(self, receipt=None, error=None, bound=1_000_000):
        self.receipt, self.error, self.bound = receipt, error, bound
        self.calls = []

    def quote(self, limits):
        assert limits == {'max_tokens': 100}
        return self.bound

    def execute(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.receipt


@pytest.fixture
def service(tmp_path):
    svc = EconomyService(tmp_path/'economy.sqlite3')
    svc.create_run('run')
    return svc


def reserve(service):
    return service.reserve('run', 'action', 1_000_000, 'key', 'fixture', 'sandbox', {'max_tokens': 100})['reservation_id']


def test_provider_receipt_and_limits_pass_through_once(service):
    rid = reserve(service)
    adapter = FixtureAdapter(UsageReceipt('fixture-charge', 234_567, {'input_tokens': 20, 'output_tokens': 37}))
    gateway = PaidGateway(service, [adapter])
    assert gateway.execute(rid)['state'] == 'settled'
    assert service.balance('run')['available_micro_usd'] == 4_765_433
    assert adapter.calls == [{'limits': {'max_tokens': 100}, 'idempotency_key': rid}]
    with pytest.raises(EconomyError, match='dispatched'):
        gateway.execute(rid)
    assert len(adapter.calls) == 1


def test_underquote_does_not_dispatch(service):
    rid = reserve(service)
    adapter = FixtureAdapter(bound=1_000_001)
    with pytest.raises(EconomyError, match='upper bound'):
        PaidGateway(service, [adapter]).execute(rid)
    assert not adapter.calls
    assert service.reservation(rid)['state'] == 'reserved'


@pytest.mark.parametrize('error', [TimeoutError(), RuntimeError(), KeyboardInterrupt()])
def test_unknown_call_keeps_reservation_across_restart(service, error):
    rid = reserve(service)
    adapter = FixtureAdapter(error=error)
    with pytest.raises(type(error)):
        PaidGateway(service, [adapter]).execute(rid)
    restarted = EconomyService(service.path)
    assert restarted.reservation(rid)['state'] == 'unknown'
    assert restarted.balance('run')['reserved_micro_usd'] == 1_000_000
    with pytest.raises(EconomyError):
        PaidGateway(restarted, [adapter]).execute(rid)
    assert len(adapter.calls) == 1


def test_failed_call_receipt_is_billable(service):
    rid = reserve(service)
    adapter = FixtureAdapter(UsageReceipt('failed-call', 100_000, {'partial_tokens': 50}, 'failed'))
    PaidGateway(service, [adapter]).execute(rid)
    assert service.balance('run')['available_micro_usd'] == 4_900_000


def test_no_unregistered_execution_or_credential_fallback(service):
    rid = reserve(service)
    with pytest.raises(EconomyError, match='registered'):
        PaidGateway(service, []).execute(rid)
    assert service.reservation(rid)['state'] == 'reserved'


def test_new_liability_invalidates_undispatched_authorization(service):
    rid = reserve(service)
    service.set_hold('run', 'refund', 6_000_000, 'refund', 'refund exposure', 'hold')
    adapter = FixtureAdapter(UsageReceipt('not-run', 0))
    with pytest.raises(EconomyError, match='liability'):
        PaidGateway(service, [adapter]).execute(rid)
    assert not adapter.calls
