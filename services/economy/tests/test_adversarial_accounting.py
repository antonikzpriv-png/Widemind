"""Independent accounting invariants, including actual multiprocess contention.

All provider records in this module are synthetic fixtures, never live payments.
"""

from __future__ import annotations

import copy
import multiprocessing
import sqlite3
from queue import Empty
from pathlib import Path

import pytest

from widemind.economy.service import EconomyError, EconomyService


PROVIDER = "fixture-provider"
ACCOUNT = "fixture-account"


def _reserve(service, action, amount, *, run="run", key=None):
    return service.reserve(
        run,
        action,
        amount,
        key or action,
        PROVIDER,
        ACCOUNT,
        {"max_output_tokens": 100},
    )


def _contending_worker(database, barrier, results, operation, index):
    try:
        barrier.wait(timeout=30)
        service = EconomyService(database)
        if operation == "seed":
            service.create_run("run")
        else:
            _reserve(service, f"action-{index}", 1_000_000)
        results.put(("ok", index))
    except EconomyError as exc:
        results.put(("rejected", exc.code))
    except BaseException as exc:
        results.put(("unexpected", type(exc).__name__, str(exc)))


def _contend(database: Path, operation: str, count: int):
    """Spawn independently initialized interpreters against one real database."""
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(count)
    results = context.Queue()
    workers = [
        context.Process(
            target=_contending_worker,
            args=(str(database), barrier, results, operation, index),
        )
        for index in range(count)
    ]
    received = []
    try:
        for worker in workers:
            worker.start()
        for _ in workers:
            try:
                received.append(results.get(timeout=45))
            except Empty:
                pytest.fail(f"A {operation} process failed to report: {received}")
        for worker in workers:
            worker.join(timeout=10)
            assert worker.exitcode == 0, (worker.pid, worker.exitcode, received)
    finally:
        for worker in workers:
            if worker.pid is not None and worker.is_alive():
                worker.terminate()
                worker.join(timeout=10)
        results.close()
        results.join_thread()
    assert not [result for result in received if result[0] == "unexpected"], received
    return received


@pytest.fixture
def service(tmp_path):
    instance = EconomyService(tmp_path / "accounting.sqlite3")
    instance.create_run("run")
    return instance


def _snapshot(*transactions, payment="payment", account=ACCOUNT, status="settled"):
    return {
        "provider": PROVIDER,
        "account": account,
        "mode": "test",
        "payment_id": payment,
        "run_id": "run",
        "job_id": "job",
        "status": status,
        "transactions": list(transactions),
        "pending_liability_micro_usd": 0,
    }


def _transaction(identifier, amount, *, fee=0, kind="receipt", settled=True):
    return {
        "transaction_id": identifier,
        "kind": kind,
        "amount_micro_usd": amount,
        "fee_micro_usd": fee,
        "net_micro_usd": amount - fee,
        "currency": "USD",
        "settled": settled,
        "native": {"fixture": True, "currency": "usd", "id": identifier},
    }


def _apply(service, snapshot, **kwargs):
    """Emulate an authenticated adapter reading a fresh optimistic revision."""
    value = copy.deepcopy(snapshot)
    try:
        payment = service.payment(value["provider"], value["account"], value["mode"], value["payment_id"])
        value["expected_revision"] = payment["revision"]
    except EconomyError as exc:
        if exc.status != 404:
            raise
        value["expected_revision"] = 0
    return service.apply_payment_snapshot(value, **kwargs)


def _register(service, *, payment="payment", account=ACCOUNT):
    service.register_payment("run", PROVIDER, account, payment, "job", f"bind-{account}-{payment}")


def test_simultaneous_new_processes_seed_exactly_five_dollars_once(tmp_path):
    database = tmp_path / "accounting.sqlite3"
    results = _contend(database, "seed", 6)
    assert [result[0] for result in results] == ["ok"] * 6, results
    recovered = EconomyService(database)
    assert recovered.balance("run")["posted_micro_usd"] == 5_000_000
    assert len(recovered.ledger("run")) == 1
    assert len(recovered.events("run")) == 1
    with pytest.raises(EconomyError):
        recovered.create_run("run", mode="live")
    assert recovered.balance("run")["posted_micro_usd"] == 5_000_000


