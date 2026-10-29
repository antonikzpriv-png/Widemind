"""Independent regression checks for cash corrections during cost reconciliation.

All amounts and provider observations are fixtures, not real provider charges.
"""
import pytest

from widemind.economy.service import EconomyError, EconomyService


def uncertain_attempt(tmp_path):
    service = EconomyService(tmp_path / "accounting.sqlite3")
    service.create_run("run", mode="test")
    reservation = service.reserve("run", "attempt", 1_000_000, "reserve", "fixture", "sandbox", {"max_tokens": 100})
    service.start_reservation(reservation["reservation_id"])
    service.settle(reservation["reservation_id"], 1_000_000, "fixture", "sandbox", "provider-charge", outcome="unknown")
    entry = next(row for row in service.ledger("run") if row["kind"] == "operating_charge")
    return service, reservation["reservation_id"], entry["entry_id"]


@pytest.mark.parametrize("delta", [1_000_000, -1_000_000, 0])
def test_cash_correction_cannot_bypass_unresolved_cost_projection(tmp_path, delta):
    service, reservation_id, entry_id = uncertain_attempt(tmp_path)
    before_balance = service.balance("run")
    before_journal = service.ledger("run")
    before_events = service.events("run")
    with pytest.raises(EconomyError):
        service.correct_entry("run", entry_id, delta, "Reconcile preliminary provider subtotal", "correction")
    assert service.balance("run") == before_balance
    assert service.ledger("run") == before_journal
    assert service.events("run") == before_events
    assert service.reservation(reservation_id)["state"] == "unknown"

    # The provider's verified final cost performs the one authoritative true-up.
    service = EconomyService(service.path)
    service.settle(reservation_id, 0, "fixture", "sandbox", "provider-charge")
    assert service.balance("run")["posted_micro_usd"] == 5_000_000
    assert service.balance("run")["available_micro_usd"] == 5_000_000
    assert service.balance("run")["reserved_micro_usd"] == 0


def test_final_cost_can_still_receive_a_linked_cash_compensation(tmp_path):
    service, reservation_id, _ = uncertain_attempt(tmp_path)
    service.settle(reservation_id, 500_000, "fixture", "sandbox", "provider-charge")
    entries = service.ledger("run")
    final_entry = entries[-1]
    result = service.correct_entry("run", final_entry["entry_id"], 100_000,
                                   "Verified later provider billing credit", "verified-credit")
    assert service.balance("run")["available_micro_usd"] == 4_600_000
    correction = service.ledger("run")[-1]
    assert correction["entry_id"] == result["entry_id"]
    assert correction["metadata"]["corrects_entry_id"] == final_entry["entry_id"]
    assert service.ledger("run")[:-1] == entries
    # Operator replay does not repeat the subsequent verified credit.
    assert service.correct_entry("run", final_entry["entry_id"], 100_000,
                                 "Verified later provider billing credit", "verified-credit") == result
    assert service.balance("run")["available_micro_usd"] == 4_600_000
