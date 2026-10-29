"""Durable verified-webhook inbox and inexpensive provider reconciliation worker.

The queue contains minimal signed-event descriptors, never raw customer payloads.
It supplies at-least-once processing: accounting idempotency remains authoritative.
No paid inference or agent loop runs here. Keep this control plane operator-owned.
"""
from __future__ import annotations

import json
import time
import uuid

from .service import EconomyError, EconomyService, encode, fingerprint, ident, now

STATES = {'pending', 'running', 'completed', 'dead_letter'}


def _bounded_int(value, minimum, maximum, label):
    if type(value) is not int or not minimum <= value <= maximum:
        raise EconomyError('invalid_input', f'{label} must be an integer from {minimum} to {maximum}', 422)
    return value


def _scope(provider, account, mode):
    ident(provider); ident(account)
    if mode not in ('test', 'live'):
        raise EconomyError('mode_mismatch', 'Expected test or live reconciliation mode', 422)


class ReconciliationQueue:
    def __init__(self, service: EconomyService, *, clock=time.time, max_attempts=8):
        self.service, self.clock = service, clock
        self.max_attempts = _bounded_int(max_attempts, 1, 50, 'max_attempts')

    @staticmethod
    def _projection(row):
        result = dict(row)
        payload = json.loads(result.pop('payload'))
        result.pop('lease_token', None)
        result.pop('fingerprint', None)
        if result['kind'] == 'payment':
            result['payment_id'] = payload['payment_id']
        else:
            result['event_id'] = payload['event']['id']
        return result

    def _audit(self, db, task_id, action, attempt, details=None):
        db.execute('INSERT INTO reconciliation_attempts VALUES(?,?,?,?,?,?)',
                   (str(uuid.uuid4()), task_id, action, attempt, now(), encode(details or {})))

    def _job(self, db, task_id):
        row = db.execute('SELECT * FROM reconciliation_jobs WHERE task_id=?', (ident(task_id),)).fetchone()
        if row is None:
            raise EconomyError('not_found', 'Reconciliation task not found', 404)
        return row

    def task(self, task_id):
        with self.service._read() as db:
            return self._projection(self._job(db, task_id))

    def _enqueue(self, provider, account, mode, request_key, kind, payload):
        _scope(provider, account, mode)
        digest = fingerprint([kind, payload])
        with self.service._tx() as db:
            old = db.execute('SELECT * FROM reconciliation_jobs WHERE provider=? AND account=? AND mode=? AND request_key=?',
                             (provider, account, mode, request_key)).fetchone()
            if old:
                if old['fingerprint'] != digest:
                    raise EconomyError('idempotency_conflict', 'Reconciliation event ID reused with different content')
                return {**self._projection(old), 'duplicate': True}
            task_id, stamp = str(uuid.uuid4()), now()
            db.execute('INSERT INTO reconciliation_jobs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                       (task_id, provider, account, mode, request_key, digest, kind, encode(payload), 'pending', 0,
                        int(self.clock()), None, None, None, stamp, stamp))
            self._audit(db, task_id, 'queued', 0)
            return {**self._projection(self._job(db, task_id)), 'duplicate': False}

    def enqueue_verified_event(self, event, digest, provider, account, mode):
        """Trusted boundary: caller must verify the RAW body signature first."""
        _scope(provider, account, mode)
        if not isinstance(event, dict) or event.get('livemode') is not (mode == 'live') or event.get('account', account) != account:
            raise EconomyError('mode_mismatch', 'Verified webhook scope differs from configured provider', 422)
        event_id = ident(event.get('id'))
        event_type = ident(event.get('type'))
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
            raise EconomyError('invalid_input', 'Expected verified webhook SHA256 digest', 422)
        data = event.get('data')
        obj = data.get('object') if isinstance(data, dict) else None
        object_id = ident(obj.get('id') if isinstance(obj, dict) else None)
        minimal = {'id': event_id, 'type': event_type, 'livemode': mode == 'live', 'account': account,
                   'data': {'object': {'id': object_id}}}
        return self._enqueue(provider, account, mode, 'event:' + event_id, 'webhook', {'event': minimal, 'digest': digest})

    def enqueue_payment(self, provider, account, mode, payment_id):
        self.service.payment(provider, account, mode, payment_id)
        # Manual requests are new fresh reads. The worker's periodic requests use
        # deterministic time buckets below, and never accumulate concurrent polls.
        return self._enqueue(provider, account, mode, 'manual:' + str(uuid.uuid4()), 'payment', {'payment_id': payment_id})

    def list_tasks(self, provider, account, mode, state=None, limit=100):
        _scope(provider, account, mode)
        _bounded_int(limit, 1, 1000, 'limit')
        if state is not None and state not in STATES:
            raise EconomyError('invalid_input', 'Invalid reconciliation state', 422)
        with self.service._read() as db:
            rows = db.execute('SELECT * FROM reconciliation_jobs WHERE provider=? AND account=? AND mode=? '
                              'AND (? IS NULL OR state=?) ORDER BY rowid DESC LIMIT ?', (provider, account, mode, state, state, limit))
            return [self._projection(row) for row in rows]

    def claim(self, provider, account, mode, lease_seconds=300):
        _scope(provider, account, mode)
        _bounded_int(lease_seconds, 10, 86400, 'lease_seconds')
        clock = int(self.clock())
        with self.service._tx() as db:
            while True:
                row = db.execute("SELECT * FROM reconciliation_jobs WHERE provider=? AND account=? AND mode=? AND "
                                 "((state='pending' AND next_attempt_at<=?) OR (state='running' AND lease_until<=?)) ORDER BY rowid LIMIT 1",
                                 (provider, account, mode, clock, clock)).fetchone()
                if row is None:
                    return None
                if row['attempts'] >= self.max_attempts:
                    db.execute("UPDATE reconciliation_jobs SET state='dead_letter',lease_token=NULL,lease_until=NULL,last_error_code='lease_expired',updated_at=? WHERE task_id=?", (now(), row['task_id']))
                    self._audit(db, row['task_id'], 'dead_letter', row['attempts'], {'error_code': 'lease_expired'})
                    continue
                if row['state'] == 'running':
                    self._audit(db, row['task_id'], 'lease_expired', row['attempts'])
                token = str(uuid.uuid4())
                db.execute("UPDATE reconciliation_jobs SET state='running',attempts=attempts+1,lease_until=?,lease_token=?,updated_at=? WHERE task_id=?",
                           (clock+lease_seconds, token, now(), row['task_id']))
                claimed = dict(self._job(db, row['task_id']))
                claimed['payload'] = json.loads(claimed['payload'])
                self._audit(db, row['task_id'], 'claimed', claimed['attempts'])
                return claimed

    def _leased(self, db, task_id, lease_token):
        row = self._job(db, task_id)
        if row['state'] != 'running' or row['lease_token'] != lease_token or row['lease_until'] <= int(self.clock()):
            raise EconomyError('stale_lease', 'Reconciliation task lease expired or was reclaimed')
        return row

    def complete(self, task_id, lease_token):
        with self.service._tx() as db:
            row = self._leased(db, task_id, lease_token)
            db.execute("UPDATE reconciliation_jobs SET state='completed',lease_token=NULL,lease_until=NULL,last_error_code=NULL,updated_at=? WHERE task_id=?", (now(), task_id))
            self._audit(db, task_id, 'completed', row['attempts'])
            return self._projection(self._job(db, task_id))

    def fail(self, task_id, lease_token, error_code):
        ident(error_code)
        with self.service._tx() as db:
            row = self._leased(db, task_id, lease_token)
            state = 'dead_letter' if row['attempts'] >= self.max_attempts else 'pending'
            delay = min(3600, 5 * 2**min(row['attempts']-1, 10))
            db.execute('UPDATE reconciliation_jobs SET state=?,next_attempt_at=?,lease_token=NULL,lease_until=NULL,last_error_code=?,updated_at=? WHERE task_id=?',
                       (state, int(self.clock())+delay, error_code, now(), task_id))
            self._audit(db, task_id, state, row['attempts'], {'error_code': error_code})
            return self._projection(self._job(db, task_id))

    def retry(self, task_id, reason, idempotency_key):
        ident(reason); ident(idempotency_key)
        digest = fingerprint([reason])
        with self.service._tx() as db:
            row = self._job(db, task_id)
            previous = db.execute('SELECT * FROM reconciliation_operator_actions WHERE task_id=? AND idempotency_key=?', (task_id, idempotency_key)).fetchone()
            if previous:
                if previous['fingerprint'] != digest:
                    raise EconomyError('idempotency_conflict', 'Retry key already used with another reason')
                return {**self._projection(row), 'duplicate': True}
            if row['state'] != 'dead_letter':
                raise EconomyError('invalid_state', 'Only dead-letter tasks require manual retry')
            db.execute('INSERT INTO reconciliation_operator_actions VALUES(?,?,?,?,?)', (task_id, idempotency_key, digest, reason, now()))
            db.execute("UPDATE reconciliation_jobs SET state='pending',attempts=0,next_attempt_at=?,lease_token=NULL,lease_until=NULL,last_error_code=NULL,updated_at=? WHERE task_id=?", (int(self.clock()), now(), task_id))
            self._audit(db, task_id, 'operator_retry', row['attempts'], {'reason': reason})
            return {**self._projection(self._job(db, task_id)), 'duplicate': False}


