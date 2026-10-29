"""Compiler, recovery and real opt-in Docker verification; no paid providers."""
import base64
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import os
from types import SimpleNamespace
import time
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from widemind.tools.code import CodeAdapter
from widemind.tools.contracts import Reconciliation, ToolContext, ToolError, ToolResult, Usage
from widemind.tools.creative_software import CreativeSoftwareAdapter, compile_files, validate_scope, verifier_arguments
from widemind.tools import creative_software as software
from widemind.workspace import DedicatedWorkspace


@pytest.fixture(autouse=True)
def isolated_capability_probe_cache():
    # Mocked negative probes must not contaminate the subsequent real image test.
    from widemind.tools.creative_software import _PROBES
    _PROBES.clear()
    yield
    _PROBES.clear()


def arguments():
    return {
        "scope": {
            "title": "Harbor Studio project estimator",
            "description": "Calculate a project estimate from hours and a customer-supplied hourly rate.",
            "category": "calculator",
            "inputs": [
                {"id": "hours", "label": "Project hours", "min": 0, "max": 500, "default": 8},
                {"id": "rate", "label": "Hourly rate", "min": 1, "max": 1000, "default": 60},
            ],
            "outputs": [{"id": "estimate", "label": "Estimated total"}],
            "acceptance_cases": [
                {"name": "Eight-hour project", "inputs": {"hours": 8, "rate": 60}, "expected": {"estimate": 480}, "tolerance": 0},
                {"name": "Fractional hours", "inputs": {"hours": 2.5, "rate": 80}, "expected": {"estimate": 200}, "tolerance": 0.000001},
                {"name": "No hours", "inputs": {"hours": 0, "rate": 60}, "expected": {"estimate": 0}, "tolerance": 0},
            ],
            "requirements": ["Multiply entered hours by hourly rate.", "Reject values outside the quoted ranges.", "Update results interactively without any network request."],
            "max_revisions": 1,
        },
        "design": {"outputs": [{"id": "estimate", "expression": {"op": "multiply", "args": [{"input": "hours"}, {"input": "rate"}]}}], "accent": "#b6ddd8", "heading": "Price your next project", "description": "An interactive estimator for Harbor Studio."},
    }


def settings():
    return {"enabled": True, "verification_runtime": "node", "code_settings": {"enabled": True, "image": os.environ.get("WIDEMIND_CREATIVE_IMAGE", "sha256:" + "1" * 64), "owner_compute_subsidy": True, "wall_seconds": 30, "memory_bytes": 134217728, "cpu_millis": 1000, "pids": 32}}


@pytest.fixture
def ctx(tmp_path):
    records = {}
    class State:
        def load(self, action): return deepcopy(records.get(action, {}))
        def update(self, action, patch): records.setdefault(action, {}).update(deepcopy(patch))
        def dispatched(self, action, intent): self.update(action, {"external_intent": dict(intent)})
    with DedicatedWorkspace(tmp_path / "files") as workspace:
        yield ToolContext("software-fixture", "run", "reservation", settings(), {}, workspace, SimpleNamespace(get=lambda _: None), None, State(), time.monotonic() + 90, lambda: None, job_id="job")


def fixture_report(args, *, failed=False):
    source = compile_files(args["scope"], args["design"])["calculator.js"][1]
    value = {"version": 1, "passed": True, "acceptance_cases": len(args["scope"]["acceptance_cases"]), "invalid_inputs_rejected": 22, "boundaries_checked": len(args["scope"]["inputs"]) * 2, "source_sha256": hashlib.sha256(source.encode()).hexdigest()}
    return ToolResult({"exit_code": 1 if failed else 0, "stdout": "" if failed else json.dumps(value), "stderr": "fixture rejection" if failed else ""}, Usage(0, units={"wall_ms": 3}, details={"fixture": "not actual Docker verification"}))


