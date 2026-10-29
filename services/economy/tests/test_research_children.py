"""Service-v1 research allocation fixtures; no provider or payment calls occur."""
from concurrent.futures import ThreadPoolExecutor
import sqlite3
import threading

import pytest

from widemind.economy.reporting import audit_integrity, public_summary
from widemind.economy.service import EconomyError, EconomyService
from test_api import settings, client, headers, create_run

U = 10_000
ACCOUNT = 'dedicated-research-account'
LIMITS = {'max_tokens': 1000, 'max_duration_ms': 60000, 'max_storage_bytes': 8000000}


@pytest.fixture
def economy(tmp_path):
    service = EconomyService(tmp_path / 'service-v1.sqlite3')
    service.create_run('run')
    return service


def parent(service, amount=100*U, *, action='research-job', run='run', account=ACCOUNT, start=True):
    value = service.reserve(run, action, amount, action, 'research.collect', account, LIMITS)
    return service.start_reservation(value['reservation_id']) if start else value


def child(service, group, amount=60*U, *, action='child-call', provider='ai.generate', account=ACCOUNT, limits=None):
    return service.reserve_child(group['reservation_id'], action, amount, action, provider, account,
                                 limits or {'max_output_tokens': 100, 'max_duration_ms': 1000})


def settle(service, value, amount, *, outcome='succeeded', charge=None):
    return service.settle(value['reservation_id'], amount, value['provider'], value['account'],
                          charge or value['action_id'] + '-charge', {'fixture': 'synthetic'}, outcome)


def test_parent_transfers_hold_without_double_reserving_and_settles_zero(economy):
    group = parent(economy)
    call = child(economy, group)
    assert economy.balance('run')['reserved_micro_usd'] == 100*U
    economy.start_reservation(call['reservation_id'])
    settle(economy, call, 20*U)
    assert economy.balance('run')['posted_micro_usd'] == 480*U
    assert economy.balance('run')['reserved_micro_usd'] == 40*U
    settle(economy, group, 0)
    assert economy.balance('run')['reserved_micro_usd'] == 0
    assert economy.balance('run')['available_micro_usd'] == 480*U
    assert audit_integrity(economy, 'run')['ok']


def test_fully_reserved_exhausted_run_can_allocate_and_start_child(economy):
    group = parent(economy, 500*U)
    assert economy.balance('run')['exhausted']
    call = child(economy, group, 300*U)
    assert economy.balance('run')['reserved_micro_usd'] == 500*U
    assert economy.start_reservation(call['reservation_id'])['state'] == 'running'
    assert economy.balance('run')['available_micro_usd'] == 0


def test_concurrent_quotes_cannot_exceed_original_parent_cap(economy):
    group = parent(economy)
    barrier = threading.Barrier(2)
    def allocate(index):
        barrier.wait(timeout=3)
        try:
            return child(EconomyService(economy.path), group, action=f'child-{index}')
        except EconomyError as error:
            return error.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(allocate, (1, 2)))
    assert sum(isinstance(value, dict) for value in results) == 1
    assert 'job_budget_exceeded' in results
    assert len(economy.reservation_children(group['reservation_id'])) == 1
    assert economy.balance('run')['reserved_micro_usd'] == 100*U


@pytest.mark.parametrize('closed', ['settled', 'cancelled'])
def test_completed_and_cancelled_quotes_still_count_full_job_cap(economy, closed):
    group = parent(economy)
    call = child(economy, group)
    if closed == 'settled':
        economy.start_reservation(call['reservation_id'])
        settle(economy, call, 1)
    else:
        economy.cancel_reservation(call['reservation_id'])
    with pytest.raises(EconomyError) as error:
        child(economy, group, 50*U, action='second')
    assert error.value.code == 'job_budget_exceeded'
    assert economy.balance('run')['reserved_micro_usd'] == 40*U


