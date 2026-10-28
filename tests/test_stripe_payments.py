"""Explicit HTTPX Stripe fixtures: no test represents a real sandbox payment."""
import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
from urllib.parse import parse_qs

import httpx
import pytest

from widemind.economy.service import EconomyError, EconomyService
from widemind.economy.stripe import API_VERSION, StripePayments

ACCOUNT = "acct_fixture"
SECRET = "whsec_fixture_signing_secret"


class StripeFixture:
    def __init__(self):
        self.calls = []
        self.checkout_keys = []
        self.fail_once = False
        self.extra = None
        self.refunds, self.disputes = [], []
        self.records = {
            "/account": {"id": ACCOUNT, "charges_enabled": True, "details_submitted": True},
            "/checkout/sessions/cs_fixture": {"id": "cs_fixture", "mode": "payment", "livemode": False, "payment_intent": "pi_fixture"},
            "/payment_intents/pi_fixture": {"id": "pi_fixture", "livemode": False, "latest_charge": "ch_fixture"},
            "/charges/ch_fixture": {"id": "ch_fixture", "livemode": False, "payment_intent": "pi_fixture", "amount": 1000, "balance_transaction": "txn_receipt"},
            "/balance_transactions/txn_receipt": self.tx("txn_receipt", 1000, 59),
        }

    @staticmethod
    def tx(identifier, amount, fee=0, status="available"):
        return {"id": identifier, "amount": amount, "fee": fee, "net": amount-fee,
                "currency": "usd", "status": status, "type": "charge", "source": "ch_fixture",
                "created": 1_700_000_000, "available_on": 1_700_000_100, "fee_details": [], "exchange_rate": None}

    def __call__(self, request):
        assert request.url.host == "api.stripe.com"
        assert request.headers["Stripe-Account"] == ACCOUNT
        assert request.headers["Stripe-Version"] == API_VERSION
        assert request.headers["Authorization"] == "Bearer sk_test_fixture"
        path = request.url.path.removeprefix("/v1")
        self.calls.append((request.method, path, dict(request.url.params)))
        if self.extra:
            response = self.extra(request, path)
            if response is not None:
                return response
        if request.method == "POST" and path == "/checkout/sessions":
            self.checkout_keys.append(request.headers["Idempotency-Key"])
            assert parse_qs(request.content.decode())["line_items[0][price_data][unit_amount]"] == ["1000"]
            if self.fail_once:
                self.fail_once = False
                raise httpx.ReadTimeout("fixture response lost", request=request)
            return httpx.Response(200, json={"id": "cs_created", "livemode": False, "url": "https://checkout.stripe.com/c/pay/fixture"})
        if path == "/refunds":
            return httpx.Response(200, json={"data": self.refunds, "has_more": False})
        if path == "/disputes":
            return httpx.Response(200, json={"data": self.disputes, "has_more": False})
        if path == "/checkout/sessions":
            return httpx.Response(200, json={"data": [self.records["/checkout/sessions/cs_fixture"]], "has_more": False})
        return httpx.Response(200, json=self.records[path])


@pytest.fixture
def setup(tmp_path):
    service = EconomyService(tmp_path / "economy.sqlite")
    service.create_run("run")
    service.register_payment("run", "stripe", ACCOUNT, "cs_fixture", "job", "binding")
    stripe = StripeFixture()
    with httpx.Client(transport=httpx.MockTransport(stripe)) as client:
        adapter = StripePayments(service, api_key="sk_test_fixture", account=ACCOUNT, mode="test", webhook_secret=SECRET,
                                 success_url="http://localhost:8000/success", cancel_url="http://localhost:8000/cancel", client=client)
        yield service, stripe, adapter


def signed(event, timestamp=None):
    timestamp = int(time.time()) if timestamp is None else timestamp
    raw = json.dumps(event, separators=(",", ":")).encode()
    digest = hmac.new(SECRET.encode(), str(timestamp).encode()+b"."+raw, hashlib.sha256).hexdigest()
    return raw, f"t={timestamp},v1={digest}"


def event(kind="checkout.session.completed", identifier="cs_fixture", **kwargs):
    return {"id": "evt_fixture", "livemode": False, "type": kind,
            "data": {"object": {"id": identifier}}, **kwargs}


