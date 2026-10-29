"""Native free reservations and migration preservation; all charges are fixtures."""
from __future__ import annotations

import multiprocessing
import sqlite3
from concurrent.futures import ProcessPoolExecutor
from contextlib import contextmanager
from pathlib import Path

import pytest

from widemind.economy.reporting import audit_integrity
from widemind.economy.service import EconomyError, EconomyService, MAX_MONEY, encode, now


class VersionFourEconomy(EconomyService):
    """Build the actual previously shipped schema for upgrade qualification."""
    def _migrate(self):
        with self._initial_connection() as connection:
            connection.execute('BEGIN IMMEDIATE')
            try:
                for name in ('001_economy.sql', '002_reconciliation_queue.sql', '003_reconciliation_schedule_index.sql', '004_projection_guards.sql'):
                    text = (Path(__file__).parents[1] / 'src/widemind/economy/migrations' / name).read_text()
                    statement = ''
                    for line in text.splitlines(keepends=True):
                        statement += line
                        if sqlite3.complete_statement(statement):
                            connection.execute(statement)
                            statement = ''
                    assert not statement.strip()
                connection.execute('PRAGMA user_version=4')
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def _start_reservation(self, db, reservation_id):
        # Frozen version-4 dispatch behavior: that shipped schema has no runtime
        # authorization/dispatch tables. This fixture exercises its real rows.
        row = self._reservation(db, reservation_id)
        if row['state'] != 'reserved':
            raise EconomyError('duplicate_dispatch', 'Action already dispatched or closed; reconcile before retry')
        if self._balance(db, row['run_id'])['available_micro_usd'] < 0:
            raise EconomyError('insufficient_funds', 'New liability invalidated this authorization; reconcile or cancel before dispatch')
        db.execute("UPDATE reservations SET state='running',updated_at=? WHERE reservation_id=?", (now(), reservation_id))
        self._event(db, row['run_id'], 'economy.dispatched', {'reservation_id': reservation_id})
        return self._reservation(db, reservation_id)


def reserve(service, action='free', amount=0):
    return service.reserve('run', action, amount, action, 'fixture-provider', 'fixture-account', {'max_duration_ms': 1000})


def record_charge(service, row, amount, *, outcome='succeeded', event='fixture-event'):
    return service.settle(row['reservation_id'], amount, 'fixture-provider', 'fixture-account', event,
                          usage={'evidence_ref': 'fixture-only'}, outcome=outcome)


def zero_process(database, index):
    service = EconomyService(database)
    row = reserve(service, f'free-{index}')
    service.start_reservation(row['reservation_id'])
    record_charge(service, row, 0, event=f'fixture-event-{index}')
    return service.reservation(row['reservation_id'])['amount_micro_usd']


def rows(connection, table):
    return connection.execute(f'SELECT rowid,* FROM {table} ORDER BY rowid').fetchall()


def version_four_fixture(path):
    service = VersionFourEconomy(path)
    service.create_run('run')
    reserve(service, 'reserved', 1_000_000)
    settled = reserve(service, 'settled', 100_000)
    service.start_reservation(settled['reservation_id'])
    record_charge(service, settled, 20_000, event='settled-event')
    unknown = reserve(service, 'unknown', 100_000)
    service.start_reservation(unknown['reservation_id'])
    record_charge(service, unknown, 10_000, outcome='unknown', event='unknown-event')
    cancelled = reserve(service, 'cancelled', 50_000)
    service.cancel_reservation(cancelled['reservation_id'])
    return service


def test_native_zero_cap_is_exact_and_does_not_spend_or_grant(tmp_path):
    service = EconomyService(tmp_path / 'zero.sqlite')
    service.create_run('run')
    row = reserve(service)
    assert row['amount_micro_usd'] == 0
    assert service.balance('run')['posted_micro_usd'] == 5_000_000
    assert service.balance('run')['reserved_micro_usd'] == 0
    service.start_reservation(row['reservation_id'])
    record_charge(service, row, 0)
    assert service.reservation(row['reservation_id'])['state'] == 'settled'
    assert service.reservation(row['reservation_id'])['actual_micro_usd'] == 0
    assert service.balance('run')['posted_micro_usd'] == 5_000_000
    assert [entry['amount_micro_usd'] for entry in service.ledger('run')] == [5_000_000, 0]
    assert audit_integrity(service, 'run')['ok'] is True