def test_independent_process_reservations_cannot_share_the_last_credit(tmp_path):
    database = tmp_path / "accounting.sqlite3"
    service = EconomyService(database)
    service.create_run("run")
    results = _contend(database, "reserve", 8)
    assert sum(result[0] == "ok" for result in results) == 5, results
    assert sum(result[0] == "rejected" for result in results) == 3, results
    balance = service.balance("run")
    assert balance["posted_micro_usd"] == 5_000_000
    assert balance["reserved_micro_usd"] == 5_000_000
    assert balance["available_micro_usd"] == 0
    assert len(service.reservations("run")) == 5


def test_unknown_outcome_survives_restart_and_cannot_be_cancelled(tmp_path):
    database = tmp_path / "accounting.sqlite3"
    first = EconomyService(database)
    first.create_run("run")
    reservation = _reserve(first, "interrupted-call", 3_000_000)
    identifier = reservation["reservation_id"]
    first.start_reservation(identifier)
    first.settle(identifier, None, PROVIDER, ACCOUNT, "uncertain-call", outcome="unknown")

    recovered = EconomyService(database)
    assert recovered.balance("run")["reserved_micro_usd"] == 3_000_000
    with pytest.raises(EconomyError):
        recovered.cancel_reservation(identifier)
    with pytest.raises(EconomyError):
        _reserve(recovered, "would-overspend", 2_000_001)
    recovered.settle(identifier, 1_250_000, PROVIDER, ACCOUNT, "actual-charge", outcome="failed")
    balance = EconomyService(database).balance("run")
    assert balance["posted_micro_usd"] == 3_750_000
    assert balance["reserved_micro_usd"] == 0
    assert balance["available_micro_usd"] == 3_750_000


def test_unexpected_bill_is_visible_below_zero_and_blocks_new_work(service):
    reservation = _reserve(service, "overrun", 1_000_000)
    identifier = reservation["reservation_id"]
    service.start_reservation(identifier)
    service.settle(identifier, 7_000_000, PROVIDER, ACCOUNT, "overrun-bill")
    balance = service.balance("run")
    assert balance["posted_micro_usd"] == -2_000_000
    assert balance["available_micro_usd"] == -2_000_000
    assert balance["reserved_micro_usd"] == 0
    with pytest.raises(EconomyError):
        _reserve(service, "one-micro-too-many", 1)


def test_holds_are_liabilities_even_when_larger_than_cash(service):
    service.set_hold("run", "refund-exposure", 7_000_000, "refund", "customer claim", "hold-1")
    balance = service.balance("run")
    assert balance["posted_micro_usd"] == 5_000_000
    assert balance["held_micro_usd"] == 7_000_000
    assert balance["available_micro_usd"] == -2_000_000
    with pytest.raises(EconomyError):
        _reserve(service, "blocked-by-liability", 1)
    service.set_hold("run", "refund-exposure", 0, "refund", "claim resolved", "hold-release")
    assert service.balance("run")["available_micro_usd"] == 5_000_000


def test_repeating_charge_reconciliation_does_not_charge_twice(service):
    reservation = _reserve(service, "charged-failure", 2_000_000)
    identifier = reservation["reservation_id"]
    service.start_reservation(identifier)
    for _ in range(3):
        service.settle(identifier, 700_000, PROVIDER, ACCOUNT, "bill-42", outcome="failed")
    assert service.balance("run")["available_micro_usd"] == 4_300_000
    assert len(service.ledger("run")) == 2
    with pytest.raises(EconomyError):
        service.settle(identifier, 800_000, PROVIDER, ACCOUNT, "bill-42", outcome="failed")
    assert service.balance("run")["available_micro_usd"] == 4_300_000


def test_pending_receipt_and_accounting_metrics_never_create_cash(service):
    _register(service)
    _apply(service, _snapshot(_transaction("pending", 10_000_000, settled=False)))
    service.record_subsidy("run", 20_000_000, "hosting-covered", "subsidy-1")
    service.record_earned_revenue("run", 9_000_000, "job", "delivery accepted", "earned-1")
    assert service.balance("run")["posted_micro_usd"] == 5_000_000
    assert service.balance("run")["available_micro_usd"] == 5_000_000


