"""Actual private CLI subprocesses against temporary local data only."""
import json
import subprocess
import sys


def test_cli_status_init_execute_replay_and_private_diagnostics(tmp_path):
    config = tmp_path/"owner.json"
    config.write_text(json.dumps({"control_dir":str(tmp_path/"private"),
                                  "workspace_dir":str(tmp_path/"workspace"),
                                  "tools":{"files":{"enabled":True}},
                                  "policy":{"profile":"development","allow_external_effects":False}}))
    config.chmod(0o600)
    def run(*args):
        result = subprocess.run([sys.executable,"-m","widemind.cli","--config",str(config),*args],
                                capture_output=True,text=True,timeout=10)
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)
    status = run("status")
    assert status["tools"]["files"]["available"]
    assert not status["tools"]["gmail"]["available"]
    assert run("init","--run-id","cli-local")["spendable_microusd"] == 5_000_000
    request = tmp_path/"request.json"
    request.write_text(json.dumps({"operation":"create","path":"report.txt","text":"CLI fixture bytes"}))
    command = ["execute","files","--run-id","cli-local","--action-id","cli-create-1","--input-file",str(request)]
    receipt = run(*command)
    assert receipt["status"] == "succeeded"
    assert (tmp_path/"workspace"/"report.txt").read_bytes() == b"CLI fixture bytes"
    assert run(*command) == receipt
    assert run("balance","cli-local")["spendable_microusd"] == 5_000_000
    assert run("reconcile","cli-create-1") == receipt


def test_cli_errors_do_not_echo_private_config_contents(tmp_path):
    config = tmp_path/"owner.json"
    config.write_text('{"not-a-valid-config":"fixture-private-key-never-echo"}')
    config.chmod(0o600)
    result = subprocess.run([sys.executable,"-m","widemind.cli","--config",str(config),"status"],
                            capture_output=True,text=True,timeout=10)
    assert result.returncode == 1
    assert "fixture-private-key" not in result.stdout+result.stderr
    assert json.loads(result.stderr)["error"] == "configuration"
