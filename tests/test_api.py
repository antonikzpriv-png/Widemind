"""Actual HTTP/accounting integration. No external payments are made here."""
from __future__ import annotations

import os
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from widemind.economy.app import create_app
from widemind.economy.config import Settings


@pytest.fixture
def settings(tmp_path):
    return Settings(str(tmp_path / "economy.sqlite3"), "operator-" + "o" * 48,
                    "gateway-" + "g" * 48, "agent-" + "a" * 48, "run-a")


@pytest.fixture
def client(settings):
    return TestClient(create_app(settings))


def headers(settings, role="operator"):
    return {"Authorization": "Bearer " + getattr(settings, role + "_token")}


def create_run(client, settings, run_id="run-a", mode="test"):
    response = client.post("/v1/runs", headers=headers(settings), json={"run_id": run_id, "mode": mode})
    assert response.status_code == 200, response.text
    return response.json()


def reserve(client, settings, *, amount=1_000_000, action="action-1", key="reserve-1", role="agent"):
    return client.post("/v1/runs/run-a/reservations", headers=headers(settings, role), json={
        "action_id": action, "amount_micro_usd": amount, "idempotency_key": key,
        "provider": "fixture-provider", "account": "fixture-account", "limits": {"max_tokens": 100},
    })


def balance(client, settings):
    response = client.get("/v1/runs/run-a/balance", headers=headers(settings, "agent"))
    assert response.status_code == 200, response.text
    return response.json()


def test_health_exposes_no_private_state(client, settings):
    assert client.get("/health").json() == {"status": "ok", "service": "widemind-economy", "contract_version": 1}
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404
    assert client.get("/v1/runs/run-a/balance").status_code == 401
    for value in ("Bearer unknown", "Basic example", "Bearer " + "x" * 1000, "bearer " + settings.agent_token):
        assert client.get("/v1/runs/run-a/balance", headers={"Authorization": value}).status_code == 401


def test_seed_idempotency_and_scoped_agent(client, settings):
    assert create_run(client, settings)["posted_micro_usd"] == 5_000_000
    assert create_run(client, settings)["posted_micro_usd"] == 5_000_000
    create_run(client, settings, "run-b")
    assert client.get("/v1/runs/run-b/balance", headers=headers(settings, "agent")).status_code == 403
    assert client.post("/v1/runs/run-b/reservations", headers=headers(settings, "agent"), json={
        "action_id": "x", "amount_micro_usd": 1, "idempotency_key": "x", "provider": "fixture", "account": "fixture", "limits": {"max_units": 1},
    }).status_code == 403
    rows = client.get("/v1/runs/run-a/ledger", headers=headers(settings, "agent")).json()
    assert len(rows) == 1 and rows[0]["amount_micro_usd"] == 5_000_000
    assert client.post("/v1/runs", headers=headers(settings), json={"run_id": "run-a", "mode": "live"}).status_code == 409


@pytest.mark.parametrize("role", ["agent", "gateway"])
@pytest.mark.parametrize("path,body", [
    ("/v1/runs", {"run_id": "self-grant", "mode": "test"}),
    ("/v1/runs/run-a/funding", {"amount_micro_usd": 50_000_000, "category": "owner_funding", "reference": "claim", "idempotency_key": "claim"}),
    ("/v1/runs/run-a/holds", {"hold_id": "hold-1", "amount_micro_usd": 0, "kind": "customer", "reason": "claim", "idempotency_key": "claim"}),
    ("/v1/runs/run-a/subsidies", {"amount_micro_usd": 1_000_000, "reference": "claim", "idempotency_key": "claim"}),
    ("/v1/runs/run-a/earned-revenue", {"amount_micro_usd": 1_000_000, "reference": "claim", "idempotency_key": "claim", "job_id": "job-1"}),
    ("/v1/runs/run-a/corrections", {"entry_id": "irrelevant", "amount_micro_usd": 1_000_000, "reason": "claim", "idempotency_key": "claim"}),
    ("/v1/runs/run-a/checkout", {"request_id": "claim", "job_id": "job-1", "amount_micro_usd": 1_000_000}),
    ("/v1/payments/cs_claim/reconcile", None),
    ("/v1/payments/cs_claim/release-hold", {"idempotency_key": "claim"}),
    ("/v1/provider/verify", None),
])
def test_untrusted_identities_cannot_grant_or_reconcile(client, settings, role, path, body):
    create_run(client, settings)
    response = client.post(path, headers=headers(settings, role), json=body)
    assert response.status_code == 403, response.text
    assert balance(client, settings)["posted_micro_usd"] == 5_000_000