def test_fee_is_not_subtracted_twice_and_customer_hold_requires_release(service):
    _register(service)
    snapshot = _snapshot(_transaction("receipt-1", 10_000_000, fee=300_000))
    _apply(service, snapshot, event_id="evt-1", event_digest="digest-1")
    before = service.balance("run")
    assert before["posted_micro_usd"] == 14_700_000
    assert before["held_micro_usd"] == 9_700_000
    assert before["available_micro_usd"] == 5_000_000
    service.release_payment_hold(PROVIDER, ACCOUNT, "test", "payment", "fulfill-1")
    assert service.balance("run")["available_micro_usd"] == 14_700_000
    _apply(service, snapshot, event_id="evt-2", event_digest="digest-2")
    assert service.balance("run")["available_micro_usd"] == 14_700_000


def test_refund_before_receipt_remains_visible_and_later_receipt_cannot_erase_it(service):
    _register(service)
    refund = _transaction("refund-1", -2_000_000, kind="refund", settled=False)
    _apply(service, _snapshot(refund, status="refunded"))
    assert service.balance("run")["posted_micro_usd"] == 3_000_000
    receipt = _transaction("receipt-1", 3_000_000, fee=100_000)
    _apply(service, _snapshot(refund, receipt, status="refunded"))
    assert service.balance("run")["posted_micro_usd"] == 5_900_000
    service.release_payment_hold(PROVIDER, ACCOUNT, "test", "payment", "release-1")
    assert service.balance("run")["available_micro_usd"] == 5_900_000


def test_duplicate_webhook_and_different_events_for_same_money_are_both_idempotent(service):
    _register(service)
    snapshot = _snapshot(_transaction("same-money", 2_000_000, fee=50_000))
    _apply(service, snapshot, event_id="event", event_digest="body-digest")
    first_ledger = service.ledger("run")
    first_events = service.events("run")
    _apply(service, snapshot, event_id="event", event_digest="body-digest")
    assert service.ledger("run") == first_ledger
    assert service.events("run") == first_events
    _apply(service, snapshot, event_id="another-event", event_digest="another-body")
    assert service.ledger("run") == first_ledger
    with pytest.raises(EconomyError):
        _apply(service, snapshot, event_id="event", event_digest="changed-body")
    assert service.ledger("run") == first_ledger


def test_same_transaction_id_in_different_provider_accounts_is_distinct(service):
    for account in ("customer-account-a", "customer-account-b"):
        _register(service, account=account)
        _apply(service,
            _snapshot(_transaction("same-id", 1_000_000), account=account),
            event_id="same-event-id",
            event_digest="same-digest",
        )
        service.release_payment_hold(PROVIDER, account, "test", "payment", f"release-{account}")
    assert service.balance("run")["available_micro_usd"] == 7_000_000


def test_mutated_transaction_replay_cannot_change_previously_recorded_money(service):
    _register(service)
    snapshot = _snapshot(_transaction("immutable-receipt", 1_000_000))
    _apply(service, snapshot)
    original = service.ledger("run")
    mutated = copy.deepcopy(snapshot)
    mutated["transactions"][0].update(amount_micro_usd=9_000_000, net_micro_usd=9_000_000)
    with pytest.raises(EconomyError):
        _apply(service, mutated)
    assert service.ledger("run") == original


def test_receipt_pending_to_settled_transition_credits_once(service):
    _register(service)
    pending = _transaction("eventually-settled", 2_000_000, fee=50_000, settled=False)
    _apply(service, _snapshot(pending, status="pending"))
    # A verified processor fee is already a liability; the pending gross is not cash.
    assert service.balance("run")["posted_micro_usd"] == 4_950_000
    assert not [entry for entry in service.ledger("run") if entry["kind"] == "customer_receipt"]
    settled = copy.deepcopy(pending)
    settled["settled"] = True
    _apply(service, _snapshot(settled))
    assert service.balance("run")["posted_micro_usd"] == 6_950_000
    service.release_payment_hold(PROVIDER, ACCOUNT, "test", "payment", "complete-1")
    _apply(service, _snapshot(settled))
    assert service.balance("run")["available_micro_usd"] == 6_950_000


def test_chargeback_reversal_cannot_create_cash_until_settled(service):
    _register(service)
    debit = _transaction("dispute-debit", -6_000_000, kind="chargeback", settled=False)
    _apply(service, _snapshot(debit, status="disputed"))
    assert service.balance("run")["posted_micro_usd"] == -1_000_000
    reversal = _transaction("dispute-reversal", 6_000_000, kind="chargeback_reversal", settled=False)
    _apply(service, _snapshot(debit, reversal, status="disputed"))
    assert service.balance("run")["posted_micro_usd"] == -1_000_000
    reversal["settled"] = True
    _apply(service, _snapshot(debit, reversal))
    assert service.balance("run")["posted_micro_usd"] == 5_000_000
    assert service.balance("run")["available_micro_usd"] == 5_000_000


