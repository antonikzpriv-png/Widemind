"""Reporting uses synthetic accounting fixtures, never live provider evidence."""
from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from widemind.economy.reporting import CASH_CATEGORIES, audit_integrity, public_summary
from widemind.economy.service import EconomyError, EconomyService


PRIVATE = "private-customer-email@example.invalid"
RUN = "private-run-identifier"
PROVIDER = "private-provider-identifier"
ACCOUNT = "private-provider-account"
JOB = "private-job-identifier"
PAYMENT = "private-payment-identifier"


def _payment(service, transactions):
    payment = service.payment(PROVIDER, ACCOUNT, "test", PAYMENT)
    service.apply_payment_snapshot({
        "provider": PROVIDER, "account": ACCOUNT, "mode": "test", "payment_id": PAYMENT,
        "run_id": RUN, "job_id": JOB, "status": "settled", "expected_revision": payment["revision"],
        "transactions": transactions,
    }, event_id=f"private-event-{payment['revision']}", event_digest="private-webhook-digest")


def _transaction(identifier, gross, fee, *, kind="receipt", settled=True):
    return {
        "transaction_id": identifier, "kind": kind, "amount_micro_usd": gross,
        "fee_micro_usd": fee, "net_micro_usd": gross - fee, "currency": "USD", "settled": settled,
        "native": {"private_customer": PRIVATE, "private_source_url": "https://example.invalid/private-source"},
    }


@pytest.fixture
def service(tmp_path):
    service = EconomyService(tmp_path / "private-accounting.sqlite3")
    service.create_run(RUN)
    return service


@pytest.fixture
def populated(service):
    owner = service.record_funding(RUN, 1_000_000, "owner_funding", PRIVATE, "private-owner-key")
    service.record_funding(RUN, 2_000_000, "test_funding", PRIVATE, "private-test-key")
    service.record_subsidy(RUN, 3_000_000, PRIVATE, "private-subsidy-key")
    service.record_earned_revenue(RUN, 4_000_000, JOB, PRIVATE, "private-earned-key")
    service.set_hold(RUN, "private-refund-hold", 500_000, "refund", PRIVATE, "private-hold-key")
    reservation = service.reserve(RUN, "private-action", 1_000_000, "private-reserve-key", PROVIDER, ACCOUNT, {"max_tokens": 100})
    service.start_reservation(reservation["reservation_id"])
    service.settle(reservation["reservation_id"], 200_000, PROVIDER, ACCOUNT, "private-charge", usage={"private": PRIVATE}, outcome="unknown")
    service.register_payment(RUN, PROVIDER, ACCOUNT, PAYMENT, JOB, "private-registration-key")
    receipt = _transaction("private-receipt", 3_000_000, 100_000)
    _payment(service, [receipt])
    service.release_payment_hold(PROVIDER, ACCOUNT, "test", PAYMENT, "private-fulfillment-key")
    refund = _transaction("private-refund", -1_000_000, -30_000, kind="refund")
    _payment(service, [receipt, refund])
    service.correct_entry(RUN, owner["entry_id"], -100_000, PRIVATE, "private-correction-key")
    return service


def _assert_no_private_data(value):
    encoded = json.dumps(value)
    for secret in (PRIVATE, RUN, PROVIDER, ACCOUNT, JOB, PAYMENT, "private-", "example.invalid"):
        assert secret not in encoded


def _failed(report):
    return {check["code"] for check in report["checks"] if not check["ok"]}


def _database_dump(service):
    with sqlite3.connect(service.path) as connection:
        return "\n".join(connection.iterdump())


def _legacy_projection_update(service, table, statement, arguments=()):
    """Inject legacy damage as a privileged test administrator, past new guards.

    New databases prevent these mutations; an upgraded old database may already
    contain them. The detector must still identify those existing bad records.
    """
    assert table in {"reservations", "payments"}
    with sqlite3.connect(service.path) as connection:
        triggers = connection.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name=?", (table,)).fetchall()
        for (name,) in triggers:
            connection.execute('DROP TRIGGER "' + name.replace('"', '""') + '"')
        connection.execute(statement, arguments)


