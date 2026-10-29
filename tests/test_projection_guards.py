"""Database checks protect accounting identities independently of HTTP/model instructions."""
import sqlite3
from pathlib import Path

import pytest

from widemind.economy.service import EconomyService


@pytest.fixture
def service(tmp_path):
    service=EconomyService(tmp_path/'accounting.sqlite3')
    service.create_run('run')
    service.reserve('run','action',4_000_000,'reserve','fixture','account',{'max_tokens':100})
    service.register_payment('run','stripe','acct_fixture','cs_fixture','job','register')
    service.record_checkout('run','stripe','acct_fixture','test','request','job',1_000_000,{})
    return service


@pytest.mark.parametrize('sql',[
    "UPDATE runs SET mode='live'",
    "UPDATE reservations SET amount_micro_usd=1000000",
    "UPDATE reservations SET provider='other'",
    "UPDATE reservations SET limits_json='{}'",
    "UPDATE payments SET run_id='other'",
    "UPDATE payments SET released_micro_usd='bad'",
    "UPDATE payments SET pending_liability_micro_usd=-1",
    "UPDATE payments SET revision=1.5",
    "UPDATE reservations SET provisional_micro_usd=-1",
    "UPDATE reservations SET actual_micro_usd='bad'",
    "UPDATE checkouts SET amount_micro_usd=5000000",
    "DELETE FROM reservations",
    "DELETE FROM payments",
    "DELETE FROM runs",
    "INSERT OR REPLACE INTO runs SELECT * FROM runs",
    "INSERT OR REPLACE INTO reservations SELECT * FROM reservations",
    "INSERT OR REPLACE INTO payments SELECT * FROM payments",
    "INSERT OR REPLACE INTO checkouts SELECT * FROM checkouts",
])
def test_authorization_or_projection_corruption_is_rejected(service,sql):
    with sqlite3.connect(service.path) as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(sql)
    assert service.balance('run')['available_micro_usd'] == 1_000_000


def test_failed_incremental_migration_rolls_back_schema_and_preserves_v1_money(tmp_path,monkeypatch):
    path=tmp_path/'legacy.sqlite3'
    migration=Path(__file__).parents[1]/'src/widemind/economy/migrations/001_economy.sql'
    with sqlite3.connect(path) as db:
        db.executescript(migration.read_text())
        db.execute('PRAGMA user_version=1')
        db.execute("INSERT INTO runs VALUES('run','test','2026-10-28T00:00:00Z')")
        db.execute("INSERT INTO ledger VALUES('opening','run','opening_grant','opening_grant',5000000,'run:run','{}','2026-10-28T00:00:00Z')")
    original=Path.read_text
    def broken_migration(self,*args,**kwargs):
        text=original(self,*args,**kwargs)
        return text+'\nSELECT nonexistent_migration_function();\n' if self.name=='002_reconciliation_queue.sql' else text
    with monkeypatch.context() as patch:
        patch.setattr(Path,'read_text',broken_migration)
        with pytest.raises(sqlite3.OperationalError):
            EconomyService(path)
    with sqlite3.connect(path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 1
        assert db.execute("SELECT name FROM sqlite_master WHERE name='reconciliation_jobs'").fetchone() is None
        assert db.execute('SELECT amount_micro_usd FROM ledger').fetchone()[0] == 5_000_000
    assert EconomyService(path).balance('run')['posted_micro_usd'] == 5_000_000
