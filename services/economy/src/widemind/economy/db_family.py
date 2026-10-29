"""Pin the independently versioned central/service accounting protocols.

Versions 1--4 predate the fork and have a common format. Opening such an
unmarked database explicitly chooses its owner family, atomically with upgrade.
Unmarked version 5 must have that family's complete, unambiguous footprint.
Neither user_version=5 alone nor matching cash tables permits conversion.
Service-v1 schema 6 additionally requires its existing immutable family marker
and complete research-child allocation footprint; it is never adopted unmarked.
"""
from __future__ import annotations

import sqlite3

TABLE = 'economy_database_family'
RUNTIME_TABLES = frozenset({'runtime_authorizations', 'runtime_receipts', 'runtime_dispatches', 'runtime_rearms'})
TRIGGERS = ('economy_family_no_update', 'economy_family_no_delete', 'economy_family_no_replace')
RESEARCH_TABLE = 'research_reservation_children'
RESEARCH_TRIGGERS = ('research_children_no_update', 'research_children_no_delete', 'research_children_no_replace', 'research_children_binding')
BASE_COLUMNS = {
    'runs': {'run_id', 'mode', 'created_at'},
    'ledger': {'entry_id', 'run_id', 'kind', 'category', 'amount_micro_usd', 'reference', 'metadata'},
    'reservations': {'reservation_id', 'run_id', 'action_id', 'amount_micro_usd', 'provider', 'account', 'state', 'provisional_micro_usd'},
    'holds': {'run_id', 'hold_id', 'kind', 'amount_micro_usd', 'reason'},
    'operations': {'run_id', 'scope', 'idempotency_key', 'fingerprint', 'result'},
    'charges': {'provider', 'account', 'mode', 'external_charge_id', 'reservation_id', 'amount_micro_usd', 'usage', 'outcome'},
    'provisional_charges': {'provider', 'account', 'mode', 'external_charge_id', 'reservation_id', 'amount_micro_usd'},
    'events': {'sequence', 'event_id', 'version', 'type', 'run_id', 'occurred_at', 'data'},
    'payments': {'provider', 'account', 'mode', 'payment_id', 'run_id', 'job_id', 'revision', 'status', 'released_micro_usd', 'pending_liability_micro_usd'},
    'payment_transactions': {'provider', 'account', 'mode', 'transaction_id', 'payment_id', 'run_id', 'kind', 'amount_micro_usd', 'fee_micro_usd', 'net_micro_usd'},
    'payment_events': {'provider', 'account', 'mode', 'event_id', 'digest', 'payment_id'},
    'checkouts': {'provider', 'account', 'mode', 'request_id', 'run_id', 'job_id', 'amount_micro_usd', 'fingerprint', 'payment_id', 'url'},
    'reconciliations': {'reconciliation_id', 'run_id', 'provider', 'account', 'payment_id', 'revision', 'summary'},
    'payment_components': {'provider', 'account', 'mode', 'transaction_id', 'component', 'entry_id', 'amount_micro_usd'},
    'reconciliation_jobs': {'task_id', 'provider', 'account', 'mode', 'state'},
    'reconciliation_attempts': {'task_id'},
    'reconciliation_operator_actions': {'task_id'},
}
CENTRAL_COLUMNS = {
    'runtime_authorizations': {'reservation_id', 'run_id', 'action_id', 'tool', 'provider', 'account', 'mode', 'limit_json', 'fingerprint'},
    'runtime_receipts': {'provider', 'account', 'mode', 'event_id', 'reservation_id', 'fingerprint', 'result_json'},
    'runtime_dispatches': {'dispatch_id', 'reservation_id', 'ordinal'},
    'runtime_rearms': {'reservation_id', 'dispatch_ordinal', 'evidence_ref', 'fingerprint'},
}