def test_summary_is_an_explicit_allowlist_and_reconciles_with_private_ledger(populated):
    summary = public_summary(populated, RUN)
    private = populated.balance(RUN)
    assert set(summary) == {
        "version", "currency", "unit", "mode", "verification_scope", "posted_micro_usd",
        "reserved_micro_usd", "held_micro_usd", "available_micro_usd", "exhausted",
        "cash_categories", "unclassified_cash_micro_usd", "noncash_metrics",
    }
    assert summary["mode"] == "test"
    assert summary["verification_scope"] == "local_accounting_only"
    assert summary["currency"] == "USD" and summary["unit"] == "micro-USD"
    assert set(summary["cash_categories"]) == set(CASH_CATEGORIES)
    for field in ("posted_micro_usd", "reserved_micro_usd", "held_micro_usd", "available_micro_usd"):
        assert summary[field] == private[field]
    assert summary["posted_micro_usd"] == sum(entry["amount_micro_usd"] for entry in populated.ledger(RUN))
    assert summary["posted_micro_usd"] == sum(summary["cash_categories"].values()) + summary["unclassified_cash_micro_usd"]
    assert summary["cash_categories"]["opening_grant"] == 5_000_000
    assert summary["cash_categories"]["customer_receipt"] == 3_000_000
    assert summary["cash_categories"]["processor_fee"] == -70_000
    assert summary["noncash_metrics"] == {"earned_revenue_micro_usd": 4_000_000, "infrastructure_subsidy_micro_usd": 3_000_000}
    assert summary["posted_micro_usd"] == 9_630_000
    assert summary["available_micro_usd"] == 8_330_000
    _assert_no_private_data(summary)


def test_unknown_future_category_is_aggregated_without_publishing_its_name(service):
    with sqlite3.connect(service.path) as connection:
        connection.execute("INSERT INTO ledger VALUES(?,?,?,?,?,?,?,?)", ("private-new-entry", RUN, "private-future-type", PRIVATE, 123, PRIVATE, "{}", "2026-10-29T00:00:00+00:00"))
    summary = public_summary(service, RUN)
    assert summary["posted_micro_usd"] == 5_000_123
    assert summary["unclassified_cash_micro_usd"] == 123
    assert sum(summary["cash_categories"].values()) + summary["unclassified_cash_micro_usd"] == 5_000_123
    _assert_no_private_data(summary)


def test_negative_balance_and_holds_are_never_clamped_or_hidden(service):
    reservation = service.reserve(RUN, "private-overrun", 1_000_000, "private-key", PROVIDER, ACCOUNT, {"max_tokens": 100})
    service.start_reservation(reservation["reservation_id"])
    service.settle(reservation["reservation_id"], 7_000_000, PROVIDER, ACCOUNT, "private-overrun-charge")
    service.set_hold(RUN, "private-liability", 1_000_000, "refund", PRIVATE, "private-hold")
    summary = public_summary(service, RUN)
    assert summary["posted_micro_usd"] == -2_000_000
    assert summary["held_micro_usd"] == 1_000_000
    assert summary["available_micro_usd"] == -3_000_000
    assert summary["exhausted"] is True
    assert audit_integrity(service, RUN)["ok"] is True


def test_live_mode_is_visible_without_claiming_live_payment_or_profit_verification(service):
    service.create_run("private-live-run", mode="live")
    summary = public_summary(service, "private-live-run")
    assert summary["mode"] == "live"
    assert summary["verification_scope"] == "local_accounting_only"
    assert "profit" not in json.dumps(summary)
    _assert_no_private_data(summary)


def test_reporting_never_mutates_accounting_records_and_survives_restart(populated):
    before = _database_dump(populated)
    summary = public_summary(populated, RUN)
    report = audit_integrity(populated, RUN)
    assert report["complete"] is True and report["ok"] is True, report
    assert report["inspected"]["payment_components"] == 4
    assert report["inspected"]["charges"] == 0
    assert report["inspected"]["provisional_charges"] == 1
    _assert_no_private_data(report)
    assert _database_dump(populated) == before
    restarted = EconomyService(populated.path)
    assert public_summary(restarted, RUN) == summary
    assert audit_integrity(restarted, RUN) == report
    reservation = restarted.reservations(RUN)[0]
    restarted.settle(reservation["reservation_id"], 250_000, PROVIDER, ACCOUNT, "private-charge", outcome="failed")
    assert audit_integrity(restarted, RUN)["ok"] is True


def test_audit_detects_missing_financial_event(service):
    service.record_funding(RUN, 1_000_000, "owner_funding", PRIVATE, "private-key")
    with sqlite3.connect(service.path) as connection:
        connection.execute("DROP TRIGGER events_no_delete")
        connection.execute("DELETE FROM events WHERE type='economy.funding'")
    report = audit_integrity(service, RUN)
    assert "financial_event_coverage" in _failed(report)
    _assert_no_private_data(report)