def test_zero_is_allowed_in_deficit_while_one_micro_paid_action_is_blocked(tmp_path):
    service = EconomyService(tmp_path / 'zero.sqlite')
    service.create_run('run')
    prior = reserve(service, 'liability', 1)
    service.start_reservation(prior['reservation_id'])
    record_charge(service, prior, 6_000_000)
    assert service.balance('run')['posted_micro_usd'] == -1_000_000
    row = reserve(service)
    service.start_reservation(row['reservation_id'])
    record_charge(service, row, 0, event='free-event')
    assert service.balance('run')['posted_micro_usd'] == -1_000_000
    with pytest.raises(EconomyError) as error:
        reserve(service, 'paid', 1)
    assert error.value.code == 'insufficient_funds'
    assert audit_integrity(service, 'run')['ok'] is True


def test_zero_remains_a_real_authorization_and_posts_unexpected_actual_liability(tmp_path):
    service = EconomyService(tmp_path / 'zero.sqlite')
    service.create_run('run')
    row = reserve(service)
    assert reserve(service)['reservation_id'] == row['reservation_id']
    service.start_reservation(row['reservation_id'])
    with pytest.raises(EconomyError) as error:
        service.start_reservation(row['reservation_id'])
    assert error.value.code == 'duplicate_dispatch'
    record_charge(service, row, 6_000_001, outcome='failed')
    record_charge(service, row, 6_000_001, outcome='failed')
    balance = service.balance('run')
    assert balance['posted_micro_usd'] == -1_000_001
    assert balance['reserved_micro_usd'] == 0
    assert balance['exhausted'] is True
    with pytest.raises(EconomyError) as error:
        reserve(service, 'paid', 1)
    assert error.value.code == 'insufficient_funds'
    assert len(service.charges('run')) == 1
    assert audit_integrity(service, 'run')['ok'] is True


def test_free_unknown_survives_restart_and_cannot_be_blindly_redispatched(tmp_path):
    path = tmp_path / 'zero.sqlite'
    service = EconomyService(path)
    service.create_run('run')
    row = reserve(service)
    service.start_reservation(row['reservation_id'])
    record_charge(service, row, None, outcome='unknown')
    restarted = EconomyService(path)
    assert restarted.reservation(row['reservation_id'])['state'] == 'unknown'
    assert restarted.reservation(row['reservation_id'])['amount_micro_usd'] == 0
    with pytest.raises(EconomyError):
        restarted.start_reservation(row['reservation_id'])
    with pytest.raises(EconomyError):
        restarted.cancel_reservation(row['reservation_id'])
    record_charge(restarted, row, 100, outcome='failed')
    assert restarted.balance('run')['posted_micro_usd'] == 4_999_900


def test_zero_cap_separate_processes_do_not_create_fake_money(tmp_path):
    path = str(tmp_path / 'zero.sqlite')
    EconomyService(path).create_run('run')
    with ProcessPoolExecutor(max_workers=4, mp_context=multiprocessing.get_context('spawn')) as executor:
        amounts = list(executor.map(zero_process, [path] * 8, range(8)))
    assert amounts == [0] * 8
    service = EconomyService(path)
    assert service.balance('run')['posted_micro_usd'] == 5_000_000
    assert len(service.reservations('run')) == 8
    assert all(row['amount_micro_usd'] == row['actual_micro_usd'] == 0 and row['state'] == 'settled'
               for row in service.reservations('run'))
    assert audit_integrity(service, 'run')['ok'] is True