def check_family(db, family, error, *, require_marker=False, allow_future=False):
    """Read-only verification; callers must invoke before journal/migration writes."""
    version = db.execute('PRAGMA user_version').fetchone()[0]
    supported = (0, 1, 2, 3, 4, 5, 6) if family == 'service-v1' else (0, 1, 2, 3, 4, 5)
    if not allow_future and version not in supported:
        raise error('future_schema', 'Unsupported accounting schema; do not downgrade')
    schema = {row[1]: row[0] for row in db.execute('SELECT type,name FROM sqlite_master')}
    marked = TABLE in schema
    def reject():
        raise error('database_family_mismatch', 'Accounting database belongs to a different or ambiguous protocol family; use its original service')
    if marked:
        if schema[TABLE] != 'table':
            reject()
        try:
            rows = db.execute('SELECT singleton,family FROM economy_database_family LIMIT 2').fetchall()
        except sqlite3.DatabaseError:
            reject()
        if len(rows) != 1 or rows[0][0] != 1 or rows[0][1] != family:
            reject()
        if any(schema.get(name) != 'trigger' for name in TRIGGERS):
            reject()
    elif require_marker:
        reject()
    if RESEARCH_TABLE in schema and (family != 'service-v1' or version < 6):
        reject()
    if version == 6:
        if (family != 'service-v1' or not marked or schema.get(RESEARCH_TABLE) != 'table'
                or not {'parent_reservation_id', 'child_reservation_id', 'created_at'}.issubset({row[1] for row in db.execute('PRAGMA table_info(' + RESEARCH_TABLE + ')')})
                or any(schema.get(name) != 'trigger' for name in RESEARCH_TRIGGERS)):
            reject()
    footprint = RUNTIME_TABLES.intersection(schema)
    complete = footprint == RUNTIME_TABLES and all(schema[name] == 'table' for name in RUNTIME_TABLES)
    if footprint and (family != 'central-v1' or not complete or (version != 5 and not marked)):
        reject()
    if version == 5 and family == 'central-v1' and not complete:
        reject()
    if version in (5, 6):
        expected = {**BASE_COLUMNS, **(CENTRAL_COLUMNS if family == 'central-v1' else {})}
        for name, columns in expected.items():
            if schema.get(name) != 'table' or not columns.issubset({row[1] for row in db.execute('PRAGMA table_info(' + name + ')')}):
                reject()
    if version == 0 and any(kind == 'table' and not name.startswith('sqlite_') and name != TABLE
                            for name, kind in schema.items()):
        reject()
    return marked


def preflight(path, family, error):
    """Reject existing foreign files through a read-only connection first."""
    if not path.exists() or path.stat().st_size == 0:
        return
    # The current WAL is authoritative. Immutable reads can ignore committed
    # markers during checkpoint activity, so use a normal read-only snapshot.
    db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, isolation_level=None, timeout=30)
    try:
        db.execute('BEGIN')
        check_family(db, family, error)
    finally:
        db.close()


def claim_family(db, family, error):
    """Run inside the migration's existing BEGIN IMMEDIATE transaction."""
    if check_family(db, family, error):
        return
    db.execute("CREATE TABLE economy_database_family (singleton INTEGER PRIMARY KEY CHECK(singleton=1), family TEXT NOT NULL CHECK(family IN ('central-v1','service-v1')))")
    db.execute('INSERT INTO economy_database_family VALUES(1,?)', (family,))
    db.execute("CREATE TRIGGER economy_family_no_update BEFORE UPDATE ON economy_database_family BEGIN SELECT RAISE(ABORT,'immutable accounting database family'); END")
    db.execute("CREATE TRIGGER economy_family_no_delete BEFORE DELETE ON economy_database_family BEGIN SELECT RAISE(ABORT,'immutable accounting database family'); END")
    db.execute("CREATE TRIGGER economy_family_no_replace BEFORE INSERT ON economy_database_family WHEN EXISTS(SELECT 1 FROM economy_database_family) BEGIN SELECT RAISE(ABORT,'immutable accounting database family'); END")