def test_unknown_partial_restart_parent_close_and_final_delta_preserve_native_liability(economy):
    group = parent(economy)
    call = child(economy, group)
    economy.start_reservation(call['reservation_id'])
    settle(economy, call, 20*U, outcome='unknown')
    assert economy.balance('run')['posted_micro_usd'] == 480*U
    assert economy.balance('run')['reserved_micro_usd'] == 80*U
    assert economy.balance('run')['available_micro_usd'] == 400*U
    economy = EconomyService(economy.path)
    assert economy.reservation_children(group['reservation_id'])[0]['state'] == 'unknown'
    settle(economy, group, 0)
    assert economy.balance('run')['reserved_micro_usd'] == 40*U
    assert economy.balance('run')['available_micro_usd'] == 440*U
    settle(economy, call, 25*U)
    settle(economy, call, 25*U)  # Same native charge identity is idempotent.
    assert economy.balance('run')['posted_micro_usd'] == 475*U
    assert economy.balance('run')['reserved_micro_usd'] == 0
    assert sum(row['amount_micro_usd'] for row in economy.ledger('run') if row['kind']=='operating_charge') == -25*U
    assert audit_integrity(economy, 'run')['ok']


def test_payment_snapshot_aggregates_all_children_and_waits_for_final_costs(economy):
    economy.register_payment('run', 'paypal', 'fixture-merchant', 'PAYMENT', 'job', 'binding')
    group = parent(economy)
    first = child(economy, group, 60*U)
    second = child(economy, group, 0, action='free-file', provider='files', limits={'max_storage_bytes': 1000})
    economy.start_reservation(first['reservation_id'])
    settle(economy, first, 20*U, outcome='unknown')
    settle(economy, group, 0)
    def snapshot():
        return economy.payment_snapshot('paypal', 'fixture-merchant', 'test', 'PAYMENT', group['reservation_id'])
    assert snapshot()['operating_cost_micro_usd'] == 20*U
    assert snapshot()['costs_final'] is False
    settle(economy, first, 25*U)
    assert snapshot()['operating_cost_micro_usd'] == 25*U
    assert snapshot()['costs_final'] is False  # Allocated file child remains open.
    economy.cancel_reservation(second['reservation_id'])
    assert snapshot()['costs_final'] is True
    assert audit_integrity(economy, 'run')['ok']


def test_parent_direct_charge_cancel_and_dispatch_after_closure_are_forbidden(economy):
    group = parent(economy)
    call = child(economy, group)
    with pytest.raises(EconomyError) as error:
        settle(economy, group, 1)
    assert error.value.code == 'research_parent_direct_cost'
    with pytest.raises(EconomyError) as error:
        economy.cancel_reservation(group['reservation_id'])
    assert error.value.code == 'research_parent_cancel_forbidden'
    settle(economy, group, 0)
    with pytest.raises(EconomyError):
        child(economy, group, 0, action='late')
    with pytest.raises(EconomyError) as error:
        economy.start_reservation(call['reservation_id'])
    assert error.value.code == 'closed_research_parent'
    assert economy.balance('run')['reserved_micro_usd'] == 60*U


def test_unknown_parent_can_allocate_and_idempotent_replay_survives_parent_closure(economy):
    group = parent(economy)
    settle(economy, group, None, outcome='unknown')
    call = child(economy, group)
    settle(economy, group, 0)
    assert child(economy, group)['reservation_id'] == call['reservation_id']
    with pytest.raises(EconomyError) as error:
        child(economy, group, 50*U)
    assert error.value.code == 'idempotency_conflict'
    assert len(economy.reservation_children(group['reservation_id'])) == 1


@pytest.mark.parametrize('field,value', [('account', 'another-account'), ('provider', 'research.collect'),
                                       ('provider', 'payments'), ('limits', {'max_tokens': 1001}),
                                       ('limits', {'max_output_tokens': 1001}), ('limits', {'max_duration_ms': 60001}),
                                       ('limits', {'max_storage_bytes': 8000001})])
def test_child_identity_provider_and_parent_resource_bounds(economy, field, value):
    group = parent(economy)
    with pytest.raises(EconomyError):
        child(economy, group, **{field: value})
    assert economy.reservation_children(group['reservation_id']) == []
    assert economy.balance('run')['reserved_micro_usd'] == 100*U