def test_v4_upgrade_preserves_all_existing_rows_references_and_guards(tmp_path):
    path = tmp_path / 'upgrade.sqlite'
    old = version_four_fixture(path)
    before_balance = old.balance('run')
    with sqlite3.connect(path) as connection:
        tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        before = {table: rows(connection, table) for table in tables}
        guards = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='reservations'")}
    upgraded = EconomyService(path)
    assert upgraded.balance('run') == before_balance
    with upgraded._read() as connection:
        assert connection.execute('PRAGMA user_version').fetchone()[0] == 5
        assert connection.execute('PRAGMA foreign_keys').fetchone()[0] == 1
        assert connection.execute('PRAGMA foreign_key_check').fetchall() == []
        for table in tables:
            assert [tuple(row) for row in rows(connection, table)] == before[table]
        current_guards = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='reservations'")}
        assert current_guards == guards
    free = reserve(upgraded)
    assert free['amount_micro_usd'] == 0
    assert audit_integrity(upgraded, 'run')['ok'] is True
    with upgraded._tx() as connection:
        with pytest.raises(sqlite3.IntegrityError, match='immutable accounting authorization'):
            connection.execute("UPDATE reservations SET amount_micro_usd=1 WHERE reservation_id=?", (free['reservation_id'],))
        with pytest.raises(sqlite3.IntegrityError, match='identities cannot be deleted'):
            connection.execute('DELETE FROM reservations WHERE reservation_id=?', (free['reservation_id'],))
        with pytest.raises(sqlite3.IntegrityError, match='invalid accounting projection'):
            connection.execute('UPDATE reservations SET provisional_micro_usd=-1 WHERE reservation_id=?', (free['reservation_id'],))
        with pytest.raises(sqlite3.IntegrityError, match='immutable accounting authorization'):
            connection.execute('INSERT OR REPLACE INTO reservations SELECT * FROM reservations WHERE reservation_id=?', (free['reservation_id'],))


def test_migration_failure_rolls_back_rebuild_and_restores_fk_enforcement(tmp_path, monkeypatch):
    path = tmp_path / 'upgrade.sqlite'
    old = version_four_fixture(path)
    before = old.reservations('run')
    real_read = Path.read_text
    def broken_read(target, *args, **kwargs):
        value = real_read(target, *args, **kwargs)
        return value + '\nINVALID_MIGRATION_STATEMENT;\n' if target.name == '005_runtime_bridge.sql' else value
    monkeypatch.setattr(Path, 'read_text', broken_read)
    connection = old._connect()
    @contextmanager
    def retained_connection():
        yield connection
    monkeypatch.setattr(old, '_initial_connection', retained_connection)
    try:
        with pytest.raises(sqlite3.OperationalError):
            old._migrate()
        assert connection.execute('PRAGMA foreign_keys').fetchone()[0] == 1
        assert connection.execute('PRAGMA user_version').fetchone()[0] == 4
        assert connection.in_transaction is False
        assert old.reservations('run') == before
        assert connection.execute("SELECT 1 FROM sqlite_master WHERE name='runtime_authorizations'").fetchone() is None
        assert connection.execute("SELECT 1 FROM sqlite_master WHERE name='reservations_v5'").fetchone() is None
    finally:
        connection.close()


def test_upgrade_foreign_key_check_rejects_preexisting_orphan_without_committing(tmp_path):
    path = tmp_path / 'upgrade.sqlite'
    old = version_four_fixture(path)
    with sqlite3.connect(path) as connection:
        connection.execute("INSERT INTO charges VALUES('fixture-orphan','fixture-account','test','orphan-event','missing-reservation',1,'{}','failed')")
    with pytest.raises(EconomyError) as error:
        EconomyService(path)
    assert error.value.code == 'invalid_migration'
    with sqlite3.connect(path) as connection:
        assert connection.execute('PRAGMA user_version').fetchone()[0] == 4
        assert connection.execute("SELECT 1 FROM sqlite_master WHERE name='runtime_authorizations'").fetchone() is None
    assert old.balance('run')['posted_micro_usd'] == 4_970_000


