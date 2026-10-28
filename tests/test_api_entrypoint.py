"""Real process entry-point smoke test with local synthetic accounting only."""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx


def test_cli_runs_durable_http_economy_and_preserves_seed_on_restart(tmp_path):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    env = {k: v for k, v in os.environ.items() if not k.startswith("WIDEMIND_")}
    env.update({
        "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
        "WIDEMIND_ECONOMY_DB": str(tmp_path / "http-entrypoint.sqlite3"),
        "WIDEMIND_OPERATOR_TOKEN": "fixture-only-operator-" + "o" * 48,
        "WIDEMIND_GATEWAY_TOKEN": "fixture-only-gateway-" + "g" * 48,
        "WIDEMIND_AGENT_TOKEN": "fixture-only-agent-" + "a" * 48,
        "WIDEMIND_AGENT_RUN_ID": "entrypoint-test",
    })
    for _restart in range(2):
        process = subprocess.Popen([sys.executable, "-m", "widemind.economy.cli", "--port", str(port)],
                                   env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=1) as client:
                deadline = time.monotonic() + 10
                while True:
                    try:
                        if client.get("/health").status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    if process.poll() is not None or time.monotonic() > deadline:
                        raise AssertionError("Economy CLI failed to start")
                    time.sleep(0.05)
                response = client.post("/v1/runs", headers={"Authorization": "Bearer " + env["WIDEMIND_OPERATOR_TOKEN"]},
                                       json={"run_id": "entrypoint-test", "mode": "test"})
                assert response.status_code == 200, response.text
                assert response.json()["posted_micro_usd"] == 5_000_000
                response = client.get("/v1/runs/entrypoint-test/ledger",
                                      headers={"Authorization": "Bearer " + env["WIDEMIND_AGENT_TOKEN"]})
                assert response.status_code == 200
                assert len(response.json()) == 1
        finally:
            process.terminate()
            try:
                process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate(timeout=10)


def test_cli_fails_closed_when_role_configuration_is_missing():
    env = {k: v for k, v in os.environ.items() if not k.startswith("WIDEMIND_")}
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    response = subprocess.run([sys.executable, "-m", "widemind.economy.cli"], env=env,
                              capture_output=True, text=True, timeout=10)
    assert response.returncode == 2
    assert "Missing required environment variable: WIDEMIND_OPERATOR_TOKEN" in response.stderr