def test_funding_categories_do_not_allow_test_money_in_live_runs(service):
    service.create_run("live-run", mode="live", funding_reference="fixture-owner-attestation")
    with pytest.raises(EconomyError):
        service.record_funding("live-run", 1_000_000, "test_funding", "sandbox", "not-real-money")
    assert service.balance("live-run")["posted_micro_usd"] == 5_000_000


@pytest.mark.parametrize("amount", [True, False, -1, 0.5, 1.0, "100", 2**64])
def test_reservations_reject_nonpositive_noninteger_and_unrepresentable_money(service, amount):
    before = service.balance("run")
    with pytest.raises(EconomyError):
        _reserve(service, "invalid", amount)
    assert service.balance("run") == before
    assert service.reservations("run") == []


def test_failed_atomic_snapshot_cannot_leave_partial_cash_or_fees(service):
    _register(service)
    original = service.ledger("run")
    good = _transaction("good", 3_000_000, fee=100_000)
    malformed = _transaction("bad", 2_000_000)
    malformed["net_micro_usd"] = 7
    with pytest.raises(EconomyError):
        _apply(service, _snapshot(good, malformed), event_id="atomic", event_digest="body")
    assert service.ledger("run") == original
    assert service.balance("run")["available_micro_usd"] == 5_000_000
    # A corrected retry with the same event identity succeeds: failure was not acknowledged.
    _apply(service, _snapshot(good), event_id="atomic", event_digest="body")
    assert service.balance("run")["posted_micro_usd"] == 7_900_000


def test_reservation_replay_preserves_identity_and_rejects_changed_authorization(service):
    first = _reserve(service, "one-action", 2_000_000, key="same-key")
    original_events = service.events("run")
    repeated = _reserve(service, "one-action", 2_000_000, key="same-key")
    assert repeated["reservation_id"] == first["reservation_id"]
    assert service.events("run") == original_events
    with pytest.raises(EconomyError):
        _reserve(service, "different-action", 2_000_000, key="same-key")
    with pytest.raises(EconomyError):
        _reserve(service, "one-action", 1_000_000, key="another-key")
    assert service.balance("run")["reserved_micro_usd"] == 2_000_000
    assert len(service.reservations("run")) == 1


def test_funding_and_revenue_are_independently_visible_without_double_cash(service):
    service.record_funding("run", 1_000_000, "owner_funding", "owner-wire", "owner-1")
    service.record_funding("run", 2_000_000, "test_funding", "sandbox", "test-1")
    service.record_subsidy("run", 3_000_000, "free-compute", "subsidy-1")
    service.record_earned_revenue("run", 4_000_000, "job", "accepted", "earned-1")
    first = service.balance("run")
    assert first["posted_micro_usd"] == 8_000_000
    assert first["available_micro_usd"] == 8_000_000
    assert first["metrics"]["earned_revenue_micro_usd"] == 4_000_000
    assert first["metrics"]["infrastructure_subsidy_micro_usd"] == 3_000_000
    # Replayed operator submissions do not repeat either money or performance metrics.
    service.record_funding("run", 1_000_000, "owner_funding", "owner-wire", "owner-1")
    service.record_funding("run", 2_000_000, "test_funding", "sandbox", "test-1")
    service.record_subsidy("run", 3_000_000, "free-compute", "subsidy-1")
    service.record_earned_revenue("run", 4_000_000, "job", "accepted", "earned-1")
    assert service.balance("run") == first
    assert {entry["category"] for entry in service.ledger("run")} >= {
        "opening_grant", "owner_funding", "test_funding", "infrastructure_subsidy", "earned_revenue"
    }


def test_paid_run_isolation_and_event_cursor_are_durable(service, tmp_path):
    service.create_run("second-run")
    first_events = service.events("run")
    _reserve(service, "first-run-action", 4_000_000)
    _reserve(service, "second-run-action", 3_000_000, run="second-run")
    assert service.balance("run")["available_micro_usd"] == 1_000_000
    assert service.balance("second-run")["available_micro_usd"] == 2_000_000
    later_events = service.events("run", after=first_events[-1]["sequence"])
    assert later_events
    assert all(event["run_id"] == "run" and event["version"] == 1 for event in later_events)
    assert all(event["sequence"] > first_events[-1]["sequence"] for event in later_events)
    sequences = [event["sequence"] for event in service.events("run")]
    assert sequences == sorted(set(sequences))
    restarted = EconomyService(tmp_path / "accounting.sqlite3")
    assert restarted.events("run") == service.events("run")
    assert restarted.balance("second-run") == service.balance("second-run")