def test_audit_detects_payment_component_and_journal_disagreement(populated):
    with sqlite3.connect(populated.path) as connection:
        connection.execute("DROP TRIGGER payment_components_no_update")
        connection.execute("UPDATE payment_components SET amount_micro_usd=amount_micro_usd+1")
    assert "payment_component_journal" in _failed(audit_integrity(populated, RUN))


def test_audit_detects_missing_opening_credit(service):
    with sqlite3.connect(service.path) as connection:
        connection.execute("DROP TRIGGER ledger_no_delete")
        connection.execute("DELETE FROM ledger")
    assert "opening_credit" in _failed(audit_integrity(service, RUN))


def test_audit_detects_broken_charge_total(populated):
    with sqlite3.connect(populated.path) as connection:
        connection.execute("UPDATE reservations SET provisional_micro_usd=200001")
    failed = _failed(audit_integrity(populated, RUN))
    assert "operating_charge_journal" in failed
    assert "partial_charge_identity" in failed


def test_audit_detects_hold_projection_changed_without_a_recorded_transition(populated):
    with sqlite3.connect(populated.path) as connection:
        connection.execute("UPDATE holds SET amount_micro_usd=0 WHERE hold_id='private-refund-hold'")
    assert "hold_event_projection" in _failed(audit_integrity(populated, RUN))


def test_audit_detects_reservation_projection_changed_without_dispatch(populated):
    with sqlite3.connect(populated.path) as connection:
        connection.execute("UPDATE reservations SET state='reserved'")
    assert "reservation_event_projection" in _failed(audit_integrity(populated, RUN))


def test_settled_component_cannot_disappear_with_its_journal_record(populated):
    with sqlite3.connect(populated.path) as connection:
        connection.execute("DROP TRIGGER payment_components_no_delete")
        connection.execute("DROP TRIGGER ledger_no_delete")
        entry = connection.execute("SELECT entry_id FROM payment_components WHERE component='gross' AND amount_micro_usd>0").fetchone()[0]
        connection.execute("DELETE FROM payment_components WHERE entry_id=?", (entry,))
        connection.execute("DELETE FROM ledger WHERE entry_id=?", (entry,))
    assert "settled_component_posted" in _failed(audit_integrity(populated, RUN))


def test_audit_rejects_active_provisional_cash_correction_from_legacy_database(service):
    reservation = service.reserve(RUN, "private-attempt", 1_000_000, "private-key", PROVIDER, ACCOUNT, {"max_tokens": 100})
    service.start_reservation(reservation["reservation_id"])
    service.settle(reservation["reservation_id"], 500_000, PROVIDER, ACCOUNT, "private-charge", outcome="unknown")
    original = next(entry for entry in service.ledger(RUN) if entry["kind"] == "operating_charge")
    # Simulate an already-persisted legacy correction without relying on the now-fixed admission method.
    with sqlite3.connect(service.path) as connection:
        connection.execute("INSERT INTO ledger VALUES(?,?,?,?,?,?,?,?)", ("private-legacy-correction", RUN, "correction", "correction", 500_000, original["entry_id"], json.dumps({"corrects_entry_id": original["entry_id"]}), "2026-10-29T00:00:00+00:00"))
    assert "active_provisional_corrections" in _failed(audit_integrity(service, RUN))


def test_incomplete_audit_does_not_report_success(service):
    report = audit_integrity(service, RUN, max_records=1)
    assert report["complete"] is False and report["ok"] is False
    assert _failed(report) == {"record_limit"}


@pytest.mark.parametrize("limit", [True, False, 0, -1, 1.5, "100", 1_000_001])
def test_invalid_audit_limits_are_rejected(service, limit):
    with pytest.raises(EconomyError) as caught:
        audit_integrity(service, RUN, max_records=limit)
    assert caught.value.status == 422


def test_unknown_run_uses_existing_not_found_contract(service):
    for read in (public_summary, audit_integrity):
        with pytest.raises(EconomyError) as caught:
            read(service, "private-unknown-run")
        assert caught.value.status == 404


@pytest.mark.parametrize("version", [2, 3, 4])
def test_audit_supports_additive_schemas_and_fails_closed_for_unknown_future_versions(service, version):
    with sqlite3.connect(service.path) as connection:
        connection.execute(f"PRAGMA user_version={version}")
    assert audit_integrity(service, RUN)["ok"] is True
    with sqlite3.connect(service.path) as connection:
        connection.execute("PRAGMA user_version=42")
    report = audit_integrity(service, RUN)
    assert report["complete"] is False and report["ok"] is False
    assert _failed(report) == {"supported_schema"}


