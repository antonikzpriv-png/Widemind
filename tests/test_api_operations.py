"""Durable reconciliation API/CLI fixtures. No real Stripe payments occur."""
from __future__ import annotations

import asyncio
from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient

from widemind.economy.app import create_app
from widemind.economy.config import Settings
from widemind.economy.service import EconomyService
from widemind.economy.stripe import StripePayments
from widemind.economy.reconciliation import ReconciliationQueue, ReconciliationWorker
from test_api import settings, client, headers, create_run, balance
from test_api_stripe import configuration
from test_stripe_payments import ACCOUNT, SECRET, StripeFixture, event, signed


@pytest.fixture
def operations(tmp_path):
    settings = configuration(tmp_path)
    service = EconomyService(settings.database_path)
    service.create_run("test-run")
    service.register_payment("test-run", "stripe", ACCOUNT, "cs_fixture", "job", "binding")
    fixture = StripeFixture()
    with httpx.Client(transport=httpx.MockTransport(fixture)) as provider_client:
        stripe = StripePayments(service, api_key=settings.stripe_api_key, account=ACCOUNT, mode="test",
                                webhook_secret=SECRET, success_url=settings.checkout_success_url,
                                cancel_url=settings.checkout_cancel_url, client=provider_client)
        queue = ReconciliationQueue(service)
        with TestClient(create_app(settings, service=service, payments=stripe)) as client:
            yield settings, service, fixture, stripe, queue, client


def test_signature_enqueued_before_io_and_duplicate_acks_without_provider(operations):
    settings, service, fixture, stripe, queue, client = operations
    raw, signature = signed(event())
    response = client.post("/v1/webhooks/stripe", content=raw, headers={"Stripe-Signature": signature})
    assert response.status_code == 202, response.text
    assert response.json()["task_id"]
    assert response.json()["duplicate"] is False
    assert fixture.calls == []
    assert service.balance("test-run")["posted_micro_usd"] == 5_000_000
    duplicate = client.post("/v1/webhooks/stripe", content=raw, headers={"Stripe-Signature": signature})
    assert duplicate.status_code == 202, duplicate.text
    assert duplicate.json()["duplicate"] is True
    assert duplicate.json()["task_id"] == response.json()["task_id"]
    assert fixture.calls == []
    result = ReconciliationWorker(queue, stripe).run_once()
    assert result["completed"] == 1
    assert service.balance("test-run")["posted_micro_usd"] == 14_410_000
    fixture.calls.clear()
    duplicate = client.post("/v1/webhooks/stripe", content=raw, headers={"Stripe-Signature": signature})
    assert duplicate.status_code == 202 and duplicate.json()["duplicate"] is True
    assert fixture.calls == []
    assert ReconciliationWorker(queue, stripe).run_once()["processed"] == 0


def test_signature_failure_never_queues_or_contacts_provider(operations):
    settings, service, fixture, stripe, queue, client = operations
    raw, signature = signed(event())
    assert client.post("/v1/webhooks/stripe", content=raw+b" ", headers={"Stripe-Signature": signature}).status_code == 400
    assert queue.list_tasks("stripe", ACCOUNT, "test") == []
    assert fixture.calls == []


def test_same_event_id_different_verified_payload_conflicts(operations):
    settings, service, fixture, stripe, queue, client = operations
    raw, signature = signed(event())
    assert client.post("/v1/webhooks/stripe", content=raw, headers={"Stripe-Signature": signature}).status_code == 202
    raw, signature = signed(event(kind="checkout.session.expired"))
    response = client.post("/v1/webhooks/stripe", content=raw, headers={"Stripe-Signature": signature})
    assert response.status_code == 409, response.text
    assert len(queue.list_tasks("stripe", ACCOUNT, "test")) == 1
    assert fixture.calls == []