def test_runtime_binding_is_immutable_and_must_match_reservation(tmp_path):
    service = EconomyService(tmp_path / 'zero.sqlite')
    service.create_run('run')
    row = reserve(service)
    binding = (row['reservation_id'], 'run', 'free', 'local.fixture', encode({'maximum': {'currency': 'USD', 'micros': '0'}}),
               'fixture-provider', 'fixture-account', 'test', 'fixture-fingerprint', row['created_at'])
    with service._tx() as connection:
        with pytest.raises(sqlite3.IntegrityError, match='does not match accounting'):
            connection.execute('INSERT INTO runtime_authorizations VALUES(?,?,?,?,?,?,?,?,?,?)',
                               (*binding[:2], 'wrong-action', *binding[3:]))
        connection.execute('INSERT INTO runtime_authorizations VALUES(?,?,?,?,?,?,?,?,?,?)', binding)
        for statement in ('UPDATE runtime_authorizations SET tool=\'other\'', 'DELETE FROM runtime_authorizations',
                          'INSERT OR REPLACE INTO runtime_authorizations SELECT * FROM runtime_authorizations'):
            with pytest.raises(sqlite3.IntegrityError, match='immutable runtime authorization'):
                connection.execute(statement)
    assert audit_integrity(service, 'run')['ok'] is True


def test_internal_reserve_and_runtime_binding_are_one_rollback_unit(tmp_path):
    service = EconomyService(tmp_path / 'zero.sqlite')
    service.create_run('run')
    with pytest.raises(sqlite3.IntegrityError):
        with service._tx() as connection:
            row = service._reserve(connection, 'run', 'free', 0, 'free', 'fixture-provider', 'fixture-account', {'max_duration_ms': 1000})
            connection.execute('INSERT INTO runtime_authorizations VALUES(?,?,?,?,?,?,?,?,?,?)',
                               (row['reservation_id'], 'run', 'wrong-action', 'local.fixture', '{}', 'fixture-provider', 'fixture-account', 'test', 'fingerprint', row['created_at']))
    assert service.reservations('run') == []
    assert len(service.ledger('run')) == len(service.events('run')) == 1


def test_signed_64_bit_amounts_are_exact_above_javascript_safe_integer(tmp_path):
    service = EconomyService(tmp_path / 'zero.sqlite')
    service.create_run('run')
    amount = 9_007_199_254_740_993
    service.record_funding('run', amount, 'owner_funding', 'fixture-large-owner-funding', 'fund-large')
    row = reserve(service, 'large-action', amount)
    assert row['amount_micro_usd'] == amount
    service.start_reservation(row['reservation_id'])
    record_charge(service, row, amount, event='large-fixture-charge')
    assert service.reservation(row['reservation_id'])['actual_micro_usd'] == amount
    assert service.balance('run')['posted_micro_usd'] == 5_000_000
    assert MAX_MONEY == 9_223_372_036_854_775_807
    with pytest.raises(EconomyError):
        service.record_funding('run', MAX_MONEY + 1, 'owner_funding', 'too-large', 'too-large')


def test_internal_settle_and_receipt_admission_are_one_rollback_unit(tmp_path):
    service = EconomyService(tmp_path / 'receipt.sqlite')
    service.create_run('run')
    row = reserve(service, amount=1_000_000)
    service.start_reservation(row['reservation_id'])
    before = service.balance('run')
    with pytest.raises(sqlite3.IntegrityError, match='does not match accounting'):
        with service._tx() as connection:
            service._settle(connection, row['reservation_id'], 300_000, 'fixture-provider', 'fixture-account',
                            'rt:' + row['reservation_id'], {'actual_provider_event_id': 'fixture-event'})
            connection.execute('INSERT INTO runtime_receipts VALUES(?,?,?,?,?,?,?,?)',
                               ('wrong-provider', 'fixture-account', 'test', 'fixture-event', row['reservation_id'], 'fingerprint', '{"kind":"settled"}', row['created_at']))
    assert service.balance('run') == before
    assert service.charges('run') == []
    assert service.reservation(row['reservation_id'])['state'] == 'running'
    with service._tx() as connection:
        result = service._settle(connection, row['reservation_id'], 300_000, 'fixture-provider', 'fixture-account',
                                 'rt:' + row['reservation_id'], {'actual_provider_event_id': 'fixture-event'})
        assert result['state'] == 'settled'
        connection.execute('INSERT INTO runtime_receipts VALUES(?,?,?,?,?,?,?,?)',
                           ('fixture-provider', 'fixture-account', 'test', 'fixture-event', row['reservation_id'], 'fingerprint', '{"kind":"settled"}', row['created_at']))
    assert service.balance('run')['posted_micro_usd'] == 4_700_000
    with service._tx() as connection:
        for statement in ('UPDATE runtime_receipts SET event_id=\'other\'', 'DELETE FROM runtime_receipts',
                          'INSERT OR REPLACE INTO runtime_receipts SELECT * FROM runtime_receipts'):
            with pytest.raises(sqlite3.IntegrityError, match='immutable runtime receipt'):
                connection.execute(statement)
    assert audit_integrity(service, 'run')['ok'] is True