def test_checkout_persisted_before_network_and_replay_cached(setup):
    service, stripe, adapter = setup
    def before(request, path):
        if request.method == "POST":
            assert service.checkout("stripe", ACCOUNT, "test", "request")["status"] == "pending"
    stripe.extra = before
    result = adapter.create_checkout("run", "request", "job", 10_000_000)
    assert result["payment_id"] == "cs_created"
    assert adapter.create_checkout("run", "request", "job", 10_000_000) == result
    assert len(stripe.checkout_keys) == 1
    assert service.balance("run")["posted_micro_usd"] == 5_000_000


def test_unknown_checkout_retries_same_provider_key_and_conflicting_retry_fails(setup):
    service, stripe, adapter = setup
    stripe.fail_once = True
    with pytest.raises(EconomyError, match="unknown"):
        adapter.create_checkout("run", "request", "job", 10_000_000)
    assert service.checkout("stripe", ACCOUNT, "test", "request")["payment_id"] is None
    adapter.create_checkout("run", "request", "job", 10_000_000)
    assert len(stripe.checkout_keys) == 2 and stripe.checkout_keys[0] == stripe.checkout_keys[1]
    with pytest.raises(EconomyError) as exc:
        adapter.create_checkout("run", "request", "job", 20_000_000)
    assert exc.value.code == "idempotency_conflict"


def test_expired_unknown_checkout_is_not_blindly_retried(setup):
    service, stripe, adapter = setup
    service.record_checkout("run", "stripe", ACCOUNT, "test", "request", "job", 10_000_000,
                            {"description": "WideMind order", "success_url": adapter.success_url, "cancel_url": adapter.cancel_url})
    adapter.clock = lambda: time.time()+24*3600
    with pytest.raises(EconomyError) as exc:
        adapter.create_checkout("run", "request", "job", 10_000_000)
    assert exc.value.code == "checkout_reconciliation_required"
    assert not stripe.calls


@pytest.mark.parametrize("amount", [True, 1.0, 0, -10000, 999_999, 499_999, 100_000_000*10_000])
def test_checkout_cents_and_bounds(amount, setup):
    _, stripe, adapter = setup
    with pytest.raises(EconomyError):
        adapter.create_checkout("run", "request", "job", amount)
    assert not stripe.calls


def test_settled_receipt_gross_fee_net_once_and_customer_hold(setup):
    service, _, adapter = setup
    adapter.reconcile("cs_fixture")
    balance = service.balance("run")
    assert balance["posted_micro_usd"] == 14_410_000
    assert balance["held_micro_usd"] == 9_410_000
    assert balance["available_micro_usd"] == 5_000_000
    adapter.reconcile("cs_fixture")
    assert service.balance("run") == balance
    kinds = [r["kind"] for r in service.ledger("run")]
    assert kinds.count("customer_receipt") == 1 and kinds.count("processor_fee") == 1


def test_pending_balance_never_credited_and_later_settles(setup):
    service, stripe, adapter = setup
    stripe.records["/balance_transactions/txn_receipt"]["status"] = "pending"
    adapter.handle_webhook(*signed(event()))
    # Known processor fees are liabilities even before positive receipt settlement.
    assert service.balance("run")["posted_micro_usd"] == 4_410_000
    stripe.records["/balance_transactions/txn_receipt"]["status"] = "available"
    adapter.reconcile("cs_fixture")
    assert service.balance("run")["posted_micro_usd"] == 14_410_000


def test_duplicate_and_reversed_event_names_do_not_duplicate_money(setup):
    service, _, adapter = setup
    raw, sig = signed(event())
    adapter.handle_webhook(raw, sig)
    adapter.handle_webhook(raw, sig)
    adapter.handle_webhook(*signed(event("checkout.session.expired", id="evt_late")))
    assert service.payment("stripe", ACCOUNT, "test", "cs_fixture")["status"] == "settled"
    assert service.balance("run")["posted_micro_usd"] == 14_410_000


@pytest.mark.parametrize("offset", [-301, 301])
def test_signature_rejects_past_and_future(offset, setup):
    _, stripe, adapter = setup
    with pytest.raises(EconomyError) as exc:
        adapter.handle_webhook(*signed(event(), int(time.time())+offset))
    assert exc.value.code == "invalid_signature" and not stripe.calls


