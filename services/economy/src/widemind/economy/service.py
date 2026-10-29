"""Trusted accounting boundary; all wallet mutations are transactional and auditable.

Callers are trusted adapters or authenticated HTTP routes, never model-generated code.
Keep this module and its SQLite file outside the agent's writable filesystem.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

OPENING_CREDIT = 5_000_000
MAX_MONEY = 9_000_000_000_000_000
LIMIT_NAMES = {'max_tokens', 'max_output_tokens', 'max_duration_ms', 'max_storage_bytes', 'max_units'}


class EconomyError(Exception):
    def __init__(self, code: str, message: str, status: int = 409):
        self.code, self.status = code, status
        super().__init__(message)


def ident(value: Any, label: str = 'identifier') -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 200 or any(ord(c) < 32 for c in value):
        raise EconomyError('invalid_input', f'{label} must be a nonempty string of at most 200 characters', 422)
    return value


def money(value: Any, *, positive: bool = False, signed: bool = False) -> int:
    if type(value) is not int or abs(value) > MAX_MONEY or (not signed and value < int(positive)):
        raise EconomyError('invalid_money', 'Expected a bounded integer micro-USD amount', 422)
    return value


def encode(value: Any) -> str:
    try:
        result = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)
    except (ValueError, TypeError, RecursionError) as exc:
        raise EconomyError('invalid_input', 'Expected finite JSON data', 422) from exc
    if len(result.encode()) > 65536:
        raise EconomyError('invalid_input', 'Accounting metadata exceeds 64 KiB', 422)
    return result


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def fingerprint(value: Any) -> str:
    return hashlib.sha256(encode(value).encode()).hexdigest()


def _row(row):
    return dict(row) if row is not None else None


class EconomyService:
    def __init__(self, database_path: str | Path):
        self.path = Path(database_path)
        if str(database_path) == ':memory:':
            raise EconomyError('invalid_database', 'Use a persistent local SQLite database', 422)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Atomically create restrictive files before sqlite opens them (no umask window).
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        # Concurrent first openers can receive SQLITE_BUSY from journal_mode
        # without honoring SQLite's normal busy handler. Retry only lock errors,
        # with a bounded startup deadline and a fresh connection each time.
        for attempt in range(8):
            try:
                self._migrate()
                break
            except sqlite3.OperationalError as error:
                code = getattr(error, 'sqlite_errorcode', 0) & 0xFF
                if code not in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED) or attempt == 7:
                    raise
                time.sleep(min(0.01 * 2**attempt, 0.25))

    def _migrate(self):
        with self._initial_connection() as connection:
            connection.execute('PRAGMA busy_timeout=1000')
            version = connection.execute('PRAGMA user_version').fetchone()[0]
            if version not in (0, 1, 2, 3, 4, 5):
                raise EconomyError('future_schema', 'Unsupported accounting schema; do not downgrade')
            connection.execute('PRAGMA journal_mode=WAL')
            connection.execute('PRAGMA foreign_keys=OFF')
            connection.execute('BEGIN IMMEDIATE')
            try:
                # Recheck after acquiring the migration lock: another process may
                # already have upgraded while this connection waited.
                version = connection.execute('PRAGMA user_version').fetchone()[0]
                if version not in (0, 1, 2, 3, 4, 5):
                    raise EconomyError('future_schema', 'Unsupported accounting schema; do not downgrade')
                migrations = ('001_economy.sql', '002_reconciliation_queue.sql', '003_reconciliation_schedule_index.sql', '004_projection_guards.sql', '005_zero_cost_authorization.sql')
                for target in range(version + 1, len(migrations) + 1):
                    script = (Path(__file__).parent / 'migrations' / migrations[target-1]).read_text()
                    statement = ''
                    for line in script.splitlines(keepends=True):
                        statement += line
                        if sqlite3.complete_statement(statement):
                            connection.execute(statement)
                            statement = ''
                    if statement.strip():
                        raise EconomyError('invalid_migration', 'Incomplete accounting migration')
                    connection.execute(f'PRAGMA user_version={target}')
                if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                    raise EconomyError("migration_integrity", "Accounting relationships failed migration verification")
                connection.commit()
                connection.execute("PRAGMA foreign_keys=ON")
            except BaseException:
                connection.rollback()
                raise

    @contextmanager
    def _initial_connection(self):
        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA synchronous=FULL')
        return db

    @contextmanager
    def _tx(self):
        db = self._connect()
        try:
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @contextmanager
    def _read(self):
        db = self._connect()
        try:
            db.execute('BEGIN')
            yield db
        finally:
            db.rollback()
            db.close()

    def _run(self, db, run_id):
        row = db.execute('SELECT * FROM runs WHERE run_id=?', (ident(run_id, 'run_id'),)).fetchone()
        if row is None:
            raise EconomyError('not_found', 'Run not found', 404)
        return row

    def _event(self, db, run_id, kind, data):
        db.execute('INSERT INTO events(event_id,version,type,run_id,occurred_at,data) VALUES(?,1,?,?,?,?)',
                   (str(uuid.uuid4()), kind, run_id, now(), encode(data)))

    def _entry(self, db, run_id, kind, amount, reference, metadata=None, category=None):
        entry_id = str(uuid.uuid4())
        db.execute('INSERT INTO ledger VALUES(?,?,?,?,?,?,?,?)',
                   (entry_id, run_id, kind, category or kind, money(amount, signed=True), reference, encode(metadata or {}), now()))
        return entry_id

    def _operation(self, db, run_id, scope, key, arguments, callback):
        self._run(db, run_id)
        ident(key, 'idempotency_key')
        digest = fingerprint(arguments)
        old = db.execute('SELECT * FROM operations WHERE run_id=? AND scope=? AND idempotency_key=?', (run_id, scope, key)).fetchone()
        if old:
            if old['fingerprint'] != digest:
                raise EconomyError('idempotency_conflict', 'Idempotency key already used with different parameters')
            return json.loads(old['result'])
        result = callback()
        db.execute('INSERT INTO operations VALUES(?,?,?,?,?)', (run_id, scope, key, digest, encode(result)))
        return result

    def _balance(self, db, run_id):
        run = self._run(db, run_id)
        totals = {}
        metrics = {'earned_revenue_micro_usd': 0, 'infrastructure_subsidy_micro_usd': 0}
        for row in db.execute('SELECT category,amount_micro_usd,metadata FROM ledger WHERE run_id=?', (run_id,)):
            totals[row['category']] = totals.get(row['category'], 0) + row['amount_micro_usd']
            metric = row['category'] + '_micro_usd'
            if metric in metrics:
                metrics[metric] += json.loads(row['metadata'])['reported_micro_usd']
        posted = sum(totals.values())
        reserved = sum(max(0, r[0]-r[1]) for r in db.execute("SELECT amount_micro_usd,provisional_micro_usd FROM reservations WHERE run_id=? AND state IN ('reserved','running','unknown')", (run_id,)))
        held = sum(r[0] for r in db.execute('SELECT amount_micro_usd FROM holds WHERE run_id=?', (run_id,)))
        available = posted - reserved - held
        return dict(run_id=run_id, mode=run['mode'], posted_micro_usd=posted, reserved_micro_usd=reserved,
                    held_micro_usd=held, available_micro_usd=available, exhausted=available <= 0, totals=totals, metrics=metrics)

    def balance(self, run_id):
        with self._read() as db:
            return self._balance(db, run_id)

    def create_run(self, run_id, mode='test', funding_reference=None):
        ident(run_id, 'run_id')
        if mode not in ('test', 'live'):
            raise EconomyError('invalid_mode', 'Mode must be test or live', 422)
        with self._tx() as db:
            old = db.execute('SELECT * FROM runs WHERE run_id=?', (run_id,)).fetchone()
            if old:
                if old['mode'] != mode:
                    raise EconomyError('idempotency_conflict', 'Run mode cannot change')
            else:
                if mode == 'live' and not funding_reference:
                    raise EconomyError('funding_attestation_required', 'Live opening credit requires an operator funding reference', 422)
                if funding_reference is not None:
                    ident(funding_reference, 'funding_reference')
                db.execute('INSERT INTO runs VALUES(?,?,?)', (run_id, mode, now()))
                self._entry(db, run_id, 'opening_grant', OPENING_CREDIT, 'run:' + run_id,
                            {'mode': mode, 'unit': 'micro-USD', 'currency': 'USD', 'funding_reference': funding_reference, 'verification': 'operator_attested' if mode == 'live' else 'sandbox_credit'})
                self._event(db, run_id, 'economy.run_opened', {'opening_micro_usd': OPENING_CREDIT, 'mode': mode})
            return self._balance(db, run_id)

    def ledger(self, run_id):
        with self._read() as db:
            self._run(db, run_id)
            result = [dict(r) for r in db.execute('SELECT * FROM ledger WHERE run_id=? ORDER BY rowid', (run_id,))]
            for row in result:
                row['metadata'] = json.loads(row['metadata'])
                row['currency'], row['unit'] = 'USD', 'micro-USD'
            return result

    def events(self, run_id, after=0):
        if type(after) is not int or not 0 <= after <= 9_223_372_036_854_775_807:
            raise EconomyError('invalid_input', 'Event cursor must be nonnegative integer', 422)
        with self._read() as db:
            self._run(db, run_id)
            rows = [dict(r) for r in db.execute('SELECT * FROM events WHERE run_id=? AND sequence>? ORDER BY sequence LIMIT 1000', (run_id, after))]
            for row in rows:
                row['data'] = json.loads(row['data'])
            return rows

    def _reservation(self, db, reservation_id):
        row = _row(db.execute('SELECT * FROM reservations WHERE reservation_id=?', (ident(reservation_id),)).fetchone())
        if row is None:
            raise EconomyError('not_found', 'Reservation not found', 404)
        row['limits'] = json.loads(row.pop('limits_json'))
        return row

    def reservation(self, reservation_id):
        with self._read() as db:
            return self._reservation(db, reservation_id)

    def reservations(self, run_id):
        with self._read() as db:
            self._run(db, run_id)
            return [self._reservation(db, r[0]) for r in db.execute('SELECT reservation_id FROM reservations WHERE run_id=? ORDER BY rowid', (run_id,))]

    def reserve(self, run_id, action_id, amount_micro_usd, idempotency_key, provider, account, limits):
        amount = money(amount_micro_usd)
        for value in (action_id, provider, account):
            ident(value)
        if not isinstance(limits, dict) or not limits or set(limits) - LIMIT_NAMES:
            raise EconomyError('invalid_limits', 'Supply recognized enforceable provider limits', 422)
        for value in limits.values():
            money(value, positive=True)
        args = dict(action_id=action_id, amount_micro_usd=amount, provider=provider, account=account, limits=limits)
        with self._tx() as db:
            def action():
                if db.execute('SELECT 1 FROM reservations WHERE run_id=? AND action_id=?', (run_id, action_id)).fetchone():
                    raise EconomyError('action_exists', 'Action already has a reservation; reconcile before retry')
                if self._balance(db, run_id)['available_micro_usd'] < amount:
                    raise EconomyError('insufficient_funds', 'Available credit cannot fund this action')
                rid, stamp = str(uuid.uuid4()), now()
                db.execute('INSERT INTO reservations VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                           (rid, run_id, action_id, amount, provider, account, encode(limits), 'reserved', None, 0, stamp, stamp))
                self._event(db, run_id, 'economy.reserved', {'reservation_id': rid, **args})
                return self._reservation(db, rid)
            return self._operation(db, run_id, 'reserve', idempotency_key, args, action)

    def start_reservation(self, reservation_id):
        with self._tx() as db:
            row = self._reservation(db, reservation_id)
            if row['state'] != 'reserved':
                raise EconomyError('duplicate_dispatch', 'Action already dispatched or closed; reconcile before retry')
            if self._balance(db, row['run_id'])['available_micro_usd'] < 0:
                raise EconomyError('insufficient_funds', 'New liability invalidated this authorization; reconcile or cancel before dispatch')
            db.execute("UPDATE reservations SET state='running',updated_at=? WHERE reservation_id=?", (now(), reservation_id))
            self._event(db, row['run_id'], 'economy.dispatched', {'reservation_id': reservation_id})
            return self._reservation(db, reservation_id)

    def cancel_reservation(self, reservation_id):
        with self._tx() as db:
            row = self._reservation(db, reservation_id)
            if row['state'] == 'cancelled':
                return row
            if row['state'] != 'reserved':
                raise EconomyError('reconciliation_required', 'Cannot cancel a possibly billable attempt')
            db.execute("UPDATE reservations SET state='cancelled',updated_at=? WHERE reservation_id=?", (now(), reservation_id))
            self._event(db, row['run_id'], 'economy.reservation_cancelled', {'reservation_id': reservation_id})
            return self._reservation(db, reservation_id)

    def settle(self, reservation_id, actual_micro_usd, provider, account, external_charge_id, usage=None, outcome='succeeded'):
        ident(provider); ident(account); ident(external_charge_id)
        usage_json = encode(usage or {})
        if outcome not in ('succeeded', 'failed', 'unknown'):
            raise EconomyError('invalid_outcome', 'Invalid provider outcome', 422)
        if actual_micro_usd is not None:
            money(actual_micro_usd)
        with self._tx() as db:
            row = self._reservation(db, reservation_id)
            if (row['provider'], row['account']) != (provider, account):
                raise EconomyError('provider_mismatch', 'Charge must belong to the reserved provider/account')
            if row['state'] == 'cancelled':
                raise EconomyError('closed_reservation', 'Reservation was cancelled before dispatch')
            run = self._run(db, row['run_id'])
            old = db.execute('SELECT * FROM charges WHERE provider=? AND account=? AND mode=? AND external_charge_id=?', (provider, account, run['mode'], external_charge_id)).fetchone()
            if old:
                if (old['reservation_id'], old['amount_micro_usd'], old['usage'], old['outcome']) != (reservation_id, actual_micro_usd, usage_json, outcome):
                    raise EconomyError('idempotency_conflict', 'External charge already recorded differently')
                return row
            if row['state'] == 'settled':
                raise EconomyError('closed_reservation', 'Charge reconciled; use a compensating correction')
            if actual_micro_usd is None or outcome == 'unknown':
                # Verified billable partial work is a liability even while the outcome is unknown.
                # Keep the unused cap reserved; a subtotal never authorizes a retry.
                if actual_micro_usd is not None:
                    previous = db.execute('SELECT * FROM provisional_charges WHERE provider=? AND account=? AND mode=? AND external_charge_id=?', (provider, account, run['mode'], external_charge_id)).fetchone()
                    own = db.execute('SELECT * FROM provisional_charges WHERE reservation_id=?', (reservation_id,)).fetchone()
                    if (previous and previous['reservation_id'] != reservation_id) or (own and own['external_charge_id'] != external_charge_id):
                        raise EconomyError('idempotency_conflict', 'Provisional charge is bound to another attempt')
                    if actual_micro_usd < row['provisional_micro_usd']:
                        raise EconomyError('invalid_subtotal', 'A partial charge cannot decrease; reconcile the final total')
                    delta = actual_micro_usd - row['provisional_micro_usd']
                    if delta:
                        self._entry(db, row['run_id'], 'operating_charge', -delta, external_charge_id,
                                    {'reservation_id': reservation_id, 'provider': provider, 'account': account, 'provisional': True, 'usage': usage or {}})
                    db.execute('INSERT INTO provisional_charges VALUES(?,?,?,?,?,?) ON CONFLICT(provider,account,mode,external_charge_id) DO UPDATE SET amount_micro_usd=excluded.amount_micro_usd',
                               (provider, account, run['mode'], external_charge_id, reservation_id, actual_micro_usd))
                    db.execute('UPDATE reservations SET provisional_micro_usd=? WHERE reservation_id=?', (actual_micro_usd, reservation_id))
                db.execute("UPDATE reservations SET state='unknown',updated_at=? WHERE reservation_id=?", (now(), reservation_id))
                self._event(db, row['run_id'], 'economy.cost_unknown', {'reservation_id': reservation_id, 'provider': provider,
                            'account': account, 'external_charge_id': external_charge_id, 'reported_subtotal_micro_usd': actual_micro_usd, 'usage': usage or {}})
                return self._reservation(db, reservation_id)
            provisional = db.execute('SELECT * FROM provisional_charges WHERE provider=? AND account=? AND mode=? AND external_charge_id=?', (provider, account, run['mode'], external_charge_id)).fetchone()
            own_provisional = db.execute('SELECT * FROM provisional_charges WHERE reservation_id=?', (reservation_id,)).fetchone()
            if (provisional and provisional['reservation_id'] != reservation_id) or (own_provisional and own_provisional['external_charge_id'] != external_charge_id):
                raise EconomyError('idempotency_conflict', 'Partial charge belongs to another provider attempt')
            db.execute('INSERT INTO charges VALUES(?,?,?,?,?,?,?,?)',
                       (provider, account, run['mode'], external_charge_id, reservation_id, actual_micro_usd, usage_json, outcome))
            eid = self._entry(db, row['run_id'], 'operating_charge', -(actual_micro_usd-row['provisional_micro_usd']), external_charge_id,
                             {'provider': provider, 'account': account, 'reservation_id': reservation_id, 'usage': usage or {}, 'outcome': outcome})
            db.execute("UPDATE reservations SET state='settled',actual_micro_usd=?,updated_at=? WHERE reservation_id=?", (actual_micro_usd, now(), reservation_id))
            self._event(db, row['run_id'], 'economy.charge_settled', {'entry_id': eid, 'reservation_id': reservation_id,
                        'actual_micro_usd': actual_micro_usd, 'overage_micro_usd': max(0, actual_micro_usd-row['amount_micro_usd'])})
            return self._reservation(db, reservation_id)

    def charges(self, run_id):
        """Verified final charges and known partial liabilities, never estimated costs."""
        with self._read() as db:
            self._run(db, run_id)
            result = []
            for row in db.execute('SELECT c.* FROM charges c JOIN reservations r USING(reservation_id) WHERE r.run_id=? ORDER BY c.rowid', (run_id,)):
                item = dict(row)
                item['usage'] = json.loads(item['usage'])
                item['final'] = True
                result.append(item)
            for row in db.execute("SELECT c.* FROM provisional_charges c JOIN reservations r USING(reservation_id) WHERE r.run_id=? AND r.state!='settled' ORDER BY c.rowid", (run_id,)):
                result.append({**dict(row), 'final': False, 'outcome': 'unknown'})
            return result

    def _hold(self, db, run_id, hold_id, amount, kind, reason):
        db.execute('INSERT INTO holds VALUES(?,?,?,?,?) ON CONFLICT(run_id,hold_id) DO UPDATE SET amount_micro_usd=excluded.amount_micro_usd,kind=excluded.kind,reason=excluded.reason',
                   (run_id, hold_id, kind, amount, reason))
        self._event(db, run_id, 'economy.hold_changed', {'hold_id': hold_id, 'amount_micro_usd': amount, 'kind': kind, 'reason': reason})

    def set_hold(self, run_id, hold_id, amount_micro_usd, kind, reason, idempotency_key):
        ident(hold_id); ident(reason)
        if hold_id.startswith('payment:') or kind not in ('customer', 'refund'):
            raise EconomyError('invalid_hold', 'Use a customer/refund hold outside the reserved payment namespace', 422)
        money(amount_micro_usd)
        args = dict(hold_id=hold_id, amount_micro_usd=amount_micro_usd, kind=kind, reason=reason)
        with self._tx() as db:
            def action():
                self._hold(db, run_id, hold_id, amount_micro_usd, kind, reason)
                return args
            return self._operation(db, run_id, 'hold', idempotency_key, args, action)

    def record_funding(self, run_id, amount_micro_usd, category, reference, idempotency_key):
        money(amount_micro_usd, positive=True); ident(reference)
        if category not in ('owner_funding', 'test_funding'):
            raise EconomyError('invalid_funding', 'Only explicit owner/test funding may be granted', 422)
        with self._tx() as db:
            run = self._run(db, run_id)
            if category == 'test_funding' and run['mode'] != 'test':
                raise EconomyError('mode_mismatch', 'Test funding cannot enter a live run')
            def action():
                eid = self._entry(db, run_id, category, amount_micro_usd, reference)
                self._event(db, run_id, 'economy.funding', {'entry_id': eid, 'category': category, 'amount_micro_usd': amount_micro_usd})
                return {'entry_id': eid}
            return self._operation(db, run_id, 'funding', idempotency_key, [amount_micro_usd, category, reference], action)

    def _metric(self, run_id, amount, kind, reference, key, job_id=None):
        money(amount, signed=True); ident(reference)
        if job_id is not None:
            ident(job_id)
        with self._tx() as db:
            def action():
                eid = self._entry(db, run_id, kind, 0, reference, {'reported_micro_usd': amount, 'job_id': job_id})
                self._event(db, run_id, 'economy.'+kind, {'entry_id': eid, 'reported_micro_usd': amount, 'job_id': job_id})
                return {'entry_id': eid}
            return self._operation(db, run_id, kind, key, [amount, reference, job_id], action)

    def record_subsidy(self, run_id, amount_micro_usd, reference, idempotency_key):
        return self._metric(run_id, amount_micro_usd, 'infrastructure_subsidy', reference, idempotency_key)

    def record_earned_revenue(self, run_id, amount_micro_usd, job_id, reference, idempotency_key):
        return self._metric(run_id, amount_micro_usd, 'earned_revenue', reference, idempotency_key, job_id)

    def correct_entry(self, run_id, entry_id, amount_micro_usd, reason, idempotency_key):
        money(amount_micro_usd, signed=True); ident(entry_id); ident(reason)
        with self._tx() as db:
            original = db.execute('SELECT * FROM ledger WHERE run_id=? AND entry_id=?', (run_id, entry_id)).fetchone()
            if original is None:
                raise EconomyError('not_found', 'Original entry not found', 404)
            root = original
            # True-up uses the authoritative subtotal projection. A separate cash
            # correction while it is active would refund the same cost twice.
            for _ in range(128):
                if root['kind'] != 'correction':
                    break
                prior = json.loads(root['metadata']).get('corrects_entry_id')
                root = db.execute('SELECT * FROM ledger WHERE run_id=? AND entry_id=?', (run_id, prior)).fetchone()
                if root is None:
                    raise EconomyError('invalid_correction_chain', 'Correction history is incomplete')
            else:
                raise EconomyError('correction_chain_limit', 'Correction chain exceeds the bounded review limit')
            if root['kind'] == 'operating_charge':
                reservation_id = json.loads(root['metadata']).get('reservation_id')
                reservation = self._reservation(db, reservation_id)
                if reservation['state'] != 'settled':
                    raise EconomyError('reconciliation_required', 'Finalize the provider total before correcting a provisional charge')
            if root['kind'] in ('earned_revenue', 'infrastructure_subsidy'):
                raise EconomyError('noncash_correction', 'Correct performance/subsidy metrics with a signed metric entry, not a cash correction', 422)
            def action():
                eid = self._entry(db, run_id, 'correction', amount_micro_usd, entry_id, {'reason': reason, 'corrects_entry_id': entry_id})
                self._event(db, run_id, 'economy.corrected', {'entry_id': eid, 'original_entry_id': entry_id, 'amount_micro_usd': amount_micro_usd, 'reason': reason})
                return {'entry_id': eid}
            return self._operation(db, run_id, 'correction', idempotency_key, [entry_id, amount_micro_usd, reason], action)

    def _payment(self, db, provider, account, mode, payment_id):
        for value in (provider, account, payment_id):
            ident(value)
        row = _row(db.execute('SELECT * FROM payments WHERE provider=? AND account=? AND mode=? AND payment_id=?', (provider, account, mode, payment_id)).fetchone())
        if row is None:
            raise EconomyError('not_found', 'Payment not registered', 404)
        return row

    def payment(self, provider, account, mode, payment_id):
        with self._read() as db:
            return self._payment(db, provider, account, mode, payment_id)

    def payment_snapshot(self, provider, account, mode, payment_id, reservation_id=None):
        """Authoritative local accounting projection; no webhook/model amounts trusted."""
        with self._read() as db:
            payment = self._payment(db, provider, account, mode, payment_id)
            values = list(db.execute('SELECT kind,amount_micro_usd,fee_micro_usd,net_micro_usd FROM payment_transactions WHERE provider=? AND account=? AND mode=? AND payment_id=?', (provider, account, mode, payment_id)))
            # Only posted monetary components count as settled receipts. Pending
            # provider objects cannot establish spendable settlement.
            components = list(db.execute('SELECT t.kind,c.component,c.amount_micro_usd FROM payment_components c JOIN payment_transactions t USING(provider,account,mode,transaction_id) WHERE t.provider=? AND t.account=? AND t.mode=? AND t.payment_id=?', (provider,account,mode,payment_id)))
            receipts = sum(row['amount_micro_usd'] for row in components if row['kind']=='receipt' and row['component']=='gross')
            refunds = -sum(min(0,row['amount_micro_usd']) for row in components if row['kind'] in ('refund','chargeback') and row['component']=='gross')
            fees = -sum(row['amount_micro_usd'] for row in components if row['component']=='fee')
            earned = 0
            for entry in db.execute("SELECT metadata FROM ledger WHERE run_id=? AND category='earned_revenue'", (payment['run_id'],)):
                metadata=json.loads(entry['metadata'])
                if metadata.get('job_id')==payment['job_id']:
                    earned+=metadata['reported_micro_usd']
            cost, final = 0, False
            if reservation_id is not None:
                reservation=self._reservation(db,reservation_id)
                if reservation['run_id']!=payment['run_id']:
                    raise EconomyError('provider_mismatch','Reservation belongs to another run')
                final=reservation['state']=='settled'
                cost=reservation['actual_micro_usd'] if final else reservation['provisional_micro_usd']
            revision=db.execute('SELECT COALESCE(MAX(sequence),0) FROM events WHERE run_id=?',(payment['run_id'],)).fetchone()[0]
            return {'run_id':payment['run_id'],'job_id':payment['job_id'],'payment_id':payment_id,'revision':revision,'provider_revision':payment['revision'],'mode':mode,'status':payment['status'],'currency':'USD','receipts_micro_usd':receipts,'refunds_micro_usd':refunds,'fees_micro_usd':fees,'earned_micro_usd':earned,'operating_cost_micro_usd':cost,'costs_final':final,'pending_liability_micro_usd':payment['pending_liability_micro_usd']}

    def payments(self, run_id):
        with self._read() as db:
            self._run(db, run_id)
            return [dict(r) for r in db.execute('SELECT * FROM payments WHERE run_id=? ORDER BY rowid', (run_id,))]

    def _register_payment(self, db, run_id, provider, account, mode, payment_id, job_id):
        for value in (provider, account, payment_id):
            ident(value)
        if job_id is not None:
            ident(job_id)
        if self._run(db, run_id)['mode'] != mode:
            raise EconomyError('mode_mismatch', 'Payment mode differs from run')
        old = db.execute('SELECT * FROM payments WHERE provider=? AND account=? AND mode=? AND payment_id=?', (provider, account, mode, payment_id)).fetchone()
        if old:
            if (old['run_id'], old['job_id']) != (run_id, job_id):
                raise EconomyError('payment_conflict', 'Payment already bound to another run or job')
            return dict(old)
        db.execute('INSERT INTO payments(provider,account,mode,payment_id,run_id,job_id) VALUES(?,?,?,?,?,?)', (provider, account, mode, payment_id, run_id, job_id))
        self._event(db, run_id, 'economy.payment_registered', {'provider': provider, 'account': account, 'payment_id': payment_id, 'job_id': job_id})
        return self._payment(db, provider, account, mode, payment_id)

    def register_payment(self, run_id, provider, account, payment_id, job_id, idempotency_key):
        with self._tx() as db:
            mode = self._run(db, run_id)['mode']
            return self._operation(db, run_id, 'payment_registration', idempotency_key, [provider, account, payment_id, job_id],
                                   lambda: self._register_payment(db, run_id, provider, account, mode, payment_id, job_id))

    @staticmethod
    def _payment_hold_id(provider, account, mode, payment_id):
        return 'payment:' + fingerprint([provider, account, mode, payment_id])

    def _payment_posted_net(self, db, key):
        return sum(r[0] for r in db.execute(
            'SELECT c.amount_micro_usd FROM payment_components c JOIN payment_transactions t '
            'ON c.provider=t.provider AND c.account=t.account AND c.mode=t.mode AND c.transaction_id=t.transaction_id '
            'WHERE t.provider=? AND t.account=? AND t.mode=? AND t.payment_id=?', key))

    def _refresh_payment_hold(self, db, payment, pending=None):
        key = (payment['provider'], payment['account'], payment['mode'], payment['payment_id'])
        net = self._payment_posted_net(db, key)
        amount = max(0, net - payment['released_micro_usd'], payment['pending_liability_micro_usd'] if pending is None else pending)
        self._hold(db, payment['run_id'], self._payment_hold_id(*key), amount, 'customer', 'Unfulfilled customer funds or pending refund/dispute exposure')

    def apply_payment_snapshot(self, snapshot, event_id=None, event_digest=None):
        if not isinstance(snapshot, dict):
            raise EconomyError('invalid_snapshot', 'Expected provider snapshot', 422)
        encode(snapshot)
        key = tuple(snapshot.get(k) for k in ('provider', 'account', 'mode', 'payment_id'))
        for value in (key[0], key[1], key[3]):
            ident(value)
        if event_id is not None:
            ident(event_id); ident(event_digest, 'event_digest')
        transactions = snapshot.get('transactions')
        if not isinstance(transactions, list) or len(transactions) > 2000:
            raise EconomyError('invalid_snapshot', 'Expected at most 2000 provider transactions', 422)
        pending = money(snapshot.get('pending_liability_micro_usd', 0))
        revision = snapshot.get('expected_revision')
        if type(revision) is not int or revision < 0:
            raise EconomyError('invalid_snapshot', 'Expected payment revision required', 422)
        status = snapshot.get('status')
        if status not in ('pending', 'settled', 'partially_refunded', 'refunded', 'disputed', 'failed'):
            raise EconomyError('invalid_snapshot', 'Invalid payment status', 422)
        with self._tx() as db:
            payment = self._payment(db, *key)
            if (snapshot.get('run_id'), snapshot.get('job_id')) != (payment['run_id'], payment['job_id']):
                raise EconomyError('payment_conflict', 'Provider snapshot binding differs from local payment')
            if event_id is not None:
                old = db.execute('SELECT * FROM payment_events WHERE provider=? AND account=? AND mode=? AND event_id=?', (*key[:3], event_id)).fetchone()
                if old:
                    if (old['digest'], old['payment_id']) != (event_digest, key[3]):
                        raise EconomyError('idempotency_conflict', 'Webhook event ID reused with different content')
                    return {'duplicate': True, 'payment': payment}
            if revision != payment['revision']:
                raise EconomyError('stale_snapshot', 'Payment changed during provider read; fetch again')
            seen = set()
            posted = []
            observations = []
            for tx in transactions:
                if not isinstance(tx, dict):
                    raise EconomyError('invalid_snapshot', 'Invalid transaction', 422)
                tid = ident(tx.get('transaction_id'))
                if tid in seen:
                    raise EconomyError('invalid_snapshot', 'Duplicate transaction inside snapshot', 422)
                seen.add(tid)
                gross, fee, net = (money(tx.get(k), signed=True) for k in ('amount_micro_usd', 'fee_micro_usd', 'net_micro_usd'))
                kind = tx.get('kind')
                if tx.get('currency') != 'USD' or type(tx.get('settled')) is not bool or kind not in ('receipt', 'refund', 'chargeback', 'chargeback_reversal', 'adjustment'):
                    raise EconomyError('invalid_snapshot', 'Unsupported transaction currency/type', 422)
                if gross-fee != net or (kind == 'receipt' and gross <= 0) or (kind in ('refund', 'chargeback') and gross > 0):
                    raise EconomyError('invalid_snapshot', 'Inconsistent provider gross/fee/net', 422)
                native = encode(tx.get('native', {}))
                old = db.execute('SELECT * FROM payment_transactions WHERE provider=? AND account=? AND mode=? AND transaction_id=?', (*key[:3], tid)).fetchone()
                expected = (key[3], payment['run_id'], kind, gross, fee, net)
                if old:
                    actual = tuple(old[k] for k in ('payment_id', 'run_id', 'kind', 'amount_micro_usd', 'fee_micro_usd', 'net_micro_usd'))
                    if actual != expected:
                        raise EconomyError('transaction_conflict', 'Immutable provider transaction changed; append a correction')
                else:
                    db.execute('INSERT INTO payment_transactions VALUES(?,?,?,?,?,?,?,?,?,?,?)', (*key[:3], tid, *expected, native))
                observations.append({'transaction_id': tid, 'settled': tx['settled'], 'native': tx.get('native', {})})
                ledger_kind = {'receipt': 'customer_receipt', 'refund': 'refund', 'chargeback': 'chargeback', 'chargeback_reversal': 'chargeback_reversal', 'adjustment': 'payment_adjustment'}[kind]
                meta = {'provider': key[0], 'account': key[1], 'mode': key[2], 'payment_id': key[3], 'transaction_id': tid,
                        'job_id': payment['job_id'], 'gross_micro_usd': gross, 'fee_micro_usd': fee, 'net_micro_usd': net, 'native': tx.get('native', {})}
                # Admit liabilities immediately, but each positive component must be available.
                # A pending refund with a fee rebate debits gross now; rebate posts only on settlement.
                for component, amount, entry_kind in (('gross', gross, ledger_kind), ('fee', -fee, 'processor_fee')):
                    if amount == 0 or (amount > 0 and not tx['settled']):
                        continue
                    exists = db.execute('SELECT 1 FROM payment_components WHERE provider=? AND account=? AND mode=? AND transaction_id=? AND component=?', (*key[:3], tid, component)).fetchone()
                    if exists:
                        continue
                    entry_id = self._entry(db, payment['run_id'], entry_kind, amount, tid, meta)
                    db.execute('INSERT INTO payment_components VALUES(?,?,?,?,?,?,?)', (*key[:3], tid, component, entry_id, amount))
                    if tid not in posted:
                        posted.append(tid)
            db.execute('UPDATE payments SET status=?,pending_liability_micro_usd=?,revision=revision+1 WHERE provider=? AND account=? AND mode=? AND payment_id=?', (status, pending, *key))
            payment = self._payment(db, *key)
            self._refresh_payment_hold(db, payment, pending)
            if event_id is not None:
                db.execute('INSERT INTO payment_events VALUES(?,?,?,?,?,?)', (*key[:3], event_id, event_digest, key[3]))
            summary = {'posted_transaction_ids': posted, 'status': status, 'event_id': event_id, 'pending_liability_micro_usd': pending, 'observations': observations}
            rid = str(uuid.uuid4())
            db.execute('INSERT INTO reconciliations VALUES(?,?,?,?,?,?,?,?)', (rid, payment['run_id'], key[0], key[1], key[3], payment['revision'], now(), encode(summary)))
            self._event(db, payment['run_id'], 'economy.payment_reconciled', {'reconciliation_id': rid, 'payment_id': key[3], **summary})
            return {'duplicate': False, 'payment': payment, 'reconciliation_id': rid}

    def release_payment_hold(self, provider, account, mode, payment_id, idempotency_key):
        with self._tx() as db:
            payment = self._payment(db, provider, account, mode, payment_id)
            key = (provider, account, mode, payment_id)
            def action():
                net = self._payment_posted_net(db, key)
                released = max(payment['released_micro_usd'], net, 0)
                db.execute('UPDATE payments SET released_micro_usd=?,revision=revision+1 WHERE provider=? AND account=? AND mode=? AND payment_id=?', (released, *key))
                # A fulfillment decision cannot release a currently disputed exposure.
                if payment['status'] == 'disputed':
                    raise EconomyError('payment_disputed', 'Reconcile dispute before releasing funds')
                self._refresh_payment_hold(db, self._payment(db, *key))
                self._event(db, payment['run_id'], 'economy.customer_funds_released', {'provider': provider, 'account': account, 'mode': mode, 'payment_id': payment_id, 'released_micro_usd': released})
                return self._payment(db, *key)
            return self._operation(db, payment['run_id'], 'payment_hold_release', idempotency_key, list(key), action)

    def record_checkout(self, run_id, provider, account, mode, request_id, job_id, amount_micro_usd, details):
        for value in (provider, account, request_id):
            ident(value)
        if job_id is not None:
            ident(job_id)
        money(amount_micro_usd, positive=True)
        params = [run_id, job_id, amount_micro_usd, details]
        digest = fingerprint(params)
        with self._tx() as db:
            if self._run(db, run_id)['mode'] != mode:
                raise EconomyError('mode_mismatch', 'Checkout mode differs from run')
            key = (provider, account, mode, request_id)
            old = db.execute('SELECT * FROM checkouts WHERE provider=? AND account=? AND mode=? AND request_id=?', key).fetchone()
            if old:
                if old['fingerprint'] != digest:
                    raise EconomyError('idempotency_conflict', 'Checkout request ID reused differently')
                return self._checkout(db, *key)
            db.execute('INSERT INTO checkouts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', (*key, run_id, job_id, amount_micro_usd, encode(details), digest, None, None, now()))
            self._event(db, run_id, 'economy.checkout_requested', {'request_id': request_id, 'provider': provider, 'account': account, 'job_id': job_id, 'amount_micro_usd': amount_micro_usd})
            return self._checkout(db, *key)

    def checkouts(self, run_id):
        with self._read() as db:
            self._run(db, run_id)
            return [self._checkout(db, row['provider'], row['account'], row['mode'], row['request_id'])
                    for row in db.execute('SELECT * FROM checkouts WHERE run_id=? ORDER BY rowid', (run_id,))]

    def _checkout(self, db, provider, account, mode, request_id):
        row = _row(db.execute('SELECT * FROM checkouts WHERE provider=? AND account=? AND mode=? AND request_id=?', (provider, account, mode, request_id)).fetchone())
        if row is None:
            raise EconomyError('not_found', 'Checkout request not found', 404)
        row['details'] = json.loads(row['details'])
        row['status'] = 'created' if row['payment_id'] else 'pending'
        return row

    def checkout(self, provider, account, mode, request_id):
        with self._read() as db:
            return self._checkout(db, provider, account, mode, request_id)

    def finish_checkout(self, provider, account, mode, request_id, payment_id, url=None):
        ident(payment_id)
        if url is not None and (not isinstance(url, str) or len(url) > 2048 or not url.startswith('https://')):
            raise EconomyError('invalid_checkout_url', 'Expected HTTPS provider checkout URL', 422)
        with self._tx() as db:
            key = (provider, account, mode, request_id)
            old = self._checkout(db, *key)
            if old['payment_id'] and (old['payment_id'] != payment_id or (old['url'] is not None and url is not None and old['url'] != url)):
                raise EconomyError('idempotency_conflict', 'Checkout already completed differently')
            if old['url'] is not None:
                url = old['url']
            self._register_payment(db, old['run_id'], provider, account, mode, payment_id, old['job_id'])
            db.execute('UPDATE checkouts SET payment_id=?,url=? WHERE provider=? AND account=? AND mode=? AND request_id=?', (payment_id, url, *key))
            if old['payment_id'] is None:
                self._event(db, old['run_id'], 'economy.checkout_created', {'provider': provider, 'account': account, 'request_id': request_id, 'payment_id': payment_id})
            return self._checkout(db, *key)

    def reconciliations(self, run_id):
        with self._read() as db:
            self._run(db, run_id)
            result = [dict(r) for r in db.execute('SELECT * FROM reconciliations WHERE run_id=? ORDER BY rowid', (run_id,))]
            for row in result:
                row['summary'] = json.loads(row['summary'])
            return result
