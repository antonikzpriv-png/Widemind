"""Durability and concurrency of the economy worker; all provider events are fixtures."""
import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from widemind.economy.reconciliation import ReconciliationQueue, ReconciliationWorker
from widemind.economy.service import EconomyError, EconomyService

ACCOUNT = 'acct_fixture'


def event(identifier='evt_fixture', account=ACCOUNT):
    return {'id': identifier, 'type': 'checkout.session.completed', 'livemode': False, 'account': account,
            'data': {'object': {'id': 'cs_fixture', 'customer_email': 'private-canary@example.invalid'}}}


def enqueue(queue, identifier='evt_fixture', account=ACCOUNT, digest=None):
    raw = event(identifier, account)
    return queue.enqueue_verified_event(raw, digest or hashlib.sha256(json.dumps(raw).encode()).hexdigest(), 'stripe', account, 'test')


@pytest.fixture
def context(tmp_path):
    service = EconomyService(tmp_path/'accounting.sqlite3')
    service.create_run('run')
    service.register_payment('run','stripe',ACCOUNT,'cs_fixture','job','register')
    clock = [1_000]
    queue = ReconciliationQueue(service, clock=lambda: clock[0])
    return service, queue, clock


class FixtureAdapter:
    account, mode = ACCOUNT, 'test'
    def __init__(self, error=None):
        self.error, self.calls = error, []
    def process_verified_event(self, event, digest):
        self.calls.append((event, digest))
        if self.error:
            raise self.error
    def reconcile(self, payment_id):
        self.calls.append(payment_id)
        if self.error:
            raise self.error


def test_verified_event_enqueue_is_durable_minimal_and_deduplicated(context):
    service, queue, _ = context
    task = enqueue(queue)
    duplicate = enqueue(queue)
    assert duplicate['task_id'] == task['task_id'] and duplicate['duplicate'] is True
    assert task['state'] == 'pending'
    with sqlite3.connect(service.path) as db:
        stored = db.execute('SELECT payload FROM reconciliation_jobs').fetchone()[0]
        assert 'private-canary' not in stored and 'customer_email' not in stored
    assert service.balance('run')['posted_micro_usd'] == 5_000_000
    recovered = ReconciliationQueue(EconomyService(service.path), clock=queue.clock)
    adapter = FixtureAdapter()
    assert ReconciliationWorker(recovered, adapter).run_once()['completed'] == 1
    assert recovered.task(task['task_id'])['state'] == 'completed'
    assert ReconciliationWorker(recovered, adapter).run_once()['processed'] == 0
    assert len(adapter.calls) == 1
    assert 'lease_token' not in recovered.task(task['task_id'])


def test_event_duplicate_scope_and_digest_conflicts(context):
    _, queue, _ = context
    first = enqueue(queue)
    other = enqueue(queue, account='acct_other')
    assert first['task_id'] != other['task_id']
    with pytest.raises(EconomyError, match='different content'):
        enqueue(queue, digest='f'*64)
    assert queue.task(first['task_id'])['attempts'] == 0


def test_concurrent_workers_claim_once(context):
    _, queue, _ = context
    task = enqueue(queue)
    with ThreadPoolExecutor(max_workers=8) as workers:
        results = list(workers.map(lambda _: queue.claim('stripe', ACCOUNT, 'test'), range(8)))
    winners = [value for value in results if value is not None]
    assert len(winners) == 1 and winners[0]['task_id'] == task['task_id']
    assert winners[0]['attempts'] == 1


def test_expired_lease_recovery_rejects_old_completion(context):
    service, queue, clock = context
    task = enqueue(queue)
    first = queue.claim('stripe',ACCOUNT,'test',lease_seconds=10)
    clock[0] += 11
    recovered = ReconciliationQueue(EconomyService(service.path), clock=queue.clock)
    next_claim = recovered.claim('stripe',ACCOUNT,'test')
    assert next_claim['attempts'] == 2 and next_claim['lease_token'] != first['lease_token']
    with pytest.raises(EconomyError, match='lease'):
        queue.complete(task['task_id'], first['lease_token'])
    recovered.complete(task['task_id'], next_claim['lease_token'])
    assert recovered.task(task['task_id'])['state'] == 'completed'


def test_retries_backoff_dead_letter_and_audited_operator_retry(context):
    service, _, clock = context
    queue = ReconciliationQueue(service, clock=lambda: clock[0], max_attempts=2)
    task = enqueue(queue)
    adapter = FixtureAdapter(EconomyError('provider_unavailable', 'private provider message'))
    worker = ReconciliationWorker(queue, adapter)
    assert worker.run_once()['retried'] == 1
    assert queue.task(task['task_id'])['next_attempt_at'] == 1_005
    assert worker.run_once()['processed'] == 0
    clock[0] += 5
    assert worker.run_once()['dead_letter'] == 1
    assert queue.task(task['task_id'])['state'] == 'dead_letter'
    result = queue.retry(task['task_id'], 'provider recovered', 'operator-retry')
    assert result['state'] == 'pending' and result['attempts'] == 0
    assert queue.retry(task['task_id'], 'provider recovered', 'operator-retry')['duplicate'] is True
    with pytest.raises(EconomyError):
        queue.retry(task['task_id'], 'different reason', 'operator-retry')
    adapter.error = None
    assert worker.run_once()['completed'] == 1
    with sqlite3.connect(service.path) as db:
        details = json.dumps(db.execute('SELECT details FROM reconciliation_attempts').fetchall())
        assert 'private provider message' not in details
        assert db.execute('SELECT COUNT(*) FROM reconciliation_operator_actions').fetchone()[0] == 1