def test_wrong_parent_unstarted_parent_cross_run_links_and_immutable_allocations(economy):
    unstarted = parent(economy, start=False)
    with pytest.raises(EconomyError):
        child(economy, unstarted)
    group = economy.start_reservation(unstarted['reservation_id'])
    linked = child(economy, group)
    economy.create_run('other-run')
    other = economy.reserve('other-run', 'other', 1, 'other', 'files', ACCOUNT, {'max_units': 1})
    with sqlite3.connect(economy.path) as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute('INSERT INTO research_reservation_children VALUES(?,?,?)', (group['reservation_id'], other['reservation_id'], 'fixture'))
        for sql in ('DELETE FROM research_reservation_children', "UPDATE research_reservation_children SET created_at='changed'", 'INSERT OR REPLACE INTO research_reservation_children SELECT * FROM research_reservation_children'):
            with pytest.raises(sqlite3.IntegrityError):
                db.execute(sql)
    wrong = economy.reserve('run', 'ordinary', 1, 'ordinary', 'ai.generate', ACCOUNT, {'max_tokens': 1})
    economy.start_reservation(wrong['reservation_id'])
    with pytest.raises(EconomyError):
        child(economy, wrong, 0, action='nested')
    with pytest.raises(EconomyError):
        child(economy, linked, 0, action='nested')


def test_service_v5_upgrade_preserves_ledger_and_immutable_service_family(economy):
    ledger = economy.ledger('run')
    with sqlite3.connect(economy.path) as db:
        db.execute('DROP TABLE research_reservation_children')
        db.execute('PRAGMA user_version=5')
    restored = EconomyService(economy.path)
    assert restored.ledger('run') == ledger
    with sqlite3.connect(economy.path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 6
        assert db.execute('SELECT family FROM economy_database_family').fetchone()[0] == 'service-v1'


def test_reporting_uses_transferred_holds_and_detects_cross_run_allocation_damage(economy):
    group = parent(economy)
    call = child(economy, group)
    economy.start_reservation(call['reservation_id'])
    settle(economy, call, 20*U, outcome='unknown')
    report = audit_integrity(economy, 'run')
    assert report['ok'], report
    assert report['inspected']['research_children'] == 1
    assert public_summary(economy, 'run')['reserved_micro_usd'] == 80*U
    economy.create_run('other')
    other = economy.reserve('other', 'other', 1, 'other', 'files', ACCOUNT, {'max_units': 1})
    with sqlite3.connect(economy.path) as db:
        db.execute('DROP TRIGGER research_children_binding')
        db.execute('INSERT INTO research_reservation_children VALUES(?,?,?)', (group['reservation_id'], other['reservation_id'], 'legacy-damage'))
    report = audit_integrity(economy, 'run')
    assert not report['ok']
    assert 'research_child_binding' in {row['code'] for row in report['checks'] if not row['ok']}


def test_children_http_is_gateway_operator_only_and_forbids_extra_run_binding(client, settings):
    create_run(client, settings)
    service = client.app.state.economy
    group = parent(service, run='run-a')
    url = f"/v1/reservations/{group['reservation_id']}/children"
    body = {'action_id': 'child', 'amount_micro_usd': 0, 'idempotency_key': 'child',
            'provider': 'files', 'account': ACCOUNT, 'limits': {'max_storage_bytes': 1000}}
    assert client.post(url, json=body, headers=headers(settings, 'agent')).status_code == 403
    assert client.get(url, headers=headers(settings, 'agent')).status_code == 403
    assert client.post(url, json={**body, 'run_id': 'another'}, headers=headers(settings, 'gateway')).status_code == 422
    response = client.post(url, json=body, headers=headers(settings, 'gateway'))
    assert response.status_code == 200, response.text
    assert response.json()['run_id'] == 'run-a'
    for role in ('operator', 'gateway'):
        listed = client.get(url, headers=headers(settings, role))
        assert listed.status_code == 200
        assert listed.json()[0]['reservation_id'] == response.json()['reservation_id']
