"""Synthetic Docker-engine fixtures. Generated code is never run on the host."""
import base64
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import tempfile
import time
from unittest.mock import patch

import pytest

from widemind.tools.code import CodeAdapter, _arguments, _limits, _relative
from widemind.tools.contracts import ToolContext, ToolError, ToolResult, Unavailable, Uncertain, Usage


IMAGE = "python@sha256:" + "a" * 64
CONFIG = {"image": IMAGE, "owner_compute_subsidy": True}


class Files:
    def __init__(self, root): self.root = Path(root)
    def read_bytes(self, path, *, max_bytes=8_000_000):
        data = (self.root / path).read_bytes()
        if len(data) > max_bytes: raise ToolError("input_limit", "Fixture input too large")
        return data
    def write_bytes(self, path, data, *, overwrite=False):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("wb" if overwrite else "xb") as out: out.write(data)
        return {"path": path}


class State:
    def __init__(self): self.value = {}; self.intent = None; self.events = []
    def load(self, action): return dict(self.value)
    def update(self, action, patch): self.value.update(patch); self.events.append(("state", dict(patch)))
    def dispatched(self, action, intent): self.intent = dict(intent); self.events.append(("dispatched", intent))


def context(root, **config):
    return ToolContext("fixture-action", "fixture-run", "fixture-reservation", {**CONFIG, **config}, {},
        Files(root), None, None, State(), time.monotonic() + 30, lambda: None)


class DockerFixture:
    def __init__(self, ctx, artifacts=None):
        self.ctx, self.commands = ctx, []
        self.exists = False
        self.status = "running"
        self.artifacts = artifacts or []
        self.cid = "b" * 64
    def __call__(self, args, **kwargs):
        self.commands.append((args, kwargs))
        if kwargs.get("check"): kwargs["check"]()
        if args[:2] == ["image", "inspect"]: return 0, b"sha256:" + b"a" * 64 + b"\n", b""
        if args[0] == "create":
            assert self.ctx.state.intent is not None
            assert self.ctx.state.value["code_container"].startswith("widemind-code-")
            self.exists = True
            return 0, self.cid.encode() + b"\n", b""
        if args[0] == "start": return 0, b"started", b""
        if args[:2] == ["exec", "-i"]: return 0, b"synthetic stdout", b"synthetic stderr"
        if args[0] == "exec": return 0, json.dumps(self.artifacts).encode(), b""
        if args[0] == "inspect":
            if not self.exists: return 1, b"", b"Error: No such object: fixture"
            return 0, json.dumps({"id": self.cid, "labels": {"io.widemind.code": CodeAdapter._identity(self.ctx)}, "status": self.status}).encode(), b""
        if args[0] == "rm": self.exists = False; return 0, b"removed", b""
        raise AssertionError(args)


def test_quote_requires_explicit_owner_subsidy_and_immutable_image():
    adapter = CodeAdapter()
    for config in ({}, {"image": "python:3.12"}, {"image": IMAGE}, {**CONFIG, "memory_bytes": True}, {**CONFIG, "pids": 10000}):
        with pytest.raises(Unavailable): adapter.quote({"code": "print(1)"}, config)
    quote = adapter.quote({"code": "print(1)"}, CONFIG)
    assert quote.maximum_microusd == 0 and not quote.paid
    assert quote.provider == "docker-owner-compute"


@pytest.mark.parametrize("path", ["../secret", "/etc/passwd", "a/../x", "a//b", "a\\b", "a:b", "CON", "a/NUL.txt", "a.", "a/", ""])
def test_paths_reject_traversal_and_ambiguous_filesystem_names(path):
    with pytest.raises(ToolError): _relative(path)


@pytest.mark.parametrize("arguments", [{"code": "x", "image": "override"}, {"code": "x", "inputs": "file"}, {"code": "x", "inputs": ["A", "a"]}, {"code": ""}, {"code": "x", "command": ["bash"]}])
def test_task_cannot_supply_commands_images_or_unbounded_inputs(arguments):
    with pytest.raises(ToolError): _arguments(arguments, _limits(CONFIG))