class ReconciliationWorker:
    def __init__(self, queue: ReconciliationQueue, adapter):
        _scope(getattr(adapter,'provider','stripe'), adapter.account, adapter.mode)
        self.queue, self.adapter = queue, adapter

    def run_once(self, limit=10):
        _bounded_int(limit, 1, 100, 'limit')
        summary = {'processed': 0, 'completed': 0, 'retried': 0, 'dead_letter': 0}
        for _ in range(limit):
            task = self.queue.claim(getattr(self.adapter,'provider','stripe'), self.adapter.account, self.adapter.mode)
            if task is None:
                break
            summary['processed'] += 1
            try:
                if task['kind'] == 'payment':
                    self.adapter.reconcile(task['payload']['payment_id'])
                else:
                    self.adapter.process_verified_event(task['payload']['event'], task['payload']['digest'])
            except Exception as error:
                # Store a fixed error code, never exception strings/provider payloads.
                code = error.code if isinstance(error, EconomyError) else 'worker_error'
                try:
                    result = self.queue.fail(task['task_id'], task['lease_token'], code)
                except EconomyError as lease_error:
                    if lease_error.code != 'stale_lease':
                        raise
                    summary['retried'] += 1
                    continue
                summary['dead_letter' if result['state'] == 'dead_letter' else 'retried'] += 1
            else:
                try:
                    self.queue.complete(task['task_id'], task['lease_token'])
                except EconomyError as lease_error:
                    if lease_error.code != 'stale_lease':
                        raise
                    summary['retried'] += 1
                else:
                    summary['completed'] += 1
        return summary

    def schedule_due_payments(self, run_id, interval_seconds=300, limit=100):
        _bounded_int(interval_seconds, 60, 86400, 'interval_seconds')
        _bounded_int(limit, 1, 1000, 'limit')
        service, adapter = self.queue.service, self.adapter
        clock = int(self.queue.clock())
        bucket = clock // interval_seconds
        queued = 0
        with service._tx() as db:
            run = service._run(db, run_id)
            if run['mode'] != adapter.mode:
                raise EconomyError('mode_mismatch', 'Worker cannot poll another run mode')
            # Least-recently-polled first, so a small batch cannot starve later payments.
            rows = db.execute(
                "WITH candidates AS (SELECT p.*, p.rowid payment_rowid, "
                "(SELECT MAX(j.next_attempt_at) FROM reconciliation_jobs j WHERE j.provider=p.provider AND j.account=p.account AND j.mode=p.mode AND j.kind='payment' AND json_extract(j.payload,'$.payment_id')=p.payment_id) last_poll "
                "FROM payments p WHERE p.run_id=? AND p.provider=? AND p.account=? AND p.mode=? AND NOT EXISTS "
                "(SELECT 1 FROM reconciliation_jobs j WHERE j.provider=p.provider AND j.account=p.account AND j.mode=p.mode AND j.kind='payment' AND json_extract(j.payload,'$.payment_id')=p.payment_id AND j.state IN ('pending','running','dead_letter'))) "
                "SELECT * FROM candidates WHERE last_poll IS NULL OR last_poll<=? ORDER BY COALESCE(last_poll,0),payment_rowid LIMIT ?",
                (run_id, getattr(adapter,'provider','stripe'), adapter.account, adapter.mode, clock-interval_seconds, limit)).fetchall()
            for payment in rows:
                if payment['last_poll'] is not None and clock-payment['last_poll'] < interval_seconds:
                    continue
                payload = encode({'payment_id': payment['payment_id']})
                active = db.execute("SELECT 1 FROM reconciliation_jobs WHERE provider=? AND account=? AND mode=? AND kind='payment' AND payload=? AND state IN ('pending','running','dead_letter') LIMIT 1",
                                    (getattr(adapter,'provider','stripe'),adapter.account, adapter.mode, payload)).fetchone()
                if active:
                    continue
                key = 'poll:' + fingerprint([payment['payment_id'], bucket])
                if db.execute("SELECT 1 FROM reconciliation_jobs WHERE provider=? AND account=? AND mode=? AND request_key=?", (getattr(adapter,'provider','stripe'),adapter.account, adapter.mode, key)).fetchone():
                    continue
                task_id, stamp = str(uuid.uuid4()), now()
                db.execute('INSERT INTO reconciliation_jobs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                           (task_id, getattr(adapter,'provider','stripe'), adapter.account, adapter.mode, key, fingerprint(['payment', {'payment_id': payment['payment_id']}]), 'payment', payload,
                            'pending', 0, clock, None, None, None, stamp, stamp))
                self.queue._audit(db, task_id, 'scheduled_poll', 0)
                queued += 1
        return {'scheduled': queued}