def test_raw_body_tamper_and_key_rotation_signature(setup):
    _, stripe, adapter = setup
    raw, sig = signed(event())
    with pytest.raises(EconomyError):
        adapter.handle_webhook(raw+b" ", sig)
    assert not stripe.calls
    adapter.handle_webhook(raw, sig+",v1="+"0"*64)
    assert stripe.calls


@pytest.mark.parametrize("overrides", [{"livemode": True}, {"account": "acct_other"}])
def test_webhook_account_and_mode_scope(overrides, setup):
    _, stripe, adapter = setup
    with pytest.raises(EconomyError):
        adapter.handle_webhook(*signed(event(**overrides)))
    assert not stripe.calls


def test_wrong_credentials_account_fails_closed(setup):
    service, stripe, adapter = setup
    stripe.records["/account"]["id"] = "acct_other"
    with pytest.raises(EconomyError) as exc:
        adapter.reconcile("cs_fixture")
    assert exc.value.code == "provider_account_mismatch"
    assert service.balance("run")["posted_micro_usd"] == 5_000_000


def test_metadata_alone_cannot_register_checkout_or_credit(setup):
    service, stripe, adapter = setup
    stripe.records["/checkout/sessions/cs_fixture"]["id"] = "cs_unregistered"
    stripe.records["/checkout/sessions/cs_fixture"]["metadata"] = {"run_id": "run"}
    with pytest.raises(EconomyError) as exc:
        adapter.handle_webhook(*signed(event("payment_intent.succeeded", "pi_fixture")))
    assert exc.value.code == "unmapped_payment"
    assert service.balance("run")["posted_micro_usd"] == 5_000_000


def test_refund_pending_liability_posts_once_then_available_does_not_conflict(setup):
    service, stripe, adapter = setup
    stripe.refunds = [
        {"id": "re_pending", "charge": "ch_fixture", "amount": 100, "status": "pending", "balance_transaction": None},
        {"id": "re_debit", "charge": "ch_fixture", "amount": 200, "status": "succeeded", "balance_transaction": "txn_refund"},
    ]  # Refund objects do not require a livemode field.
    stripe.records["/balance_transactions/txn_refund"] = stripe.tx("txn_refund", -200, status="pending")
    adapter.reconcile("cs_fixture")
    assert service.balance("run")["posted_micro_usd"] == 12_410_000
    assert service.payment("stripe", ACCOUNT, "test", "cs_fixture")["pending_liability_micro_usd"] == 1_000_000
    stripe.records["/balance_transactions/txn_refund"]["status"] = "available"
    adapter.reconcile("cs_fixture")
    assert service.balance("run")["posted_micro_usd"] == 12_410_000


def test_dispute_fee_and_won_reversal_have_distinct_ids(setup):
    service, stripe, adapter = setup
    stripe.disputes = [{"id": "dp_fixture", "livemode": False, "charge": "ch_fixture", "amount": 1000,
                        "status": "lost", "balance_transactions": [stripe.tx("txn_dispute", -1000, 1500)]}]
    adapter.reconcile("cs_fixture")
    assert service.balance("run")["posted_micro_usd"] == -10_590_000
    stripe.disputes[0].update(status="won", balance_transactions=[stripe.tx("txn_dispute", -1000, 1500), stripe.tx("txn_reversal", 1000, -1500)])
    adapter.reconcile("cs_fixture")
    assert service.balance("run")["posted_micro_usd"] == 14_410_000


def test_dispute_without_known_debit_retains_hold(setup):
    service, stripe, adapter = setup
    stripe.disputes = [{"id": "dp_fixture", "livemode": False, "charge": "ch_fixture", "amount": 2000,
                        "status": "needs_response", "balance_transactions": []}]
    adapter.reconcile("cs_fixture")
    assert service.balance("run")["held_micro_usd"] == 20_000_000


def test_paginated_refunds_complete_before_posting(setup):
    service, stripe, adapter = setup
    def pages(request, path):
        if path != "/refunds":
            return None
        first = request.url.params.get("starting_after") is None
        item = {"id": "re_first" if first else "re_second", "charge": "ch_fixture", "amount": 100, "status": "pending", "balance_transaction": None}
        return httpx.Response(200, json={"data": [item], "has_more": first})
    stripe.extra = pages
    adapter.reconcile("cs_fixture")
    assert service.payment("stripe", ACCOUNT, "test", "cs_fixture")["pending_liability_micro_usd"] == 2_000_000
    assert [call[2].get("starting_after") for call in stripe.calls if call[1] == "/refunds"] == [None, "re_first"]


