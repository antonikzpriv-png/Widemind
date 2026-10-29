"""Standalone SDK accounting must not become a central-runtime wallet fallback.

All paid adapters and receipts below are synthetic; no provider is contacted.
"""

import pytest

from widemind.gateway import Gateway
from widemind.tools.contracts import Quote, Reconciliation, ToolError, ToolResult, ToolSpec, Uncertain, Usage


class FixturePaid:
    spec = ToolSpec("fixture.paid", "Synthetic SDK billing adapter", "external")

    def __init__(self):
        self.calls = self.observations = 0

    def availability(self, config, secrets):
        return {"available": True}

    def quote(self, arguments, config):
        return Quote(100, "fixture-provider", True, {"max_tokens": 10})

    def execute(self, arguments, context):
        context.check()
        context.mark_dispatched({"fixture": True})
        self.calls += 1
        if arguments.get("uncertain"):
            raise Uncertain("Fixture provider response unavailable")
        return ToolResult({"fixture": "done"}, Usage(25, {"tokens": 2}))

    def reconcile(self, arguments, context):
        context.check()
        self.observations += 1
        return Reconciliation("completed", ToolResult({"fixture": "observed"}, Usage(25, {"tokens": 2})))


class FixtureFree:
    spec = ToolSpec("fixture.free", "Synthetic free owner tool", "local")

    def __init__(self):
        self.calls = 0

    def availability(self, config, secrets):
        return {"available": True}

    def quote(self, arguments, config):
        return Quote()

    def execute(self, arguments, context):
        context.check()
        self.calls += 1
        return ToolResult({"fixture": "free"}, Usage(0))


@pytest.fixture
def sdk(tmp_path, monkeypatch):
    for variable in ("WIDEMIND_ECONOMY_URL", "WIDEMIND_RUNTIME_AUTH_SECRET"):
        monkeypatch.delenv(variable, raising=False)
    config = {"control_dir": str(tmp_path / "control"), "workspace_dir": str(tmp_path / "workspace"),
              "policy": {"profile": "development"},
              "tools": {"fixture.paid": {"enabled": True}, "fixture.free": {"enabled": True}}}
    paid, free = FixturePaid(), FixtureFree()
    gateway = Gateway(config, adapters=[paid, free], transport=object())
    gateway.store.create_run("sdk-development", 500)
    return gateway, paid, free, config


@pytest.mark.parametrize("selection", ["url", "secret", "owner-policy"])
def test_central_selection_blocks_paid_admission_before_local_prepare_or_dispatch(sdk, monkeypatch, selection):
    gateway, paid, _, _ = sdk
    if selection == "url":
        monkeypatch.setenv("WIDEMIND_ECONOMY_URL", "http://127.0.0.1:8080")
    elif selection == "secret":
        monkeypatch.setenv("WIDEMIND_RUNTIME_AUTH_SECRET", "fixture-only-signing-key")
    else:
        gateway.config["policy"]["accounting_authority"] = "central"
    balance = gateway.store.balance("sdk-development")
    with pytest.raises(ToolError) as failure:
        gateway.execute("fixture.paid", {}, run_id="sdk-development", action_id="blocked")
    assert failure.value.code == "central_economy_required"
    assert paid.calls == 0
    assert gateway.store.balance("sdk-development") == balance
    with gateway.store.connection() as db:
        assert db.execute("SELECT count(*) FROM actions").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM ledger").fetchone()[0] == 1


def test_owner_standalone_label_cannot_override_inherited_central_authority(sdk, monkeypatch):
    gateway, paid, _, _ = sdk
    gateway.config["policy"]["accounting_authority"] = "standalone"
    monkeypatch.setenv("WIDEMIND_ECONOMY_URL", "http://127.0.0.1:8080")
    with pytest.raises(ToolError, match="central runtime accounting"):
        gateway.execute("fixture.paid", {}, run_id="sdk-development", action_id="blocked")
    assert paid.calls == 0


