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


def test_cli_worker_once_processes_durable_queue_with_explicit_provider_fixture(tmp_path):
    """Run the normal CLI in a process; replace only external Stripe transport."""
    import json
    from widemind.economy.service import EconomyService
    from widemind.economy.reconciliation import ReconciliationQueue
    from test_stripe_payments import ACCOUNT, SECRET
    database = tmp_path / "worker-entrypoint.sqlite3"
    service = EconomyService(database)
    service.create_run("entrypoint-test")
    service.register_payment("entrypoint-test", "stripe", ACCOUNT, "cs_fixture", "job", "binding")
    task = ReconciliationQueue(service).enqueue_payment("stripe", ACCOUNT, "test", "cs_fixture")
    root = Path(__file__).resolve().parents[1]
    env = {k: v for k, v in os.environ.items() if not k.startswith("WIDEMIND_")}
    env.update({
        "PYTHONPATH": os.pathsep.join((str(root / "src"), str(root / "tests"))),
        "WIDEMIND_ECONOMY_DB": str(database),
        "WIDEMIND_OPERATOR_TOKEN": "fixture-only-operator-" + "o" * 48,
        "WIDEMIND_GATEWAY_TOKEN": "fixture-only-gateway-" + "g" * 48,
        "WIDEMIND_AGENT_TOKEN": "fixture-only-agent-" + "a" * 48,
        "WIDEMIND_AGENT_RUN_ID": "entrypoint-test",
        "WIDEMIND_PAYMENT_PROVIDER": "stripe",
        "WIDEMIND_STRIPE_API_KEY": "sk_test_fixture",
        "WIDEMIND_STRIPE_WEBHOOK_SECRET": SECRET,
        "WIDEMIND_STRIPE_ACCOUNT": ACCOUNT,
        "WIDEMIND_STRIPE_MODE": "test",
        "WIDEMIND_CHECKOUT_SUCCESS_URL": "http://localhost/success",
        "WIDEMIND_CHECKOUT_CANCEL_URL": "http://localhost/cancel",
    })
    script = '''
import runpy
import httpx
from test_stripe_payments import StripeFixture
fixture = StripeFixture()
original = httpx.Client
httpx.Client = lambda **kwargs: original(transport=httpx.MockTransport(fixture), **kwargs)
runpy.run_module("widemind.economy.cli", run_name="__main__")
'''
    for iteration in range(2):
        result = subprocess.run([sys.executable, "-c", script, "worker", "--once"],
                                env=env, capture_output=True, text=True, timeout=20)
        assert result.returncode == 0, result.stderr
        report = json.loads(result.stdout)
        if iteration == 0:
            assert report["completed"] >= 1
        else:
            assert report["processed"] == 0
        assert service.balance("entrypoint-test")["posted_micro_usd"] == 14_410_000
    assert ReconciliationQueue(service).task(task["task_id"])["state"] == "completed"


def test_cli_worker_options_are_bounded_before_configuration():
    for option, value in (("--batch-size", "0"), ("--batch-size", "101"), ("--poll-seconds", "0"), ("--scan-interval", "59")):
        result = subprocess.run([sys.executable, "-m", "widemind.economy.cli", "worker", "--once", option, value],
                                capture_output=True, text=True, timeout=10)
        assert result.returncode == 2
        assert "Expected an integer between" in result.stderr