def test_periodic_polling_is_bounded_deduplicated_and_restart_safe(context):
    service, queue, clock = context
    adapter = FixtureAdapter()
    worker = ReconciliationWorker(queue, adapter)
    assert worker.schedule_due_payments('run')['scheduled'] == 1
    assert worker.schedule_due_payments('run')['scheduled'] == 0
    assert worker.run_once()['completed'] == 1
    assert worker.schedule_due_payments('run')['scheduled'] == 0
    clock[0] += 300
    assert worker.schedule_due_payments('run')['scheduled'] == 1
    assert worker.run_once()['completed'] == 1
    assert adapter.calls == ['cs_fixture','cs_fixture']
    assert service.balance('run')['posted_micro_usd'] == 5_000_000


def test_small_poll_batch_does_not_starve_other_payments(context):
    service, queue, _ = context
    for i in range(3):
        service.register_payment('run','stripe',ACCOUNT,f'cs_{i}','job',f'register{i}')
    worker = ReconciliationWorker(queue, FixtureAdapter())
    for _ in range(4):
        assert worker.schedule_due_payments('run',limit=1)['scheduled'] == 1
        worker.run_once()
    assert len(queue.list_tasks('stripe',ACCOUNT,'test')) == 4


def test_queue_cannot_cross_account_or_mode(context):
    _, queue, _ = context
    task = enqueue(queue)
    assert queue.claim('stripe','acct_other','test') is None
    assert queue.claim('stripe',ACCOUNT,'live') is None
    assert queue.list_tasks('stripe','acct_other','test') == []
    assert queue.task(task['task_id'])['attempts'] == 0


@pytest.mark.parametrize('mutation', ["UPDATE reconciliation_jobs SET payload='{}'", "DELETE FROM reconciliation_jobs", "INSERT OR REPLACE INTO reconciliation_jobs SELECT * FROM reconciliation_jobs"])
def test_verified_inputs_cannot_be_rewritten(context, mutation):
    service, queue, _ = context
    enqueue(queue)
    with sqlite3.connect(service.path) as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(mutation)


def test_v1_database_upgrade_preserves_financial_history(tmp_path):
    from pathlib import Path
    path = tmp_path/'legacy.sqlite3'
    migration = Path(__file__).parents[1]/'src/widemind/economy/migrations/001_economy.sql'
    with sqlite3.connect(path) as db:
        db.executescript(migration.read_text())
        db.execute('PRAGMA user_version=1')
        db.execute("INSERT INTO runs VALUES('legacy','test','2026-10-28T00:00:00Z')")
        db.execute("INSERT INTO ledger VALUES('opening','legacy','opening_grant','opening_grant',5000000,'run:legacy','{}','2026-10-28T00:00:00Z')")
    service = EconomyService(path)
    assert service.balance('legacy')['posted_micro_usd'] == 5_000_000
    assert len(service.ledger('legacy')) == 1
    with sqlite3.connect(path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 2
    queue = ReconciliationQueue(service)
    assert enqueue(queue)['state'] == 'pending'
    assert EconomyService(path).balance('legacy')['posted_micro_usd'] == 5_000_000


@pytest.mark.parametrize('limit', [0, -1, True, 1.5, 2**100])
def test_queue_limits_reject_invalid_input(context, limit):
    _, queue, _ = context
    with pytest.raises(EconomyError):
        queue.list_tasks('stripe',ACCOUNT,'test',limit=limit)


def test_dead_letter_poll_requires_operator_recovery_before_new_auto_polls(context):
    service, _, clock = context
    queue = ReconciliationQueue(service, clock=lambda: clock[0], max_attempts=1)
    worker = ReconciliationWorker(queue,FixtureAdapter(EconomyError('provider_unavailable','temporary outage')))
    assert worker.schedule_due_payments('run')['scheduled'] == 1
    assert worker.run_once()['dead_letter'] == 1
    clock[0] += 3600
    assert worker.schedule_due_payments('run')['scheduled'] == 0
    task = queue.list_tasks('stripe',ACCOUNT,'test')[0]
    queue.retry(task['task_id'],'provider recovered','recover')
    worker.adapter.error = None
    assert worker.run_once()['completed'] == 1
    clock[0] += 300
    assert worker.schedule_due_payments('run')['scheduled'] == 1
