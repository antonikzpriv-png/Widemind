"""Cross-protocol SQLite isolation. All receipts are explicitly synthetic."""
import importlib.util
from pathlib import Path
import sqlite3
import sys
import types

import pytest

ROOT = next(parent for parent in Path(__file__).resolve().parents
            if (parent / 'src/widemind/economy/migrations/005_runtime_bridge.sql').exists())


def load_copy(directory, name):
    package = types.ModuleType(name)
    package.__path__ = [str(directory)]
    sys.modules[name] = package
    spec = importlib.util.spec_from_file_location(name + '.service', directory / 'service.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope='module')
def copies():
    return (load_copy(ROOT / 'src/widemind/economy', '_family_test_central'),
            load_copy(ROOT / 'services/economy/src/widemind/economy', '_family_test_service'))


def funded(module, path, provider):
    service = module.EconomyService(path)
    service.create_run('synthetic-run')
    service.register_payment('synthetic-run', provider, 'fixture-account', 'fixture-payment', 'fixture-job', 'binding')
    service.apply_payment_snapshot(dict(provider=provider, account='fixture-account', mode='test', payment_id='fixture-payment',
        run_id='synthetic-run', job_id='fixture-job', status='settled', expected_revision=0, transactions=[dict(
            transaction_id='fixture-receipt', kind='receipt', amount_micro_usd=2_000_000, fee_micro_usd=100_000,
            net_micro_usd=1_900_000, currency='USD', settled=True, native={'evidence': 'synthetic fixture only'})]))
    service.release_payment_hold(provider, 'fixture-account', 'test', 'fixture-payment', 'actual-fixture-fulfillment')
    return service


def files(directory):
    # SHM contains volatile reader locks, not financial pages; DB and WAL must
    # remain byte-identical even while an existing writer keeps its WAL open.
    return {path.name: path.read_bytes() for path in directory.iterdir() if path.is_file()
            and not path.name.endswith('-shm') and not (path.name.endswith('-wal') and path.stat().st_size == 0)}


def remove_marker(path):
    with sqlite3.connect(path) as db:
        db.execute('DROP TABLE economy_database_family')


@pytest.mark.parametrize('source,target,provider', [(0, 1, 'stripe'), (1, 0, 'paypal')])
@pytest.mark.parametrize('marked', [True, False])
def test_actual_cash_cannot_cross_families_and_rejection_never_changes_files(tmp_path, copies, source, target, provider, marked):
    path = tmp_path / 'cash.sqlite3'
    original = funded(copies[source], path, provider)
    assert original.balance('synthetic-run')['available_micro_usd'] == 6_900_000
    if not marked:
        remove_marker(path)
    before = files(tmp_path)
    with pytest.raises(copies[target].EconomyError) as error:
        copies[target].EconomyService(path)
    assert error.value.code == ('future_schema' if source == 1 else 'database_family_mismatch')
    assert files(tmp_path) == before
    if source == 1 and not marked:
        with pytest.raises(copies[source].EconomyError) as own_error:
            copies[source].EconomyService(path)
        assert own_error.value.code == 'database_family_mismatch'
    else:
        assert copies[source].EconomyService(path).balance('synthetic-run')['available_micro_usd'] == 6_900_000


@pytest.mark.parametrize('index,family', [(0, 'central-v1'), (1, 'service-v1')])
def test_new_and_unmarked_owned_v5_restarts_preserve_cash_and_pin_immutable_family(tmp_path, copies, index, family):
    path = tmp_path / 'owned.sqlite3'
    service = funded(copies[index], path, 'stripe' if index == 0 else 'paypal')
    before = service.ledger('synthetic-run')
    if index == 1:
        # Preserve the legacy unmarked-v5 adoption case. A current service-v6
        # database must retain its immutable ownership marker instead.
        with sqlite3.connect(path) as db:
            db.execute('DROP TABLE research_reservation_children')
            db.execute('PRAGMA user_version=5')
    remove_marker(path)
    restored = copies[index].EconomyService(path)
    assert restored.ledger('synthetic-run') == before
    assert copies[index].EconomyService(path).balance('synthetic-run')['posted_micro_usd'] == 6_900_000
    with sqlite3.connect(path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == (5 if index == 0 else 6)
        assert db.execute('SELECT family FROM economy_database_family').fetchone()[0] == family
        for sql in ["UPDATE economy_database_family SET family='service-v1'", 'DELETE FROM economy_database_family',
                    "INSERT OR REPLACE INTO economy_database_family VALUES(1,'central-v1')",
                    "INSERT INTO economy_database_family VALUES(2,'central-v1')"]:
            with pytest.raises(sqlite3.IntegrityError):
                db.execute(sql)


@pytest.mark.parametrize('index', [0, 1])
def test_common_unmarked_legacy_format_is_explicitly_pinned_without_reseeding(tmp_path, copies, index):
    path = tmp_path / 'legacy.sqlite3'
    migrations = ROOT / 'src/widemind/economy/migrations'
    with sqlite3.connect(path) as db:
        for name in ['001_economy.sql', '002_reconciliation_queue.sql', '003_reconciliation_schedule_index.sql', '004_projection_guards.sql']:
            db.executescript((migrations / name).read_text())
        db.execute('PRAGMA user_version=4')
        db.execute("INSERT INTO runs VALUES('legacy','test','2026-10-29T00:00:00Z')")
        db.execute("INSERT INTO ledger VALUES('fixture-opening','legacy','opening_grant','opening_grant',5000000,'fixture','{}','2026-10-29T00:00:00Z')")
    chosen = copies[index].EconomyService(path)
    assert chosen.balance('legacy')['posted_micro_usd'] == 5_000_000
    assert len(chosen.ledger('legacy')) == 1
    with pytest.raises(copies[1-index].EconomyError) as error:
        copies[1-index].EconomyService(path)
    assert error.value.code == ('future_schema' if index == 1 else 'database_family_mismatch')


@pytest.mark.parametrize('index', [0, 1])
def test_partial_or_ambiguous_v5_footprints_fail_without_adoption(tmp_path, copies, index):
    path = tmp_path / 'ambiguous.sqlite3'
    funded(copies[0], path, 'stripe')
    remove_marker(path)
    with sqlite3.connect(path) as db:
        for name in ['runtime_rearms', 'runtime_dispatches', 'runtime_receipts']:
            db.execute('DROP TABLE ' + name)
    before = files(tmp_path)
    with pytest.raises(copies[index].EconomyError) as error:
        copies[index].EconomyService(path)
    assert error.value.code == 'database_family_mismatch'
    assert files(tmp_path) == before


@pytest.mark.parametrize('index', [0, 1])
def test_existing_service_instance_refuses_a_replaced_foreign_database(tmp_path, copies, index):
    path, replacement = tmp_path / 'active.sqlite3', tmp_path / 'replacement.sqlite3'
    existing = funded(copies[index], path, 'stripe' if index == 0 else 'paypal')
    funded(copies[1-index], replacement, 'paypal' if index == 0 else 'stripe')
    replacement.replace(path)
    before = files(tmp_path)
    with pytest.raises(copies[index].EconomyError) as error:
        existing.balance('synthetic-run')
    assert error.value.code == 'database_family_mismatch'
    assert files(tmp_path) == before


@pytest.mark.parametrize('index', [0, 1])
@pytest.mark.parametrize('dummies', [False, True])
def test_incomplete_v5_tables_are_not_adopted_or_rewritten(tmp_path, copies, index, dummies):
    path = tmp_path / 'incomplete.sqlite3'
    db = sqlite3.connect(path)
    try:
        db.execute('PRAGMA user_version=5')
        if dummies:
            for name in ['runtime_authorizations', 'runtime_receipts', 'runtime_dispatches', 'runtime_rearms']:
                db.execute('CREATE TABLE ' + name + '(dummy TEXT)')
        db.commit()
    finally:
        db.close()
    before = files(tmp_path)
    with pytest.raises(copies[index].EconomyError) as error:
        copies[index].EconomyService(path)
    assert error.value.code == 'database_family_mismatch'
    assert files(tmp_path) == before


def test_unmarked_service_v6_is_not_adopted_by_either_family(tmp_path, copies):
    path = tmp_path / 'unmarked-v6.sqlite3'
    funded(copies[1], path, 'paypal')
    remove_marker(path)
    before = files(tmp_path)
    for module in copies:
        with pytest.raises(module.EconomyError):
            module.EconomyService(path)
        assert files(tmp_path) == before