def test_paid_terminal_replay_keeps_identity_checks_when_central_accounting_is_enabled(sdk, monkeypatch):
    gateway, paid, _, _ = sdk
    original = gateway.execute("fixture.paid", {}, run_id="sdk-development", action_id="finished")
    before = gateway.store.balance("sdk-development")
    monkeypatch.setenv("WIDEMIND_ECONOMY_URL", "http://127.0.0.1:8080")
    assert gateway.execute("fixture.paid", {}, run_id="sdk-development", action_id="finished") == original
    with pytest.raises(ToolError, match="different inputs"):
        gateway.execute("fixture.paid", {"changed": True}, run_id="sdk-development", action_id="finished")
    assert paid.calls == 1
    assert gateway.store.balance("sdk-development") == before


def test_old_uncertain_paid_action_can_be_observed_but_cannot_redispatch(sdk, monkeypatch):
    gateway, paid, _, _ = sdk
    pending = gateway.execute("fixture.paid", {"uncertain": True}, run_id="sdk-development", action_id="uncertain")
    assert pending["status"] == "uncertain"
    before = gateway.store.balance("sdk-development")
    monkeypatch.setenv("WIDEMIND_RUNTIME_AUTH_SECRET", "fixture-central-key")
    with pytest.raises(ToolError, match="recover existing standalone actions with reconcile"):
        gateway.execute("fixture.paid", {"uncertain": True}, run_id="sdk-development", action_id="uncertain")
    assert gateway.store.balance("sdk-development") == before
    assert gateway.reconcile("uncertain")["status"] == "succeeded"
    assert paid.calls == 1 and paid.observations == 1
    assert gateway.store.balance("sdk-development")["spendable_microusd"] == 475


def test_prepared_but_undispatched_paid_action_cannot_start_after_authority_changes(sdk, monkeypatch):
    gateway, paid, _, config = sdk
    gateway.store.prepare(action_id="prepared", run_id="sdk-development", job_id=None, tool="fixture.paid",
                          version=1, arguments={}, config=config["tools"]["fixture.paid"], policy=config["policy"],
                          quote=paid.quote({}, {}))
    before = gateway.store.balance("sdk-development")
    monkeypatch.setenv("WIDEMIND_ECONOMY_URL", "http://127.0.0.1:8080")
    with pytest.raises(ToolError):
        gateway.execute("fixture.paid", {}, run_id="sdk-development", action_id="prepared")
    assert gateway.store.get("prepared")["status"] == "planned"
    assert gateway.store.balance("sdk-development") == before
    assert paid.calls == 0


def test_free_tools_status_and_private_secret_setup_remain_available(sdk, monkeypatch):
    gateway, paid, free, _ = sdk
    monkeypatch.setenv("WIDEMIND_ECONOMY_URL", "http://127.0.0.1:8080")
    assert gateway.status()["tools"]["fixture.free"]["available"] is True
    gateway.secrets.set("owner-fixture", "not-a-real-key")
    assert gateway.secrets.get("owner-fixture") == "not-a-real-key"
    before = gateway.store.balance("sdk-development")
    result = gateway.execute("fixture.free", {}, run_id="sdk-development", action_id="free")
    assert result["status"] == "succeeded"
    assert free.calls == 1 and paid.calls == 0
    assert gateway.store.balance("sdk-development") == before


def test_task_arguments_never_choose_the_accounting_authority(sdk):
    gateway, paid, _, _ = sdk
    result = gateway.execute("fixture.paid", {"accounting_authority": "central", "WIDEMIND_ECONOMY_URL": "task-data"},
                             run_id="sdk-development", action_id="standalone-owner-action")
    assert result["status"] == "succeeded"
    assert paid.calls == 1


def test_task_data_cannot_select_a_standalone_wallet_under_central_authority(sdk, monkeypatch):
    gateway, paid, _, _ = sdk
    monkeypatch.setenv("WIDEMIND_ECONOMY_URL", "http://127.0.0.1:8080")
    with pytest.raises(ToolError) as failure:
        gateway.execute("fixture.paid", {"accounting_authority": "standalone", "WIDEMIND_ECONOMY_URL": ""},
                        run_id="sdk-development", action_id="task-cannot-bypass")
    assert failure.value.code == "central_economy_required"
    assert paid.calls == 0