def test_concurrent_read_reports_never_combine_different_transaction_snapshots(service):
    def write():
        for index in range(20):
            service.record_funding(RUN, 1000, "owner_funding", PRIVATE, f"private-key-{index}")

    with ThreadPoolExecutor(max_workers=2) as executor:
        writer = executor.submit(write)
        for _ in range(20):
            report = audit_integrity(service, RUN)
            assert report["ok"] is True, report
            summary = public_summary(service, RUN)
            assert summary["posted_micro_usd"] == sum(summary["cash_categories"].values())
        writer.result(timeout=20)


def test_corrupt_structured_record_reports_failure_without_echoing_its_contents(service):
    with sqlite3.connect(service.path) as connection:
        connection.execute("DROP TRIGGER events_no_update")
        connection.execute("UPDATE events SET data=?", (json.dumps({"reconciliation_id": {"private": PRIVATE}}),))
    report = audit_integrity(service, RUN)
    assert report["ok"] is False
    _assert_no_private_data(report)


def test_corrupt_cost_metadata_is_a_failed_audit_without_a_private_exception(service):
    reservation = service.reserve(RUN, "private-attempt", 1_000_000, "private-key", PROVIDER, ACCOUNT, {"max_tokens": 100})
    service.start_reservation(reservation["reservation_id"])
    service.settle(reservation["reservation_id"], 500_000, PROVIDER, ACCOUNT, "private-charge")
    with sqlite3.connect(service.path) as connection:
        connection.execute("DROP TRIGGER ledger_no_update")
        connection.execute("UPDATE ledger SET metadata=? WHERE kind='operating_charge'", (json.dumps({"reservation_id": {"private": PRIVATE}}),))
    report = audit_integrity(service, RUN)
    assert "operating_charge_identity" in _failed(report)
    _assert_no_private_data(report)


@pytest.mark.parametrize("field,value", [
    ("amount_micro_usd", 1_000_000), ("provider", "changed-provider"),
    ("account", "changed-account"), ("action_id", "changed-action"),
    ("limits_json", '{"max_tokens":1}'),
])
def test_reservation_authorization_cannot_change_while_state_is_preserved(service, field, value):
    service.reserve(RUN, "original-action", 4_000_000, "original-key", PROVIDER, ACCOUNT, {"max_tokens": 100})
    _legacy_projection_update(service, "reservations", f"UPDATE reservations SET {field}=?", (value,))
    report = audit_integrity(service, RUN)
    assert "reservation_authorization" in _failed(report)
    _assert_no_private_data(report)


def test_partial_charge_projection_must_match_recorded_subtotal_events(populated):
    with sqlite3.connect(populated.path) as connection:
        connection.execute("DROP TRIGGER events_no_update")
        row = connection.execute("SELECT sequence,data FROM events WHERE type='economy.cost_unknown'").fetchone()
        data = json.loads(row[1])
        data["reported_subtotal_micro_usd"] = 100_000
        connection.execute("UPDATE events SET data=? WHERE sequence=?", (json.dumps(data), row[0]))
    assert "provisional_event_projection" in _failed(audit_integrity(populated, RUN))


@pytest.mark.parametrize("field,value", [
    ("released_micro_usd", "bad"), ("released_micro_usd", -1),
    ("pending_liability_micro_usd", "bad"), ("pending_liability_micro_usd", -1),
    ("revision", "bad"), ("revision", -1),
])
def test_payment_numeric_projection_corruption_cannot_receive_a_clean_audit(service, field, value):
    service.register_payment(RUN, PROVIDER, ACCOUNT, PAYMENT, JOB, "registration")
    _legacy_projection_update(service, "payments", f"UPDATE payments SET {field}=?", (value,))
    report = audit_integrity(service, RUN)
    assert report["ok"] is False
    assert "payment_projection_fields" in _failed(report)
    _assert_no_private_data(report)


def test_payment_release_projection_must_match_actual_customer_hold(populated):
    with sqlite3.connect(populated.path) as connection:
        connection.execute("UPDATE payments SET released_micro_usd=0")
    assert "payment_hold_projection" in _failed(audit_integrity(populated, RUN))


@pytest.mark.parametrize("state,actual", [("reserved", 1), ("unknown", -1), ("settled", None), ("settled", "bad"), ("settled", -1)])
def test_reservation_actual_amount_is_valid_for_its_state(service, state, actual):
    service.reserve(RUN, "action", 1_000_000, "key", PROVIDER, ACCOUNT, {"max_tokens": 100})
    _legacy_projection_update(service, "reservations", "UPDATE reservations SET state=?,actual_micro_usd=?", (state, actual))
    assert "reservation_amount_state" in _failed(audit_integrity(service, RUN))