def test_fixture_execution_is_hardened_durable_bounded_and_cached(tmp_path):
    ctx = context(tmp_path)
    (tmp_path / "input.txt").write_bytes(b"fixture input")
    docker = DockerFixture(ctx, [{"path": "result.txt", "data": base64.b64encode(b"result").decode()}])
    adapter = CodeAdapter()
    with patch("widemind.tools.code._run", docker):
        result = adapter.execute({"code": "print('fixture')", "inputs": ["input.txt"]}, ctx)
        again = adapter.execute({"code": "print('fixture')", "inputs": ["input.txt"]}, ctx)
    assert result == again
    assert not docker.exists
    creates = [args for args, _ in docker.commands if args[0] == "create"]
    assert len(creates) == 1
    argv = creates[0]
    for flag in ["--pull=never", "--network", "none", "--read-only", "--cap-drop", "ALL", "no-new-privileges", "65532:65532", "--pids-limit", "--memory", "--memory-swap", "--cpus", "--tmpfs"]:
        assert flag in argv
    mount = argv[argv.index("--mount") + 1]
    assert "dst=/inputs,readonly" in mount and "workspace" not in mount
    assert result.usage.cost_microusd == 0
    assert result.usage.units["input_bytes"] == 13
    assert result.usage.details["funding"] == "owner_compute_subsidy"
    assert result.usage.details["cpu_usage_measured"] is False
    assert (tmp_path / result.artifacts[0]["path"]).read_bytes() == b"result"


@pytest.mark.parametrize("payload", [
    [{"path": "../escape", "data": ""}], [{"path": "ok", "data": "!!!"}],
    [{"path": "A", "data": ""}, {"path": "a", "data": ""}],
    [{"path": "ok", "data": "eHh4"}], {"path": "wrong-shape"},
])
def test_host_revalidates_entire_artifact_stream_before_any_write(tmp_path, payload):
    ctx = context(tmp_path)
    limits = _limits({**CONFIG, "max_artifact_bytes": 2})
    with pytest.raises(ToolError) as caught:
        CodeAdapter._artifacts(json.dumps(payload).encode(), ctx, limits, "a" * 64)
    assert caught.value.code == "artifact_rejected"
    assert list(tmp_path.iterdir()) == []


def test_restart_reconciliation_removes_owned_running_container_and_never_reexecutes(tmp_path):
    ctx = context(tmp_path)
    adapter = CodeAdapter()
    arguments = {"code": "print('must not replay')"}
    ctx.state.value.update(code_container="widemind-code-" + adapter._identity(ctx)[:32], code_phase="executing",
        code_request_sha256=hashlib.sha256(json.dumps(arguments, sort_keys=True).encode()).hexdigest())
    docker = DockerFixture(ctx); docker.exists = True
    with patch("widemind.tools.code._run", docker):
        with pytest.raises(Uncertain): adapter.execute(arguments, ctx)
    assert not docker.exists
    assert not any(args[0] in {"create", "exec", "start"} for args, _ in docker.commands)


def test_absent_container_does_not_prove_interrupted_execution_never_happened(tmp_path):
    ctx = context(tmp_path)
    ctx.state.value["code_container"] = "widemind-code-" + CodeAdapter._identity(ctx)[:32]
    with patch("widemind.tools.code._run", DockerFixture(ctx)):
        assert CodeAdapter().reconcile({}, ctx).outcome == "uncertain"


def test_created_container_is_affirmative_evidence_code_did_not_start(tmp_path):
    ctx = context(tmp_path)
    ctx.state.value["code_container"] = "widemind-code-" + CodeAdapter._identity(ctx)[:32]
    docker = DockerFixture(ctx); docker.exists = True; docker.status = "created"
    with patch("widemind.tools.code._run", docker):
        assert CodeAdapter().reconcile({}, ctx).outcome == "not_executed"
    assert not docker.exists


def test_foreign_container_is_never_removed(tmp_path):
    ctx = context(tmp_path)
    ctx.state.value["code_container"] = "widemind-code-" + CodeAdapter._identity(ctx)[:32]
    response = json.dumps({"id": "b" * 64, "labels": {"io.widemind.code": "foreign"}, "status": "running"}).encode()
    with patch("widemind.tools.code._run", return_value=(0, response, b"")) as command:
        with pytest.raises(Uncertain): CodeAdapter().reconcile({}, ctx)
    assert command.call_count == 1


def test_unavailable_docker_never_uses_a_host_fallback(tmp_path):
    ctx = context(tmp_path)
    with patch("widemind.tools.code._run", side_effect=Unavailable("fixture daemon down")) as command:
        with pytest.raises(Unavailable): CodeAdapter().execute({"code": "raise Exception('never host')"}, ctx)
    assert command.call_count == 1
    assert ctx.state.intent is None