def test_compile_is_customer_specific_functional_source_and_self_contained():
    args = arguments()
    files = compile_files(**args)
    assert set(files) == {"index.html", "calculator.js", "calculator.css", "README.md", "scope.json"}
    document = files["index.html"][1]
    assert 'id="input-hours"' in document and 'id="output-estimate"' in document
    assert 'addEventListener(\'input\',render)' in document
    assert 'values["hours"]' in files["calculator.js"][1]
    assert 'values["rate"]' in files["calculator.js"][1]
    assert "*" in files["calculator.js"][1]
    assert "fetch(" not in document and "https://" not in document and "<script src=" not in document
    assert "Harbor Studio" in files["README.md"][1]
    assert "sandbox=\"allow-scripts\"" in files["README.md"][1]
    assert json.loads(files["scope.json"][1]) == args["scope"]


def test_customer_markup_is_data_and_cannot_escape_generated_script_or_document():
    args = arguments()
    attack = '</script><img src="https://private.invalid" onerror="alert(1)">EXPRESSIONS'
    args["scope"]["title"] = attack
    args["scope"]["inputs"][0]["label"] = attack
    args["design"]["heading"] = attack
    files = compile_files(**args)
    assert attack not in files["index.html"][1]
    assert "\\u003c/script>" in files["calculator.js"][1]
    assert "EXPRESSIONS" in files["calculator.js"][1]
    assert files["index.html"][1].count("<script>") == 1


@pytest.mark.parametrize("expression", [
    "require('fs').readFileSync('/etc/passwd')", {"input": "constructor"}, {"input": []},
    {"op": "eval", "args": [1, 2]}, {"op": [], "args": [1, 2]},
    {"op": "pow", "args": [2, 999]}, {"op": "pow", "args": [2, {"input": "hours"}]},
    {"op": "divide", "args": [1]}, True, float("nan"),
])
def test_model_cannot_inject_source_or_unbounded_arithmetic(expression):
    args = arguments()
    args["design"]["outputs"][0]["expression"] = expression
    with pytest.raises(ToolError):
        compile_files(**args)


def test_expression_depth_is_bounded():
    args = arguments()
    expression = 1
    for _ in range(10): expression = {"op": "add", "args": [1, expression]}
    args["design"]["outputs"][0]["expression"] = expression
    with pytest.raises(ToolError, match="depth"):
        compile_files(**args)


@pytest.mark.parametrize("patch", [
    lambda s: s.update(category="arbitrary-app"),
    lambda s: s.update(max_revisions=3),
    lambda s: s.update(acceptance_cases=[s["acceptance_cases"][0]]),
    lambda s: s["inputs"][0].update(default=99999),
    lambda s: s["inputs"][0].update(id="__proto__"),
    lambda s: s["acceptance_cases"][0].update(expected={"unrequested": 7}),
    lambda s: s["acceptance_cases"][0].update(tolerance=-1),
    lambda s: s["acceptance_cases"][0]["inputs"].update(rate=True),
])
def test_scope_needs_independent_bounded_observable_acceptance(patch):
    args = arguments()
    patch(args["scope"])
    with pytest.raises(ToolError): validate_scope(args["scope"])


def test_missing_sandbox_remains_unavailable_and_never_uses_a_host_fallback(monkeypatch):
    adapter = CreativeSoftwareAdapter()
    assert adapter.availability({}, None)["available"] is False
    monkeypatch.setattr(CodeAdapter, "availability", lambda *a: {"available": False, "message": "No pinned local image"})
    assert adapter.availability(settings(), None) == {"available": False, "reason": "No pinned local image"}


def test_disabled_code_capability_and_small_output_caps_fail_before_execution():
    config = settings()
    config["code_settings"]["enabled"] = False
    with pytest.raises(ToolError, match="separately enable"):
        CreativeSoftwareAdapter().quote(arguments(), config)
    config = settings()
    config["max_output_bytes"] = 4000
    with pytest.raises(ToolError, match="output cap"):
        CreativeSoftwareAdapter().quote(arguments(), config)


def reset_probe_cache():
    with software._PROBE_CACHE_LOCK:
        software._PROBES.clear()
        software._PROBE_LOCKS.clear()


