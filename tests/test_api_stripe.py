"""Stripe HTTP fixtures through actual FastAPI + ledger. Not sandbox payments."""
from __future__ import annotations

import httpx
from fastapi.testclient import TestClient

from widemind.economy.app import create_app
from widemind.economy.config import Settings
from widemind.economy.service import EconomyService
from widemind.economy.stripe import StripePayments
from widemind.economy.reconciliation import ReconciliationWorker
from test_stripe_payments import ACCOUNT, SECRET, StripeFixture, event, signed


def configuration(tmp_path):
    return Settings(str(tmp_path / "stripe-api.sqlite3"), "fixture-operator-" + "o" * 48,
                    "fixture-gateway-" + "g" * 48, "fixture-agent-" + "a" * 48, "test-run",
                    stripe_api_key="sk_test_fixture", stripe_webhook_secret=SECRET,
                    stripe_account=ACCOUNT, stripe_mode="test",
                    checkout_success_url="http://localhost:8000/success", checkout_cancel_url="http://localhost:8000/cancel")


def test_checkout_signed_webhook_duplicate_fulfillment_refund_http_slice(tmp_path):
    settings = configuration(tmp_path)
    ledger = EconomyService(settings.database_path)
    fixture = StripeFixture()
    # A hosted session created by this flow is the only eligible local binding.
    created = dict(fixture.records["/checkout/sessions/cs_fixture"], id="cs_created")
    fixture.records["/checkout/sessions/cs_created"] = created
    fixture.records["/checkout/sessions/cs_fixture"] = created
    with httpx.Client(transport=httpx.MockTransport(fixture)) as provider_client:
        payments = StripePayments(ledger, api_key=settings.stripe_api_key, account=ACCOUNT, mode="test",
                                  webhook_secret=SECRET, success_url=settings.checkout_success_url,
                                  cancel_url=settings.checkout_cancel_url, client=provider_client)
        with TestClient(create_app(settings, service=ledger, payments=payments)) as app:
            operator = {"Authorization": "Bearer " + settings.operator_token}
            agent = {"Authorization": "Bearer " + settings.agent_token}
            assert app.post("/v1/runs", headers=operator, json={"run_id": "test-run", "mode": "test"}).status_code == 200
            assert app.post("/v1/provider/verify", headers=operator).json()["account"] == ACCOUNT
            body = {"request_id": "order-1", "job_id": "job-1", "amount_micro_usd": 10_000_000}
            response = app.post("/v1/runs/test-run/checkout", headers=operator, json=body)
            assert response.status_code == 200, response.text
            assert response.json()["payment_id"] == "cs_created"
            assert response.json()["url"].startswith("https://checkout.stripe.com/")
            assert app.post("/v1/runs/test-run/checkout", headers=operator, json=body).json() == response.json()
            assert len(fixture.checkout_keys) == 1
            assert ledger.balance("test-run")["available_micro_usd"] == 5_000_000
            raw, signature = signed(event(identifier="cs_created"))
            assert app.post("/v1/webhooks/stripe", content=raw+b" ", headers={"Stripe-Signature": signature}).status_code == 400
            for _duplicate in range(2):
                response = app.post("/v1/webhooks/stripe", content=raw, headers={"Stripe-Signature": signature})
                assert response.status_code == 202, response.text
            result = ReconciliationWorker(app.app.state.reconciliation_queue, payments).run_once()
            assert result["completed"] == 1
            balance = app.get("/v1/runs/test-run/balance", headers=agent).json()
            assert balance["posted_micro_usd"] == 14_410_000
            assert balance["held_micro_usd"] == 9_410_000
            assert balance["available_micro_usd"] == 5_000_000
            assert app.post("/v1/payments/cs_created/release-hold", headers=agent,
                            json={"idempotency_key": "fulfilled-1"}).status_code == 403
            response = app.post("/v1/payments/cs_created/release-hold", headers=operator,
                                json={"idempotency_key": "fulfilled-1"})
            assert response.status_code == 200, response.text
            assert ledger.balance("test-run")["available_micro_usd"] == 14_410_000
            fixture.refunds = [{"id": "re_fixture", "charge": "ch_fixture", "amount": 1000,
                                "status": "succeeded", "balance_transaction": "txn_refund"}]
            fixture.records["/balance_transactions/txn_refund"] = dict(
                fixture.tx("txn_refund", -1000), source="re_fixture", type="refund")
            raw, signature = signed(event("charge.refunded", "ch_fixture", id="evt_refund"))
            response = app.post("/v1/webhooks/stripe", content=raw, headers={"Stripe-Signature": signature})
            assert response.status_code == 202, response.text
            assert ReconciliationWorker(app.app.state.reconciliation_queue, payments).run_once()["completed"] == 1
            assert app.post("/v1/payments/cs_created/reconcile", headers=operator).status_code == 200
            assert ledger.balance("test-run")["posted_micro_usd"] == 4_410_000
            entries = app.get("/v1/runs/test-run/ledger", headers=operator).json()
            assert sum(entry["kind"] == "customer_receipt" for entry in entries) == 1
            assert sum(entry["kind"] == "refund" for entry in entries) == 1
            assert sum(entry["kind"] == "processor_fee" for entry in entries) == 1
            status = app.get("/v1/runs/test-run/payments", headers=agent).json()[0]
            assert status["status"] == "refunded"
            assert app.get("/v1/runs/test-run/reconciliations", headers=agent).json()
        assert not provider_client.is_closed  # Externally owned client belongs to the caller.


def test_application_closes_its_owned_stripe_http_client(tmp_path):
    settings = configuration(tmp_path)
    # Configuration constructs a real adapter. No provider request is issued.
    app = create_app(settings)
    adapter = app.state.payments
    assert not adapter.client.is_closed
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
    assert adapter.client.is_closed