def test_incomplete_pages_post_nothing(setup):
    service, stripe, adapter = setup
    stripe.extra = lambda request, path: httpx.Response(200, json={"data": [], "has_more": True}) if path == "/refunds" else None
    with pytest.raises(EconomyError) as exc:
        adapter.reconcile("cs_fixture")
    assert exc.value.code == "incomplete_provider_snapshot"
    assert service.balance("run")["posted_micro_usd"] == 5_000_000


@pytest.mark.parametrize("mutation", [{"currency": "eur"}, {"fee": 1.5}, {"net": 999}, {"status": "unknown"}])
def test_invalid_native_money_cannot_credit(mutation, setup):
    service, stripe, adapter = setup
    stripe.records["/balance_transactions/txn_receipt"].update(mutation)
    with pytest.raises(EconomyError):
        adapter.reconcile("cs_fixture")
    assert service.balance("run")["posted_micro_usd"] == 5_000_000


def test_refund_from_other_charge_is_rejected(setup):
    service, stripe, adapter = setup
    stripe.refunds = [{"id": "re_wrong", "charge": "ch_other", "amount": 100, "status": "pending"}]
    with pytest.raises(EconomyError):
        adapter.reconcile("cs_fixture")
    assert service.balance("run")["posted_micro_usd"] == 5_000_000


def test_stale_snapshot_retries_with_fresh_provider_fetch(setup, monkeypatch):
    service, stripe, adapter = setup
    original = service.apply_payment_snapshot
    first = True
    def race(snapshot, **kwargs):
        nonlocal first
        if first:
            first = False
            raise EconomyError("stale_snapshot", "fixture race", 409)
        return original(snapshot, **kwargs)
    monkeypatch.setattr(service, "apply_payment_snapshot", race)
    adapter.reconcile("cs_fixture")
    assert sum(call[1] == "/checkout/sessions/cs_fixture" for call in stripe.calls) == 2


def test_unsupported_signed_event_does_not_touch_provider_or_money(setup):
    service, stripe, adapter = setup
    result = adapter.handle_webhook(*signed(event("customer.updated", "cus_fixture")))
    assert result["handled"] is False and not stripe.calls
    assert service.balance("run")["posted_micro_usd"] == 5_000_000


def test_charge_event_finds_only_registered_session(setup):
    service, _, adapter = setup
    adapter.handle_webhook(*signed(event("charge.refunded", "ch_fixture")))
    assert service.balance("run")["posted_micro_usd"] == 14_410_000


def test_pending_refund_fee_rebate_waits_for_settlement(setup):
    service, stripe, adapter = setup
    stripe.refunds = [{"id": "re_fixture", "charge": "ch_fixture", "amount": 1000,
                       "status": "succeeded", "balance_transaction": "txn_refund"}]
    stripe.records["/balance_transactions/txn_refund"] = stripe.tx("txn_refund", -1000, -59, status="pending")
    adapter.reconcile("cs_fixture")
    assert service.balance("run")["posted_micro_usd"] == 4_410_000
    stripe.records["/balance_transactions/txn_refund"]["status"] = "available"
    adapter.reconcile("cs_fixture")
    assert service.balance("run")["posted_micro_usd"] == 5_000_000
    adapter.reconcile("cs_fixture")
    assert service.balance("run")["posted_micro_usd"] == 5_000_000


def test_non_ascii_signature_is_rejected_without_server_error(setup):
    _, stripe, adapter = setup
    raw, _ = signed(event())
    with pytest.raises(EconomyError) as exc:
        adapter.handle_webhook(raw, f"t={int(time.time())},v1=" + "é"*64)
    assert exc.value.code == "invalid_signature" and exc.value.status == 400
    assert not stripe.calls


@pytest.mark.parametrize("invalid_type", [None, [], {}, 10])
def test_signed_malformed_type_is_validation_error(invalid_type, setup):
    _, stripe, adapter = setup
    bad = event()
    bad["type"] = invalid_type
    with pytest.raises(EconomyError) as exc:
        adapter.handle_webhook(*signed(bad))
    assert exc.value.code == "invalid_webhook"
    assert not stripe.calls