def test_provider_metadata_alone_cannot_bind_an_unknown_receipt_to_a_run(service):
    before = service.ledger("run")
    with pytest.raises(EconomyError):
        _apply(service, _snapshot(_transaction("unmapped", 40_000_000)))
    assert service.ledger("run") == before
    assert service.balance("run")["available_micro_usd"] == 5_000_000


def test_sandbox_receipt_cannot_enter_a_live_run_even_with_an_existing_binding(service):
    service.create_run("live-run", mode="live", funding_reference="fixture-owner-attestation")
    service.register_payment("live-run", PROVIDER, ACCOUNT, "payment", "job", "live-bind")
    sandbox = _snapshot(_transaction("sandbox-funds", 40_000_000))
    sandbox["run_id"] = "live-run"
    with pytest.raises(EconomyError):
        _apply(service, sandbox)
    assert service.balance("live-run")["posted_micro_usd"] == 5_000_000


def test_partial_refunds_and_returned_processor_fees_use_net_exactly_once(service):
    _register(service)
    receipt = _transaction("receipt", 3_000_000, fee=90_000)
    _apply(service, _snapshot(receipt))
    service.release_payment_hold(PROVIDER, ACCOUNT, "test", "payment", "fulfilled")
    refund = _transaction("partial-refund", -1_000_000, fee=-30_000, kind="refund")
    for _ in range(3):
        _apply(service, _snapshot(receipt, refund, status="partially_refunded"))
    assert service.balance("run")["posted_micro_usd"] == 6_940_000
    assert service.balance("run")["available_micro_usd"] == 6_940_000


def test_compensating_correction_keeps_original_entry_and_is_replay_safe(service):
    service.record_funding("run", 1_000_000, "owner_funding", "wire", "owner-wire")
    original = service.ledger("run")
    funded = original[-1]
    service.correct_entry("run", funded["entry_id"], -1_000_000, "wire reversed", "correction")
    service.correct_entry("run", funded["entry_id"], -1_000_000, "wire reversed", "correction")
    assert service.ledger("run")[: len(original)] == original
    assert len(service.ledger("run")) == len(original) + 1
    assert service.balance("run")["posted_micro_usd"] == 5_000_000
    with pytest.raises(EconomyError):
        service.correct_entry("run", funded["entry_id"], 500_000, "changed", "correction")
    assert service.balance("run")["posted_micro_usd"] == 5_000_000


def test_verified_subtotal_is_charged_without_releasing_unknown_remainder(service, tmp_path):
    reservation = _reserve(service, "partially-billed", 3_000_000)
    identifier = reservation["reservation_id"]
    service.start_reservation(identifier)
    service.settle(identifier, 1_000_000, PROVIDER, ACCOUNT, "partial-bill", outcome="unknown")
    restarted = EconomyService(tmp_path / "accounting.sqlite3")
    balance = restarted.balance("run")
    assert balance["posted_micro_usd"] == 4_000_000
    assert balance["reserved_micro_usd"] == 2_000_000
    assert balance["available_micro_usd"] == 2_000_000
    restarted.settle(identifier, 1_000_000, PROVIDER, ACCOUNT, "partial-bill", outcome="unknown")
    assert restarted.balance("run") == balance
    restarted.settle(identifier, 1_500_000, PROVIDER, ACCOUNT, "partial-bill", outcome="failed")
    assert restarted.balance("run")["posted_micro_usd"] == 3_500_000
    assert restarted.balance("run")["reserved_micro_usd"] == 0
    assert restarted.balance("run")["available_micro_usd"] == 3_500_000


def test_fulfillment_cannot_release_pending_refund_exposure(service):
    _register(service)
    receipt = _transaction("receipt", 10_000_000)
    _apply(service, _snapshot(receipt))
    service.release_payment_hold(PROVIDER, ACCOUNT, "test", "payment", "fulfilled-before-claim")
    claim = _snapshot(receipt, status="partially_refunded")
    claim["pending_liability_micro_usd"] = 2_000_000
    _apply(service, claim)
    assert service.balance("run")["available_micro_usd"] == 13_000_000
    service.release_payment_hold(PROVIDER, ACCOUNT, "test", "payment", "operator-repeats-fulfillment")
    assert service.balance("run")["available_micro_usd"] == 13_000_000
    assert service.balance("run")["held_micro_usd"] == 2_000_000