def test_agent_cannot_self_report_free_execution(client, settings):
    create_run(client, settings)
    rid = reserve(client, settings).json()["reservation_id"]
    for suffix, body in (("start", None), ("cancel", None), ("settle", {
        "actual_micro_usd": 0, "provider": "fixture-provider", "account": "fixture-account",
        "external_charge_id": "charge-1", "outcome": "succeeded",
    })):
        assert client.post(f"/v1/reservations/{rid}/{suffix}", headers=headers(settings, "agent"), json=body).status_code == 403
    assert balance(client, settings)["reserved_micro_usd"] == 1_000_000


@pytest.mark.parametrize("value", [True, False, 1.1, "1000000", None, -1, 9_000_000_000_000_001])
def test_exact_money_rejects_implicit_coercion(client, settings, value):
    create_run(client, settings)
    assert reserve(client, settings, amount=value).status_code == 422
    assert balance(client, settings)["reserved_micro_usd"] == 0


def test_extra_fields_do_not_become_money_authority(client, settings):
    create_run(client, settings)
    result = client.post("/v1/runs/run-a/funding", headers=headers(settings), json={
        "amount_micro_usd": 1, "category": "customer_receipt", "reference": "invoice-1", "idempotency_key": "claim", "verified": True,
    })
    assert result.status_code == 422
    assert balance(client, settings)["posted_micro_usd"] == 5_000_000
    assert client.post("/v1/runs/run-a/receipts", headers=headers(settings), json={"amount_micro_usd": 99999999}).status_code == 404


def test_gateway_settlement_failed_calls_and_exhaustion(client, settings):
    create_run(client, settings)
    response = reserve(client, settings, amount=5_000_000)
    assert response.status_code == 200
    rid = response.json()["reservation_id"]
    assert balance(client, settings)["exhausted"] is True
    assert reserve(client, settings, amount=1, action="other", key="other").status_code == 409
    assert client.post(f"/v1/reservations/{rid}/start", headers=headers(settings, "gateway")).status_code == 200
    assert client.post(f"/v1/reservations/{rid}/start", headers=headers(settings, "gateway")).status_code == 409
    body = {"actual_micro_usd": 1_200_000, "provider": "fixture-provider", "account": "fixture-account", "external_charge_id": "charged-failure", "usage": {"tokens": 120}, "outcome": "failed"}
    first = client.post(f"/v1/reservations/{rid}/settle", headers=headers(settings, "gateway"), json=body)
    assert first.status_code == 200, first.text
    assert client.post(f"/v1/reservations/{rid}/settle", headers=headers(settings, "gateway"), json=body).status_code == 200
    assert balance(client, settings)["posted_micro_usd"] == 3_800_000
    assert balance(client, settings)["reserved_micro_usd"] == 0
    ledger = client.get("/v1/runs/run-a/ledger", headers=headers(settings)).json()
    assert sum(row["amount_micro_usd"] for row in ledger) == 3_800_000


def test_unknown_survives_process_reconstruction_and_cannot_cancel(client, settings):
    create_run(client, settings)
    rid = reserve(client, settings).json()["reservation_id"]
    client.post(f"/v1/reservations/{rid}/start", headers=headers(settings, "gateway"))
    response = client.post(f"/v1/reservations/{rid}/settle", headers=headers(settings, "gateway"), json={
        "actual_micro_usd": None, "provider": "fixture-provider", "account": "fixture-account", "external_charge_id": "unknown-attempt", "outcome": "unknown",
    })
    assert response.status_code == 200
    restarted = TestClient(create_app(settings))
    assert balance(restarted, settings)["reserved_micro_usd"] == 1_000_000
    assert restarted.post(f"/v1/reservations/{rid}/cancel", headers=headers(settings, "gateway")).status_code == 409
    response = restarted.post(f"/v1/reservations/{rid}/settle", headers=headers(settings, "gateway"), json={
        "actual_micro_usd": 7_000_000, "provider": "fixture-provider", "account": "fixture-account", "external_charge_id": "unknown-attempt", "outcome": "failed",
    })
    assert response.status_code == 200, response.text
    assert balance(restarted, settings)["posted_micro_usd"] == -2_000_000
    assert balance(restarted, settings)["exhausted"] is True
    assert reserve(restarted, settings, amount=1, action="later", key="later").status_code == 409