def test_internal_settle_preserves_provisional_to_final_cost_true_up(tmp_path):
    service = EconomyService(tmp_path / 'receipt.sqlite')
    service.create_run('run')
    row = reserve(service, amount=1_000_000)
    service.start_reservation(row['reservation_id'])
    stable_charge_id = 'rt:' + row['reservation_id']
    with service._tx() as connection:
        service._settle(connection, row['reservation_id'], 250_000, 'fixture-provider', 'fixture-account',
                        stable_charge_id, {'actual_provider_event_id': 'provisional-event'}, 'unknown')
        connection.execute('INSERT INTO runtime_receipts VALUES(?,?,?,?,?,?,?,?)',
                           ('fixture-provider', 'fixture-account', 'test', 'provisional-event', row['reservation_id'], 'subtotal-fingerprint', '{"kind":"unavailable"}', row['created_at']))
    assert service.balance('run')['posted_micro_usd'] == 4_750_000
    assert service.balance('run')['reserved_micro_usd'] == 750_000
    with service._tx() as connection:
        service._settle(connection, row['reservation_id'], 400_000, 'fixture-provider', 'fixture-account',
                        stable_charge_id, {'actual_provider_event_id': 'final-event'})
        connection.execute('INSERT INTO runtime_receipts VALUES(?,?,?,?,?,?,?,?)',
                           ('fixture-provider', 'fixture-account', 'test', 'final-event', row['reservation_id'], 'final-fingerprint', '{"kind":"settled"}', row['created_at']))
    assert service.balance('run')['posted_micro_usd'] == 4_600_000
    assert service.balance('run')['reserved_micro_usd'] == 0
    assert service.reservation(row['reservation_id'])['actual_micro_usd'] == 400_000
    assert audit_integrity(service, 'run')['ok'] is True


def bind_runtime(service, row):
    with service._tx() as connection:
        connection.execute('INSERT INTO runtime_authorizations VALUES(?,?,?,?,?,?,?,?,?,?)',
                           (row['reservation_id'], row['run_id'], row['action_id'], 'local.fixture', '{}',
                            row['provider'], row['account'], 'test', 'fixture-fingerprint', row['created_at']))


def record_definitive_not_performed(service, row, ordinal):
    # Fixture for the trusted runtime adapter's atomic rearm path. No external
    # effect or charge is claimed. Parent bridge performs the evidence checks.
    with service._tx() as connection:
        connection.execute('INSERT INTO runtime_rearms VALUES(?,?,?,?,?)',
                           (row['reservation_id'], ordinal, f'provider-evidence-{ordinal}',
                            f'fingerprint-{ordinal}', row['created_at']))
        connection.execute("UPDATE reservations SET state='reserved' WHERE reservation_id=?", (row['reservation_id'],))
        service._event(connection, row['run_id'], 'economy.runtime_not_performed',
                       {'reservation_id': row['reservation_id'], 'evidence_ref': f'provider-evidence-{ordinal}'})