def test_pending_refund_debit_cannot_hide_behind_a_positive_fee_rebate(service):
    _register(service)
    refund = _transaction("pending-refund", -1_000_000, fee=-30_000, kind="refund", settled=False)
    _apply(service, _snapshot(refund, status="partially_refunded"))
    assert service.balance("run")["posted_micro_usd"] == 4_000_000
    refund["settled"] = True
    _apply(service, _snapshot(refund, status="partially_refunded"))
    assert service.balance("run")["posted_micro_usd"] == 4_030_000


@pytest.mark.parametrize("table", ["ledger", "events", "operations", "charges", "payment_transactions", "payment_events", "payment_components", "reconciliations"])
@pytest.mark.parametrize("mutation", ["update", "delete"])
def test_database_itself_rejects_rewriting_durable_accounting_records(service, tmp_path, table, mutation):
    _register(service)
    _apply(service, _snapshot(_transaction("receipt", 1_000_000)), event_id="event", event_digest="digest")
    reservation = _reserve(service, "billed", 1_000_000)
    service.start_reservation(reservation["reservation_id"])
    service.settle(reservation["reservation_id"], 500_000, PROVIDER, ACCOUNT, "billed-charge")
    before = service.balance("run")
    journal_before = service.ledger("run")
    with sqlite3.connect(tmp_path / "accounting.sqlite3") as connection:
        assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] > 0
        statement = f"UPDATE {table} SET rowid=rowid" if mutation == "update" else f"DELETE FROM {table}"
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(statement)
    assert service.balance("run") == before
    assert service.ledger("run") == journal_before


def test_newer_schema_is_rejected_without_downgrading_or_mutating_records(service, tmp_path):
    before = service.ledger("run")
    database = tmp_path / "accounting.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA user_version=42")
    with pytest.raises(EconomyError) as caught:
        EconomyService(database)
    assert caught.value.code == "future_schema"
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 42
    assert service.ledger("run") == before


def test_new_liability_invalidates_dispatch_but_preserves_recovery(service):
    reservation = _reserve(service, "not-yet-dispatched", 5_000_000)
    assert service.balance("run")["available_micro_usd"] == 0
    service.set_hold("run", "refund-risk", 1_000_000, "refund", "new claim", "claim")
    with pytest.raises(EconomyError):
        service.start_reservation(reservation["reservation_id"])
    assert service.reservation(reservation["reservation_id"])["state"] == "reserved"
    service.cancel_reservation(reservation["reservation_id"])
    assert service.balance("run")["available_micro_usd"] == 4_000_000


def test_exactly_funded_reservation_can_dispatch_at_zero_unreserved_credit(service):
    reservation = _reserve(service, "exact-budget", 5_000_000)
    assert service.balance("run")["available_micro_usd"] == 0
    service.start_reservation(reservation["reservation_id"])
    with pytest.raises(EconomyError):
        service.start_reservation(reservation["reservation_id"])
    service.settle(reservation["reservation_id"], 5_000_000, PROVIDER, ACCOUNT, "exact-charge")
    assert service.balance("run")["available_micro_usd"] == 0


@pytest.mark.parametrize("table", ["ledger", "events", "operations", "charges", "payment_transactions", "payment_events", "reconciliations", "payment_components"])
def test_sql_replace_cannot_rewrite_immutable_identity(service, table):
    """SQLite REPLACE otherwise bypasses UPDATE/DELETE triggers with recursive_triggers off."""
    reservation = _reserve(service, "replace-charge", 100_000)
    service.start_reservation(reservation["reservation_id"])
    service.settle(reservation["reservation_id"], 50_000, PROVIDER, ACCOUNT, "replace-charge")
    _register(service)
    _apply(service, _snapshot(_transaction("replace-payment", 1_000_000)), event_id="replace-event", event_digest="digest")
    with sqlite3.connect(service.path) as connection:
        assert connection.execute("PRAGMA recursive_triggers").fetchone()[0] == 0
        assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] > 0
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(f"INSERT OR REPLACE INTO {table} SELECT * FROM {table} LIMIT 1")
    assert service.balance("run")["posted_micro_usd"] == 5_950_000