def test_node_probe_uses_validated_owner_resources_and_bounded_startup_deadline(monkeypatch):
    reset_probe_cache()
    owner = settings()["code_settings"]
    owner.update(memory_bytes=268435456, pids=64, cpu_millis=1500, wall_seconds=60)
    observed = []
    def run(argv, **options):
        observed.append((argv, options))
        return 0, b"v18.20.4\n", b""
    monkeypatch.setattr(software, "_run", run)
    before = time.monotonic()
    assert software._probe_node(owner) is True
    argv, options = observed[0]
    assert "--memory=268435456" in argv and "--memory-swap=268435456" in argv
    assert "--pids-limit=64" in argv and "--cpus=1.500" in argv
    assert "--network=none" in argv and "--read-only" in argv and "--pull=never" in argv
    assert argv[-2:] == [owner["image"], "--version"]
    assert 14 <= options["deadline"] - before <= 16
    invalid = {**owner, "pids": 10000}
    with pytest.raises(ToolError): software._probe_node(invalid)
    assert len(observed) == 1


def test_node_probe_separates_effective_limits_and_recovers_quickly_from_transient_failure(monkeypatch):
    reset_probe_cache()
    clock, calls = [100.0], []
    monkeypatch.setattr(software, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    def run(argv, **options):
        calls.append(argv)
        return (1, b"", b"synthetic transient Docker startup failure") if len(calls) == 1 else (0, b"v18.20.4\n", b"")
    monkeypatch.setattr(software, "_run", run)
    owner = settings()["code_settings"]
    assert software._probe_node(owner) is False
    clock[0] += .5
    assert software._probe_node(owner) is False and len(calls) == 1
    clock[0] += 3
    assert software._probe_node(owner) is True and len(calls) == 2
    clock[0] += 31
    assert software._probe_node(owner) is True and len(calls) == 2
    assert software._probe_node({**owner, "cpu_millis": 2000}) is True
    assert len(calls) == 3  # same image, changed owner execution envelope
    clock[0] += 301
    assert software._probe_node(owner) is True and len(calls) == 4


def test_concurrent_capability_refreshes_share_one_real_probe_result(monkeypatch):
    reset_probe_cache()
    gate, entered = threading.Event(), threading.Event()
    barrier, guard = threading.Barrier(6), threading.Lock()
    calls = []
    def run(argv, **options):
        with guard: calls.append(argv)
        entered.set()
        assert gate.wait(2), "Probe fixture was never released"
        return 0, b"v18.20.4\n", b""
    def refresh():
        barrier.wait(2)
        return software._probe_node(settings()["code_settings"])
    monkeypatch.setattr(software, "_run", run)
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(refresh) for _ in range(6)]
        assert entered.wait(2)
        threading.Event().wait(.05)
        assert len(calls) == 1
        gate.set()
        assert [future.result(2) for future in futures] == [True] * 6
    assert len(calls) == 1


def test_completed_stages_and_files_are_preserved_and_outer_receipt_recovers(ctx, monkeypatch):
    args = arguments()
    calls = []
    def execute(_adapter, payload, child):
        calls.append(child.action_id)
        assert "software_code" in child.action_id
        assert child.config["image"] == settings()["code_settings"]["image"]
        return fixture_report(args)
    monkeypatch.setattr(CodeAdapter, "execute", execute)
    adapter = CreativeSoftwareAdapter()
    result = adapter.execute(args, ctx)
    assert result.data["verified"] is True and result.data["checks"]["acceptance_cases"] == 3
    assert len(result.data["files"]) == 5
    for file in result.data["files"]:
        assert file["sha256"] == hashlib.sha256(file["content"].encode()).hexdigest()
    assert adapter.execute(args, ctx) == result
    assert len(calls) == 1
    recovered = adapter.reconcile(args, ctx)
    assert recovered.outcome == "completed" and recovered.result.data == result.data


def test_failed_verification_does_not_supply_an_accepted_deliverable(ctx, monkeypatch):
    args = arguments()
    monkeypatch.setattr(CodeAdapter, "execute", lambda *a: fixture_report(args, failed=True))
    result = CreativeSoftwareAdapter().execute(args, ctx)
    assert result.data["verified"] is False
    assert result.data["checks"]["acceptance_passed"] is False
    assert "files" not in result.data and "content" not in result.data
    assert result.artifacts  # preserved privately for diagnostics, never published as passed