def test_operator_schedule_is_durable_and_keeps_sync_contract(operations):
    settings, service, fixture, stripe, queue, client = operations
    response = client.post("/v1/payments/cs_fixture/schedule", headers=headers(settings))
    assert response.status_code == 202, response.text
    assert response.json()["task_id"]
    assert fixture.calls == []
    result = ReconciliationWorker(queue, stripe).run_once()
    assert result["completed"] == 1
    # The original endpoint retains synchronous response semantics for existing callers.
    assert client.post("/v1/payments/cs_fixture/reconcile", headers=headers(settings)).status_code == 200
    assert service.balance("test-run")["posted_micro_usd"] == 14_410_000


@pytest.mark.parametrize("role", ["agent", "gateway"])
@pytest.mark.parametrize("method,path,body", [
    ("post", "/v1/payments/cs_fixture/schedule", None),
    ("get", "/v1/reconciliation/tasks", None),
    ("post", "/v1/reconciliation/tasks/unknown/retry", {"reason": "operator-investigated", "idempotency_key": "retry-1"}),
    ("post", "/v1/checkouts/request-1/recover", None),
    ("get", "/v1/runs/test-run/financial-summary", None),
    ("get", "/v1/runs/test-run/integrity", None),
])
def test_operations_are_operator_only(operations, role, method, path, body):
    settings, service, fixture, stripe, queue, client = operations
    kwargs = {"headers": headers(settings, role)}
    if method == "post":
        kwargs["json"] = body
    assert getattr(client, method)(path, **kwargs).status_code == 403
    assert fixture.calls == []


def test_queue_read_filter_bounds_and_pending_retry_rejection(operations):
    settings, service, fixture, stripe, queue, client = operations
    task = queue.enqueue_payment("stripe", ACCOUNT, "test", "cs_fixture")
    response = client.get("/v1/reconciliation/tasks", params={"state": "pending", "limit": 1}, headers=headers(settings))
    assert response.status_code == 200, response.text
    assert len(response.json()) == 1 and response.json()[0]["task_id"] == task["task_id"]
    for params in ({"limit": 0}, {"limit": 101}, {"state": "unbounded"}):
        assert client.get("/v1/reconciliation/tasks", params=params, headers=headers(settings)).status_code == 422
    response = client.post(f"/v1/reconciliation/tasks/{task['task_id']}/retry", headers=headers(settings),
                           json={"reason": "not-dead-yet", "idempotency_key": "retry-1"})
    assert response.status_code == 409
    assert fixture.calls == []


def test_private_authentication_precedes_body_read(client):
    # Missing auth must not read even one byte of an attacker-controlled body.
    application = client.app
    messages = []
    reads = 0
    async def receive():
        nonlocal reads
        reads += 1
        raise AssertionError("Unauthenticated private body was consumed")
    async def send(message):
        messages.append(message)
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
             "scheme": "http", "path": "/v1/runs", "raw_path": b"/v1/runs", "root_path": "",
             "query_string": b"", "headers": [(b"content-type", b"application/json")],
             "client": ("127.0.0.1", 1234), "server": ("testserver", 80)}
    asyncio.run(application(scope, receive, send))
    assert reads == 0
    assert next(item for item in messages if item["type"] == "http.response.start")["status"] == 401


def test_duplicate_authorization_is_rejected_before_mutation(client, settings):
    for values in ([settings.operator_token, settings.agent_token], [settings.agent_token, settings.operator_token]):
        response = client.post("/v1/runs", headers=[("Authorization", "Bearer " + value) for value in values],
                               json={"run_id": "run-a", "mode": "test"})
        assert response.status_code == 401
    assert client.get("/v1/runs/run-a/balance", headers=headers(settings)).status_code == 404


def test_http_identifiers_cannot_create_unaddressable_slash_paths(client, settings):
    assert client.post("/v1/runs", headers=headers(settings), json={"run_id": "run/a"}).status_code == 422
    with pytest.raises(ValueError):
        replace(settings, agent_run_id="run/a")
    for identifier in ("garbage", "cs_bad-id", "cs_" + "x" * 201):
        assert client.post(f"/v1/payments/{identifier}/reconcile", headers=headers(settings)).status_code == 422


