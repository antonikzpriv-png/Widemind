"""Recovery and isolation regressions from the independent gateway audit.

Synthetic adapters below never contact a provider or represent live charges.
"""
import json
import time

import pytest

from widemind.control import ControlStore, JournalState
from widemind.gateway import Gateway
from widemind.tools.contracts import Quote, ToolError, ToolResult, ToolSpec, Usage
from widemind.tools.files import FilesAdapter


def prepare(store, action_id):
    store.prepare(action_id=action_id, run_id="audit", job_id=action_id,
                  tool="fixture", version=1, arguments={}, config={}, policy={}, quote=Quote())
    return store.claim(action_id, 30)


def test_action_state_cannot_read_another_jobs_private_state(tmp_path):
    store = ControlStore(tmp_path / "private")
    store.create_run("audit", 100)
    first_claim = prepare(store, "first")
    second_claim = prepare(store, "second")
    first = JournalState(store, first_claim, "first")
    second = JournalState(store, second_claim, "second")
    second.update("second", {"private_customer_record": "second-job-only"})
    assert second.load("second")["private_customer_record"] == "second-job-only"
    with pytest.raises(ToolError) as error:
        first.load("second")
    assert error.value.code == "authorization"
    with pytest.raises(ToolError):
        first.update("second", {"private_customer_record": "overridden"})
    with pytest.raises(ToolError):
        first.dispatched("second", {"wrong_action": True})


def test_expired_action_claim_cannot_mutate_recovery_state(tmp_path):
    store = ControlStore(tmp_path / "private")
    store.create_run("audit", 100)
    claim = prepare(store, "action")
    state = JournalState(store, claim, "action")
    with store.connection(True) as db:
        db.execute("UPDATE actions SET lease=? WHERE id='action'", (time.time() - 1,))
    for operation in (lambda: state.update("action", {"late": True}),
                      lambda: state.dispatched("action", {"late_effect": True})):
        with pytest.raises(ToolError) as error:
            operation()
        assert error.value.code == "authorization"


class SyntheticPaidAdapter:
    spec = ToolSpec("audit-paid", "Synthetic bounded charge fixture", "external")

    def __init__(self, data):
        self.data = data

    def availability(self, config, secrets):
        return {"available": True}

    def quote(self, arguments, config):
        return Quote(20, "synthetic-audit", True)

    def execute(self, arguments, ctx):
        ctx.mark_dispatched({"provider": "synthetic-audit", "actual_network_calls": 0})
        return ToolResult(self.data, Usage(7, {"synthetic_units": 1}))


def gateway(tmp_path, adapter, *, timeout=30):
    runtime = Gateway({"control_dir": str(tmp_path / "control"),
                       "workspace_dir": str(tmp_path / "workspace"),
                       "policy": {"profile": "development"},
                       "tools": {adapter.spec.name: {"enabled": True, "timeout_seconds": timeout}}},
                      adapters=[adapter], transport=object())
    runtime.store.create_run("audit", 100)
    return runtime


@pytest.mark.parametrize("data", [{"blob": "x" * 4_100_000}, {"not_json": object()}],
                         ids=["oversized-result", "unserializable-result"])
def test_result_persistence_failure_keeps_known_provider_charge(tmp_path, data):
    runtime = gateway(tmp_path, SyntheticPaidAdapter(data))
    receipt = runtime.execute("audit-paid", {}, run_id="audit", action_id="charged-once")
    assert receipt["charged_microusd"] == 7
    assert runtime.store.balance("audit")["ledger_microusd"] == 93
    assert len(json.dumps(receipt)) < 4_100_000
    # Replaying an action ID must neither dispatch nor charge again.
    runtime.execute("audit-paid", {}, run_id="audit", action_id="charged-once")
    assert runtime.store.balance("audit")["ledger_microusd"] == 93


def test_local_write_cancellation_after_commit_remains_recoverable(tmp_path, monkeypatch):
    runtime = gateway(tmp_path, FilesAdapter())
    original = runtime.workspace.write_bytes

    def write_then_cancel(path, data, **kwargs):
        artifact = original(path, data, **kwargs)
        runtime.store.cancel("local-committed")
        return artifact

    monkeypatch.setattr(runtime.workspace, "write_bytes", write_then_cancel)
    receipt = runtime.execute("files", {"operation": "create", "path": "delivery.txt", "text": "committed once"},
                              run_id="audit", action_id="local-committed", job_id="customer")
    assert runtime.workspace.read_bytes("delivery.txt") == b"committed once"
    assert receipt["status"] == "uncertain"
    recovered = runtime.reconcile("local-committed")
    assert recovered["status"] == "succeeded"
    assert recovered["result"]["artifacts"][0]["job_id"] == "customer"
    assert runtime.workspace.read_bytes("delivery.txt") == b"committed once"


def test_invalid_owner_timeout_cannot_strand_paid_reservation(tmp_path):
    runtime = gateway(tmp_path, SyntheticPaidAdapter({"unused": True}), timeout=0)
    try:
        runtime.execute("audit-paid", {}, run_id="audit", action_id="invalid-timeout")
    except ToolError as error:
        assert error.code == "configuration"
    assert runtime.store.balance("audit")["reserved_microusd"] == 0
    assert runtime.store.balance("audit")["ledger_microusd"] == 100
