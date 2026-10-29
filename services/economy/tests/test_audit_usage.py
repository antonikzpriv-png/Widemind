"""A verified bill cannot disappear because optional provider diagnostics are bad.

Fixture adapters model unexpected overages; no real paid provider calls occur.
"""
import json

import pytest

from widemind.economy.gateway import PaidGateway, UsageReceipt
from widemind.economy.service import EconomyError, EconomyService


class DiagnosticFixtureAdapter:
    provider, account, mode = "fixture-billing", "sandbox", "test"

    def __init__(self, usage, *, outcome="succeeded"):
        self.usage, self.outcome, self.calls = usage, outcome, 0

    def quote(self, limits):
        return 1_000_000

    def execute(self, *, limits, idempotency_key):
        self.calls += 1
        return UsageReceipt("verified-charge", 6_000_000, self.usage, self.outcome)


def start_fixture(tmp_path, usage, outcome="succeeded"):
    service = EconomyService(tmp_path / "accounting.sqlite3")
    service.create_run("run", mode="test")
    reservation = service.reserve("run", "attempt", 1_000_000, "reserve", "fixture-billing", "sandbox", {"max_tokens": 100})
    adapter = DiagnosticFixtureAdapter(usage, outcome=outcome)
    return service, reservation["reservation_id"], adapter, PaidGateway(service, [adapter])


@pytest.mark.parametrize("usage", [
    pytest.param({"raw_provider_report": "x" * 70_000}, id="oversized-report"),
    pytest.param({"provider_measurement": float("nan")}, id="nonfinite-number"),
    pytest.param({"provider_value": object()}, id="nonserializable-object"),
    pytest.param({1: "integer-key", "other": "text-key"}, id="mixed-object-keys"),
])
@pytest.mark.parametrize("outcome", ["succeeded", "failed"])
def test_optional_diagnostics_cannot_hide_verified_overage(tmp_path, usage, outcome):
    service, reservation_id, adapter, gateway = start_fixture(tmp_path, usage, outcome)
    result = gateway.execute(reservation_id)
    assert result["state"] == "settled"
    balance = service.balance("run")
    assert balance["posted_micro_usd"] == -1_000_000
    assert balance["available_micro_usd"] == -1_000_000
    assert balance["reserved_micro_usd"] == 0
    assert balance["exhausted"] is True
    assert adapter.calls == 1
    charge = service.charges("run")[0]
    assert charge["amount_micro_usd"] == 6_000_000
    assert charge["outcome"] == outcome
    serialized = json.dumps(charge["usage"], allow_nan=False, sort_keys=True)
    assert len(serialized.encode()) <= 20_000
    assert "x" * 1000 not in serialized

    with pytest.raises(EconomyError):
        service.reserve("run", "overspend", 1, "overspend-key", "fixture-billing", "sandbox", {"max_tokens": 1})
    with pytest.raises(EconomyError):
        gateway.execute(reservation_id)
    assert adapter.calls == 1

    # Replay the actual stored supporting record without posting the bill again.
    service.settle(reservation_id, 6_000_000, "fixture-billing", "sandbox", "verified-charge",
                   usage=charge["usage"], outcome=outcome)
    assert service.balance("run")["posted_micro_usd"] == -1_000_000
    assert len(service.charges("run")) == 1


def test_small_valid_usage_is_preserved_with_actual_bill(tmp_path):
    usage = {"input_tokens": 10, "output_tokens": 20, "request_id": "fixture-request"}
    service, _, _, gateway = start_fixture(tmp_path, usage)
    gateway.execute(service.reservations("run")[0]["reservation_id"])
    assert service.charges("run")[0]["usage"] == usage
    assert service.balance("run")["posted_micro_usd"] == -1_000_000


def test_malformed_diagnostics_do_not_erase_known_unknown_outcome_subtotal(tmp_path):
    service, reservation_id, _, gateway = start_fixture(tmp_path, {"raw_report": "x" * 70_000}, "unknown")
    assert gateway.execute(reservation_id)["state"] == "unknown"
    balance = service.balance("run")
    assert balance["posted_micro_usd"] == -1_000_000
    assert balance["reserved_micro_usd"] == 0  # Known subtotal already exceeds the original cap.
    assert balance["exhausted"] is True
    assert service.charges("run")[0]["final"] is False
    restarted = EconomyService(service.path)
    assert restarted.balance("run")["posted_micro_usd"] == -1_000_000
    restarted.settle(reservation_id, 2_000_000, "fixture-billing", "sandbox", "verified-charge",
                     usage={"final_provider_units": 200})
    assert restarted.balance("run")["posted_micro_usd"] == 3_000_000
    assert restarted.balance("run")["available_micro_usd"] == 3_000_000
