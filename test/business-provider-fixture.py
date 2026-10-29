"""Explicit loopback-only test backend. Never imported by the deployed application.

Runs the real economy HTTP app/ledger/Stripe adapter with an HTTPX provider
fixture. No network request leaves the host and no record is a real payment.
"""
from __future__ import annotations

import argparse
import hmac
from pathlib import Path
import sys

import httpx
from fastapi import Header, HTTPException
import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from test_stripe_payments import ACCOUNT, SECRET, StripeFixture  # noqa: E402
from widemind.economy.app import create_app  # noqa: E402
from widemind.economy.config import Settings  # noqa: E402
from widemind.economy.reconciliation import ReconciliationWorker  # noqa: E402
from widemind.economy.service import EconomyService  # noqa: E402
from widemind.economy.stripe import StripePayments  # noqa: E402


OPERATOR = "fixture-business-operator-" + "o" * 48
GATEWAY = "fixture-business-gateway-" + "g" * 48
FIXTURE_CONTROL = "fixture-business-control-" + "c" * 48
PROFILES = {
    "gmail.send": {"provider": "gmail", "account": "fixture-mailbox", "mode": "sandbox"},
    "gmail.read": {"provider": "gmail", "account": "fixture-mailbox", "mode": "sandbox"},
    "research.collect": {"provider": "openai", "account": "fixture-openai", "mode": "sandbox"},
    "commerce.checkout": {"provider": "commerce", "account": "fixture-commerce", "mode": "sandbox"},
    "commerce.fulfill": {"provider": "commerce", "account": "fixture-commerce", "mode": "sandbox"},
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--explicit-fixtures", action="store_true", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--port", required=True, type=int)
    args = parser.parse_args()
    settings = Settings(
        args.database, OPERATOR, GATEWAY, "fixture-business-agent-" + "a" * 48,
        "fixture-not-a-real-customer", stripe_api_key="sk_test_fixture",
        stripe_webhook_secret=SECRET, stripe_account=ACCOUNT, stripe_mode="test",
        checkout_success_url="http://localhost:8000/success",
        checkout_cancel_url="http://localhost:8000/cancel",
        runtime_auth_secret="fixture-business-signing-" + "s" * 48,
        runtime_tool_profiles=PROFILES,
    )
    economy = EconomyService(settings.database_path)
    provider = StripeFixture()
    created = dict(provider.records["/checkout/sessions/cs_fixture"], id="cs_created")
    provider.records["/checkout/sessions/cs_fixture"] = created
    provider.records["/checkout/sessions/cs_created"] = created
    with httpx.Client(transport=httpx.MockTransport(provider)) as client:
        payments = StripePayments(
            economy, api_key="sk_test_fixture", account=ACCOUNT, mode="test",
            webhook_secret=SECRET, success_url=settings.checkout_success_url,
            cancel_url=settings.checkout_cancel_url, client=client,
        )
        app = create_app(settings, service=economy, payments=payments)

        @app.post("/_fixture/control")
        def control(body: dict, authorization: str = Header(default="")):
            if not hmac.compare_digest(authorization, "Bearer " + FIXTURE_CONTROL):
                raise HTTPException(status_code=403, detail="Explicit fixture control only")
            if body == {"operation": "refund"}:
                provider.refunds = [{
                    "id": "re_fixture", "charge": "ch_fixture", "amount": 1000,
                    "status": "succeeded", "balance_transaction": "txn_refund",
                }]
                provider.records["/balance_transactions/txn_refund"] = dict(
                    provider.tx("txn_refund", -1000), source="re_fixture", type="refund",
                )
                return {"mode": "explicit-fixtures", "refundFixtureReady": True}
            if body == {"operation": "drain"}:
                return ReconciliationWorker(app.state.reconciliation_queue, payments).run_once()
            if body == {"operation": "stats"}:
                return {"mode": "explicit-fixtures", "checkoutPosts": len(provider.checkout_keys)}
            raise HTTPException(status_code=422, detail="Unknown fixture command")

        uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False, log_level="warning")


if __name__ == "__main__":
    main()