def test_interrupted_verification_reconciles_without_executing_or_rewriting(ctx, monkeypatch):
    args = arguments()
    def interrupted(_adapter, _payload, child):
        child.save_state({"code_container": "fixture-owned-container"})
        raise ToolError("uncertain", "fixture interrupted")
    monkeypatch.setattr(CodeAdapter, "execute", interrupted)
    with pytest.raises(ToolError): CreativeSoftwareAdapter().execute(args, ctx)
    monkeypatch.setattr(CodeAdapter, "execute", lambda *_: pytest.fail("Reconciliation executed code"))
    monkeypatch.setattr(CodeAdapter, "reconcile", lambda *_: Reconciliation("completed", fixture_report(args)))
    recovered = CreativeSoftwareAdapter().reconcile(args, ctx)
    assert recovered.outcome == "completed" and recovered.result.data["verified"] is True


def test_partial_source_stages_can_resume_only_when_sandbox_never_started(ctx, monkeypatch):
    args = arguments()
    adapter = CreativeSoftwareAdapter()
    monkeypatch.setattr(CodeAdapter, "execute", lambda *_: (_ for _ in ()).throw(ToolError("unavailable", "fixture before launch")))
    with pytest.raises(ToolError): adapter.execute(args, ctx)
    first = ctx.workspace.read_bytes(adapter._root(ctx) + "/index.html")
    ctx.workspace.delete(adapter._root(ctx) + "/README.md")
    recovered = adapter.reconcile(args, ctx)
    assert recovered.outcome == "not_executed"
    assert ctx.workspace.read_bytes(adapter._root(ctx) + "/index.html") == first
    monkeypatch.setattr(CodeAdapter, "execute", lambda *_: fixture_report(args))
    result = adapter.execute(args, ctx)
    assert result.data["verified"] is True
    assert ctx.workspace.read_bytes(adapter._root(ctx) + "/index.html") == first


def test_changed_existing_artifact_is_not_overwritten_during_recovery(ctx, monkeypatch):
    args = arguments()
    monkeypatch.setattr(CodeAdapter, "execute", lambda *_: (_ for _ in ()).throw(ToolError("uncertain", "interrupted")))
    adapter = CreativeSoftwareAdapter()
    with pytest.raises(ToolError): adapter.execute(args, ctx)
    path = adapter._root(ctx) + "/calculator.js"
    ctx.workspace.write_bytes(path, b"altered", overwrite=True)
    monkeypatch.setattr(CodeAdapter, "reconcile", lambda *_: pytest.fail("Conflicting source reached sandbox reconciliation"))
    assert adapter.reconcile(args, ctx).outcome == "uncertain"
    assert ctx.workspace.read_bytes(path) == b"altered"


def test_cancelled_action_does_not_generate_files_or_invoke_sandbox(ctx, monkeypatch):
    monkeypatch.setattr(CodeAdapter, "execute", lambda *_: pytest.fail("Cancelled action invoked sandbox"))
    with pytest.raises(ToolError, match="cancelled"):
        CreativeSoftwareAdapter().execute(arguments(), replace(ctx, cancelled=lambda: True))
    assert ctx.workspace.list_files() == []


@pytest.mark.skipif(not os.environ.get("WIDEMIND_CREATIVE_IMAGE"), reason="Provision the approved Python+Node image and set WIDEMIND_CREATIVE_IMAGE to its immutable ID.")
def test_real_isolated_node_executes_generated_calculator_and_rejects_wrong_formula(ctx):
    args = arguments()
    adapter = CreativeSoftwareAdapter()
    health = adapter.availability(ctx.config, ctx.secrets)
    assert health["available"] is True, health
    result = adapter.execute(args, ctx)
    assert result.data["verified"] is True
    assert result.data["checks"]["generated_javascript_executed"] is True
    assert result.data["checks"]["acceptance_cases"] == 3
    assert result.data["checks"]["invalid_inputs_rejected"] >= 20
    assert result.data["checks"]["boundaries_checked"] == 4
    assert adapter.reconcile(args, ctx).outcome == "completed"
    bad = deepcopy(args)
    bad["design"]["outputs"][0]["expression"]["op"] = "add"
    rejected = adapter.execute(bad, replace(ctx, action_id="software-wrong-formula"))
    assert rejected.data["verified"] is False and "content" not in rejected.data
