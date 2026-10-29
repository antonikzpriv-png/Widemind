"""Integrated creative registration and authoritative reservation bindings.

All money/service rows below are explicitly local test fixtures. Graphics are
actually rendered; no model, payment, external provider or code container is used.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from widemind.integrated_service import IntegratedTools
from widemind.tools.contracts import ToolError, Uncertain, Usage


class Economy:
    def __init__(self):
        self.rows, self.starts, self.settlements = {}, [], []

    def add(self, action, reservation_id="creative-reservation"):
        self.rows[reservation_id] = {
            "reservation_id": reservation_id, "run_id": action["runId"], "action_id": action["actionId"],
            "provider": action["tool"], "account": "dedicated-tools", "amount_micro_usd": 0,
            "limits": {"max_duration_ms": 30000, "max_storage_bytes": 1000000}, "state": "reserved",
        }
        return {"reservationId": reservation_id, "runId": action["runId"], "actionId": action["actionId"],
                "limit": deepcopy(action["limit"]), "authorization": "synthetic-opaque-value-is-not-authority"}

    def reservation(self, reservation_id):
        return deepcopy(self.rows[reservation_id])

    def start(self, reservation_id):
        assert self.rows[reservation_id]["state"] == "reserved", "Duplicate dispatch is forbidden"
        self.rows[reservation_id]["state"] = "running"
        self.starts.append(reservation_id)

    def settle(self, reservation_id, receipt):
        self.settlements.append((reservation_id, deepcopy(receipt)))
        self.rows[reservation_id]["state"] = "unknown" if receipt["usage"]["provisional"] else "settled"
        return {}


def graphic_action():
    source = Path(__file__).resolve().parents[1] / "examples" / "creative-graphics"
    return {"version": 1, "actionId": "synthetic-creative-graphic", "runId": "synthetic-run", "jobId": "synthetic-job",
            "workKey": "synthetic-graphic-work", "tool": "creative.graphics",
            "input": {"scope": json.loads((source / "brief.json").read_text()),
                      "design": json.loads((source / "design.json").read_text())},
            "limit": {"maximum": {"currency": "USD", "micros": "0"}, "maxDurationMs": 30000, "maxOutputBytes": 1000000}}


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    pytest.importorskip("PIL")
    monkeypatch.delenv("WIDEMIND_RUNTIME_DATABASE", raising=False)
    monkeypatch.setenv("WIDEMIND_TOOLS_ACCOUNT", "dedicated-tools")
    economy = Economy()
    config = {"control_dir": str(tmp_path / "control"), "workspace_dir": str(tmp_path / "workspace"),
              "policy": {"profile": "development", "allow_external_effects": False},
              "tools": {"files": {"enabled": True}, "graphics": {"enabled": True},
                        "creative.graphics": {"enabled": True}}}
    def forbidden(*_args, **_kwargs):
        raise AssertionError("No external provider call belongs in a local graphics fixture")
    secrets = SimpleNamespace(get=lambda _name: None, set=forbidden)
    tools = IntegratedTools(config, economy, secrets=secrets, transport=SimpleNamespace(request=forbidden))
    yield tools, economy, config, secrets
    tools.workspace.close()


def test_registered_graphics_render_with_authoritative_zero_cost_and_replay_without_dispatch(fixture):
    tools, economy, config, secrets = fixture
    assert "creative.graphics" in tools.adapters and "creative.software" in tools.adapters
    capabilities = {row["id"]: row["availability"] for row in tools.capabilities()}
    assert capabilities["creative.graphics"]["available"] is True
    action = graphic_action()
    reservation = economy.add(action)
    payload = {"action": action, "reservation": reservation}
    result = tools.execute(payload)
    assert result["kind"] == "receipt"
    receipt = result["receipt"]
    assert receipt["status"] == "succeeded"
    assert receipt["usage"]["actualCharge"] == {"currency": "USD", "micros": "0"}
    assert receipt["usage"]["provider"] == "creative.graphics"
    assert receipt["result"]["data"]["verified"] is True
    assert economy.starts == ["creative-reservation"]
    files = receipt["result"]["data"]["files"]
    for item in files:
        raw = tools.workspace.read_bytes(item["path"])
        assert len(raw) == item["size"] and hashlib.sha256(raw).hexdigest() == item["sha256"]
    economy.settle(reservation["reservationId"], receipt)
    assert tools.execute(payload) == result
    assert tools.reconcile(payload) == result
    restarted = IntegratedTools(config, economy, secrets=secrets)
    try:
        assert restarted.reconcile(payload) == result
        assert restarted.execute(payload) == result
        assert len(restarted.workspace.list_files()) == 2
    finally:
        restarted.workspace.close()
    assert economy.starts == ["creative-reservation"]
    assert len(economy.settlements) == 1
    assert not (Path(config["control_dir"]) / "control.sqlite3").exists(), "No second wallet is created"


@pytest.mark.parametrize("binding", ["run_id", "action_id", "provider", "account"])
def test_authoritative_binding_mismatch_prevents_graphic_mutation(fixture, binding):
    tools, economy, _config, _secrets = fixture
    action = graphic_action()
    reservation = economy.add(action)
    economy.rows[reservation["reservationId"]][binding] = "another-identity"
    with pytest.raises(ToolError, match="binding"):
        tools.execute({"action": action, "reservation": reservation})
    assert economy.starts == [] and tools.workspace.list_files() == []


@pytest.mark.parametrize("dependency", ["files", "graphics"])
def test_disabled_owner_dependency_is_unavailable_and_enforced_before_dispatch(fixture, dependency):
    tools, economy, config, _secrets = fixture
    config["tools"][dependency]["enabled"] = False
    capabilities = {row["id"]: row["availability"] for row in tools.capabilities()}
    assert capabilities["creative.graphics"]["available"] is False
    action = graphic_action()
    reservation = economy.add(action)
    result = tools.execute({"action": action, "reservation": reservation})
    assert result["kind"] == "receipt" and result["receipt"]["status"] == "failed"
    assert result["receipt"]["result"]["data"]["error"]["code"] == "unavailable"
    assert economy.starts == [] and tools.workspace.list_files() == []


def test_failed_visual_checks_are_terminal_observations_not_unknown_effects(fixture):
    tools, economy, _config, _secrets = fixture
    action = graphic_action()
    action["input"]["design"]["elements"][4]["x"] = 799
    reservation = economy.add(action)
    payload = {"action": action, "reservation": reservation}
    result = tools.execute(payload)
    assert result["kind"] == "receipt"
    data = result["receipt"]["result"]["data"]
    assert data["verified"] is False and data["checks"]["no_clipping_or_overflow"] is False
    assert "files" not in data
    assert tools.reconcile(payload) == result
    assert tools.execute(payload) == result
    assert economy.starts == ["creative-reservation"] and tools.workspace.list_files() == []


def test_changed_owner_configuration_fences_pending_recovery_then_original_restores_hashes(fixture, monkeypatch):
    tools, economy, config, secrets = fixture
    adapter = tools.adapters["creative.graphics"]
    execute = adapter.execute
    def interrupted(arguments, ctx):
        execute(arguments, ctx)
        raise Uncertain("Synthetic process interruption after complete local output", usage=Usage(0))
    monkeypatch.setattr(adapter, "execute", interrupted)
    action = graphic_action()
    reservation = economy.add(action)
    payload = {"action": action, "reservation": reservation}
    first = tools.execute(payload)
    assert first["kind"] == "unknown"
    assert len(tools.workspace.list_files()) == 2
    config["tools"]["graphics"]["max_width"] = 1600
    assert tools.reconcile(payload)["kind"] == "unknown"
    assert tools.execute(payload) == first
    assert economy.starts == ["creative-reservation"]
    del config["tools"]["graphics"]["max_width"]
    restarted = IntegratedTools(config, economy, secrets=secrets)
    try:
        recovered = restarted.reconcile(payload)
        assert recovered["kind"] == "receipt"
        assert recovered["receipt"]["result"]["data"]["verified"] is True
        for item in recovered["receipt"]["result"]["data"]["files"]:
            assert hashlib.sha256(restarted.workspace.read_bytes(item["path"])).hexdigest() == item["sha256"]
        assert restarted.execute(payload) == recovered
    finally:
        restarted.workspace.close()
    assert economy.starts == ["creative-reservation"]


def test_software_runtime_settings_are_selected_only_from_existing_owner_code_adapter(fixture):
    tools, economy, config, _secrets = fixture
    approved = {"enabled": True, "image": "sha256:" + "1" * 64, "owner_compute_subsidy": True, "wall_seconds": 12}
    config["tools"]["code.execute"] = deepcopy(approved)
    config["tools"]["creative.software"] = {"enabled": True, "verification_runtime": "node",
                                            "code_settings": {"image": "unapproved-custom-runtime", "wall_seconds": 999999}}
    action = graphic_action()
    action["tool"] = "creative.software"
    # This is intentionally invalid input. An untrusted caller cannot select a
    # container image or replace owner resource limits through model output.
    action["input"]["code_settings"] = {"image": "customer-runtime", "wall_seconds": 999999}
    reservation = economy.add(action)
    settings = tools.settings("creative.software")
    assert settings["code_settings"] == approved
    fingerprint = tools.owner_fingerprint(action)
    tools.journal.prepare(action, reservation, fingerprint)
    _adapter, arguments, selected = tools._adapter(action)
    assert arguments["code_settings"]["image"] == "customer-runtime"
    assert selected["code_settings"] == approved
    with pytest.raises(ToolError, match="frozen calculator scope"):
        _adapter.quote(arguments, selected)
    assert economy.starts == [] and tools.workspace.list_files() == []
    config["tools"]["code.execute"]["image"] = "sha256:" + "2" * 64
    assert tools.owner_fingerprint(action) != fingerprint
    with pytest.raises(ToolError, match="changed"):
        tools._adapter(action)


def test_existing_adapter_owner_fingerprints_keep_saved_pending_operations_compatible(fixture):
    from widemind.integrated_service import canonical
    tools, economy, config, _secrets = fixture
    for name in ("files", "graphics", "ai.generate", "software.create"):
        old_value = {"tool_config": config.get("tools", {}).get(name, {}),
                     "policy": config.get("policy", {}), "workspace": str(tools.workspace.root),
                     "adapter_version": tools.adapters[name].spec.version}
        old_fingerprint = hashlib.sha256(canonical(old_value).encode()).hexdigest()
        assert tools.owner_fingerprint({"tool": name}) == old_fingerprint