def test_runtime_dispatch_limit_is_enforced_by_all_core_start_paths(tmp_path):
    service = EconomyService(tmp_path / 'dispatch.sqlite')
    service.create_run('run')
    row = reserve(service)
    bind_runtime(service, row)
    for ordinal in range(1, 4):
        service.start_reservation(row['reservation_id'])
        with service._read() as connection:
            dispatches = connection.execute('SELECT ordinal FROM runtime_dispatches WHERE reservation_id=? ORDER BY ordinal',
                                            (row['reservation_id'],)).fetchall()
            assert [entry[0] for entry in dispatches] == list(range(1, ordinal + 1))
        record_definitive_not_performed(service, row, ordinal)
        assert audit_integrity(service, 'run')['ok'] is True
    with pytest.raises(EconomyError) as error:
        service.start_reservation(row['reservation_id'])
    assert error.value.code == 'dispatch_limit'
    assert service.reservation(row['reservation_id'])['state'] == 'reserved'
    assert service.balance('run')['posted_micro_usd'] == 5_000_000
    with service._read() as connection:
        assert connection.execute('SELECT count(*) FROM runtime_dispatches').fetchone()[0] == 3


def test_runtime_dispatch_and_state_change_share_one_transaction(tmp_path):
    service = EconomyService(tmp_path / 'dispatch.sqlite')
    service.create_run('run')
    row = reserve(service)
    bind_runtime(service, row)
    with pytest.raises(sqlite3.IntegrityError):
        with service._tx() as connection:
            service._start_reservation(connection, row['reservation_id'])
            connection.execute('INSERT INTO runtime_dispatches VALUES(?,?,?,?)',
                               ('invalid-fourth', row['reservation_id'], 4, row['created_at']))
    assert service.reservation(row['reservation_id'])['state'] == 'reserved'
    with service._read() as connection:
        assert connection.execute('SELECT count(*) FROM runtime_dispatches').fetchone()[0] == 0
    service.start_reservation(row['reservation_id'])
    assert service.reservation(row['reservation_id'])['state'] == 'running'
    with service._read() as connection:
        assert connection.execute('SELECT ordinal FROM runtime_dispatches').fetchone()[0] == 1


def test_runtime_dispatch_rearm_history_is_immutable_and_evidence_not_reusable(tmp_path):
    service = EconomyService(tmp_path / 'dispatch.sqlite')
    service.create_run('run')
    row = reserve(service)
    bind_runtime(service, row)
    service.start_reservation(row['reservation_id'])
    record_definitive_not_performed(service, row, 1)
    service.start_reservation(row['reservation_id'])
    with service._tx() as connection:
        for table, message in (('runtime_dispatches', 'immutable runtime dispatch'), ('runtime_rearms', 'immutable runtime rearm')):
            for statement in (f'UPDATE {table} SET created_at=\'modified\'', f'DELETE FROM {table}',
                              f'INSERT OR REPLACE INTO {table} SELECT * FROM {table}'):
                with pytest.raises(sqlite3.IntegrityError, match=message):
                    connection.execute(statement)
        with pytest.raises(sqlite3.IntegrityError, match='immutable runtime rearm'):
            connection.execute('INSERT INTO runtime_rearms VALUES(?,?,?,?,?)',
                               (row['reservation_id'], 2, 'provider-evidence-1', 'other-fingerprint', row['created_at']))
        with pytest.raises(sqlite3.IntegrityError, match='FOREIGN KEY constraint failed'):
            connection.execute('INSERT INTO runtime_rearms VALUES(?,?,?,?,?)',
                               (row['reservation_id'], 3, 'unperformed-dispatch', 'fingerprint-3', row['created_at']))


def test_legacy_nonruntime_reservation_does_not_create_runtime_dispatch_records(tmp_path):
    service = EconomyService(tmp_path / 'dispatch.sqlite')
    service.create_run('run')
    row = reserve(service)
    service.start_reservation(row['reservation_id'])
    record_charge(service, row, 0)
    with service._read() as connection:
        assert connection.execute('SELECT count(*) FROM runtime_dispatches').fetchone()[0] == 0