def test_funding_metrics_holds_and_events_are_distinct(client, settings):
    create_run(client, settings)
    h = headers(settings)
    for category, amount in (("owner_funding", 500_000), ("test_funding", 250_000)):
        assert client.post("/v1/runs/run-a/funding", headers=h, json={"amount_micro_usd": amount, "category": category, "reference": category, "idempotency_key": category}).status_code == 200
    for path, body in (("subsidies", {"amount_micro_usd": 8_000_000, "reference": "hosting", "idempotency_key": "subsidy-1"}),
                       ("earned-revenue", {"amount_micro_usd": 9_000_000, "reference": "delivery", "idempotency_key": "earned-1", "job_id": "job-1"})):
        assert client.post("/v1/runs/run-a/" + path, headers=h, json=body).status_code == 200
    assert balance(client, settings)["posted_micro_usd"] == 5_750_000
    assert client.post("/v1/runs/run-a/holds", headers=h, json={"hold_id": "refund-exposure", "amount_micro_usd": 6_000_000, "kind": "refund", "reason": "pending-refund", "idempotency_key": "hold-1"}).status_code == 200
    assert balance(client, settings)["available_micro_usd"] == -250_000
    events = client.get("/v1/runs/run-a/events", headers=headers(settings, "agent")).json()
    assert events and all(event["version"] == 1 and event["run_id"] == "run-a" for event in events)
    assert client.get("/v1/runs/run-a/events", params={"after": events[-1]["sequence"]}, headers=h).json() == []
    for projection in ("reservations", "payments", "reconciliations"):
        assert client.get("/v1/runs/run-a/" + projection, headers=headers(settings, "agent")).status_code == 200


def test_missing_payment_configuration_is_explicit_not_simulated(client, settings):
    create_run(client, settings)
    assert client.post("/v1/provider/verify", headers=headers(settings)).status_code == 503
    assert client.post("/v1/runs/run-a/checkout", headers=headers(settings), json={"request_id": "order-1", "job_id": "job-1", "amount_micro_usd": 1_000_000}).status_code == 503
    assert client.post("/v1/webhooks/stripe", content=b"{}").status_code == 400


def test_body_bounds_and_validation_do_not_echo_submitted_secrets(client, settings):
    assert client.post("/v1/webhooks/stripe", content=b"x" * 1_048_577, headers={"Stripe-Signature": "bad"}).status_code == 413
    response = client.post("/v1/runs", headers=headers(settings), json={"run_id": "run-a", "mode": settings.operator_token})
    assert response.status_code == 422
    assert settings.operator_token not in response.text
    assert settings.gateway_token not in repr(settings)


@pytest.mark.parametrize("field,value", [("operator_token", "short"), ("agent_token", "a" * 31), ("gateway_token", "x y" * 20), ("agent_token", "é" * 40), ("database_path", ":memory:"), ("agent_run_id", "")])
def test_configuration_rejects_unsafe_credentials(settings, field, value):
    with pytest.raises(ValueError):
        replace(settings, **{field: value})


def test_configuration_rejects_shared_role_tokens_and_partial_stripe(settings):
    with pytest.raises(ValueError, match="distinct"):
        replace(settings, gateway_token=settings.operator_token)
    with pytest.raises(ValueError, match="Stripe requires"):
        replace(settings, stripe_api_key="sk_test_fixture")
    with pytest.raises(ValueError, match="MODE"):
        replace(settings, stripe_mode="automatic")


def test_configuration_environment_has_no_credential_defaults(monkeypatch):
    for key in list(os.environ):
        if key.startswith("WIDEMIND_"):
            monkeypatch.delenv(key)
    with pytest.raises(ValueError, match="WIDEMIND_OPERATOR_TOKEN"):
        Settings.from_env()


@pytest.mark.parametrize("mode,key,url,valid", [
    ("test", "sk_test_fixture", "http://localhost:8080/success", True),
    ("test", "rk_test_fixture", "https://example.com/success", True),
    ("live", "sk_live_fixture", "https://example.com/success", True),
    ("test", "sk_live_fixture", "https://example.com/success", False),
    ("live", "sk_live_fixture", "http://localhost:8080/success", False),
    ("test", "sk_test_fixture", "javascript:alert(1)", False),
    ("test", "sk_test_fixture", "https://user:password@example.com/", False),
    ("test", "sk_test_fixture", "https://example.com:not-a-port/", False),
    ("test", "sk_test_fixture", "https://example.com:65536/", False),
    ("test", "sk_test_fixture", "https://example.com:0/", False),
    ("test", "sk_test_fixture", "https://example.com/\nheader", False),
])
def test_stripe_configuration_mode_and_redirect_safety(settings, mode, key, url, valid):
    values = dict(stripe_api_key=key, stripe_webhook_secret="whsec_fixture", stripe_account="acct_fixture", stripe_mode=mode, checkout_success_url=url, checkout_cancel_url=url)
    if valid:
        assert replace(settings, **values).stripe_enabled
    else:
        with pytest.raises(ValueError):
            replace(settings, **values)


def test_event_cursor_is_bounded_before_sqlite(client, settings):
    create_run(client, settings)
    for cursor in (-1, 2 ** 100):
        assert client.get("/v1/runs/run-a/events", params={"after": cursor}, headers=headers(settings, "agent")).status_code == 422