def test_retry_cannot_mutate_another_provider_account(operations):
    settings, service, fixture, stripe, queue, client = operations
    service.register_payment("test-run", "stripe", "acct_other", "cs_other", "other-job", "other-binding")
    task = queue.enqueue_payment("stripe", "acct_other", "test", "cs_other")
    response = client.post(f"/v1/reconciliation/tasks/{task['task_id']}/retry", headers=headers(settings),
                           json={"reason": "wrong-account", "idempotency_key": "retry-1"})
    assert response.status_code == 404
    assert queue.task(task["task_id"])["state"] == "pending"
    response = client.get("/v1/reconciliation/tasks", headers=headers(settings))
    assert response.status_code == 200 and response.json() == []
    assert fixture.calls == []


def test_dead_letter_retry_is_operator_audited_and_idempotent(operations):
    settings, service, fixture, stripe, queue, client = operations
    queue.max_attempts = 1
    task = queue.enqueue_payment("stripe", ACCOUNT, "test", "cs_fixture")
    claim = queue.claim("stripe", ACCOUNT, "test")
    assert queue.fail(task["task_id"], claim["lease_token"], "provider_unavailable")["state"] == "dead_letter"
    body = {"reason": "verified-provider-restored", "idempotency_key": "retry-1"}
    response = client.post(f"/v1/reconciliation/tasks/{task['task_id']}/retry", headers=headers(settings), json=body)
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "pending" and response.json()["attempts"] == 0
    response = client.post(f"/v1/reconciliation/tasks/{task['task_id']}/retry", headers=headers(settings), json=body)
    assert response.status_code == 200 and response.json()["duplicate"] is True
    response = client.post(f"/v1/reconciliation/tasks/{task['task_id']}/retry", headers=headers(settings),
                           json={**body, "reason": "changed-reason"})
    assert response.status_code == 409
    assert fixture.calls == []


def test_financial_report_routes_are_local_redacted_reads(operations):
    settings, service, fixture, stripe, queue, client = operations
    service.record_funding("test-run", 1_000_000, "owner_funding", "private-operator-reference", "fund-1")
    before = service.balance("test-run")
    response = client.get("/v1/runs/test-run/financial-summary", headers=headers(settings))
    assert response.status_code == 200, response.text
    assert response.json()["posted_micro_usd"] == 6_000_000
    assert response.json()["verification_scope"] == "local_accounting_only"
    for private in ("test-run", "private-operator-reference", ACCOUNT, settings.operator_token):
        assert private not in response.text
    response = client.get("/v1/runs/test-run/integrity", headers=headers(settings))
    assert response.status_code == 200, response.text
    assert response.json()["ok"] is True
    assert response.json()["verification_scope"] == "local_accounting_consistency"
    assert service.balance("test-run") == before
    assert fixture.calls == []


def test_operator_checkout_recovery_route_only_reads_existing_provider_session(operations):
    from test_stripe_payments import recovery_fixture
    settings, service, fixture, stripe, queue, client = operations
    service.create_run("run")
    _, session = recovery_fixture((service, fixture, stripe))
    response = client.post("/v1/checkouts/lost-request/recover", headers=headers(settings))
    assert response.status_code == 200, response.text
    assert response.json()["payment_id"] == "cs_recovered"
    assert response.json()["url"] == session["url"]
    assert service.balance("run")["posted_micro_usd"] == 5_000_000
    assert not fixture.checkout_keys and all(call[0] == "GET" for call in fixture.calls)
    calls = list(fixture.calls)
    assert client.post("/v1/checkouts/lost-request/recover", headers=headers(settings)).status_code == 200
    assert fixture.calls == calls
