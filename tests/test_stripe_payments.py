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
            "/checkout/sessions/cs_fixture": {"id": "cs_fixture", "mode": "payment", "livemode": False, "payment_intent": "pi_fixture", "currency": "usd", "amount_total": 1000},
            "/payment_intents/pi_fixture": {"id": "pi_fixture", "livemode": False, "latest_charge": "ch_fixture"},
            "/charges/ch_fixture": {"id": "ch_fixture", "livemode": False, "payment_intent": "pi_fixture", "amount": 1000, "amount_captured": 1000, "paid": True, "captured": True, "currency": "usd", "balance_transaction": "txn_receipt"},
            "/balance_transactions/txn_receipt": self.tx("txn_receipt", 1000, 59),
        }

    @staticmethod
    def tx(identifier, amount, fee=0, status="available"):
        return {"id": identifier, "amount": amount, "fee": fee, "net": amount-fee,
                "currency": "usd", "status": status, "type": "charge", "source": "ch_fixture" if identifier == "txn_receipt" else None,
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
        if path == "/charges":
            return httpx.Response(200, json={"data": [record for key,record in self.records.items() if key.startswith("/charges/") and record.get("payment_intent") == request.url.params["payment_intent"]], "has_more": False})
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


def test_succeeded_refund_without_balance_record_reserves_full_principal(setup):
    service, stripe, adapter = setup
    adapter.reconcile("cs_fixture")
    service.release_payment_hold("stripe", ACCOUNT, "test", "cs_fixture", "fulfilled")
    stripe.refunds = [{"id": "re_fixture", "charge": "ch_fixture", "amount": 100,
                       "status": "succeeded", "balance_transaction": None}]
    adapter.reconcile("cs_fixture")
    assert service.balance("run")["held_micro_usd"] == 1_000_000


@pytest.mark.parametrize("kind", ["refund", "dispute"])
def test_fee_only_balance_record_does_not_erase_principal_exposure(kind, setup):
    service, stripe, adapter = setup
    adapter.reconcile("cs_fixture")
    service.release_payment_hold("stripe", ACCOUNT, "test", "cs_fixture", "fulfilled")
    if kind == "refund":
        stripe.refunds = [{"id": "re_fixture", "charge": "ch_fixture", "amount": 100,
                           "status": "succeeded", "balance_transaction": "txn_fee"}]
        stripe.records["/balance_transactions/txn_fee"] = stripe.tx("txn_fee", 0, 15)
    else:
        stripe.disputes = [{"id": "dp_fixture", "livemode": False, "charge": "ch_fixture", "amount": 100,
                            "status": "needs_response", "balance_transactions": [stripe.tx("txn_fee", 0, 15)]}]
    adapter.reconcile("cs_fixture")
    assert service.balance("run")["held_micro_usd"] == 1_000_000
    assert service.balance("run")["posted_micro_usd"] == 14_260_000


@pytest.mark.parametrize("mutate", ["intent_id", "charge_id", "bt_id", "source", "session_total", "currency", "captured_amount"])
def test_cross_record_identity_or_amount_mismatch_fails_closed(mutate, setup):
    service, stripe, adapter = setup
    if mutate == "intent_id":
        stripe.records["/payment_intents/pi_fixture"]["id"] = "pi_other"
    elif mutate == "charge_id":
        stripe.records["/charges/ch_fixture"]["id"] = "ch_other"
    elif mutate == "bt_id":
        stripe.records["/balance_transactions/txn_receipt"]["id"] = "txn_other"
    elif mutate == "source":
        stripe.records["/balance_transactions/txn_receipt"]["source"] = "ch_other"
    elif mutate == "session_total":
        stripe.records["/checkout/sessions/cs_fixture"]["amount_total"] = 999
    elif mutate == "currency":
        stripe.records["/charges/ch_fixture"]["currency"] = "eur"
    else:
        stripe.records["/charges/ch_fixture"]["amount_captured"] = 500
    with pytest.raises(EconomyError):
        adapter.reconcile("cs_fixture")
    assert service.balance("run")["posted_micro_usd"] == 5_000_000


def test_authorization_without_capture_does_not_create_cash(setup):
    service, stripe, adapter = setup
    stripe.records["/charges/ch_fixture"].update(captured=False, amount_captured=0)
    adapter.reconcile("cs_fixture")
    assert service.balance("run")["posted_micro_usd"] == 4_410_000
    assert service.payment("stripe", ACCOUNT, "test", "cs_fixture")["status"] == "pending"


def test_prior_failed_charge_and_its_reversal_are_reconciled_not_only_latest(setup):
    service, stripe, adapter = setup
    prior = dict(stripe.records["/charges/ch_fixture"])
    prior.update(id="ch_prior", paid=False, captured=False, amount_captured=0,
                 balance_transaction="txn_prior", failure_balance_transaction="txn_failure")
    stripe.records["/charges/ch_prior"] = prior
    stripe.records["/balance_transactions/txn_prior"] = stripe.tx("txn_prior", 1000, 59)
    stripe.records["/balance_transactions/txn_prior"]["source"] = "ch_prior"
    stripe.records["/balance_transactions/txn_failure"] = stripe.tx("txn_failure", -1000, -59)
    stripe.records["/balance_transactions/txn_failure"]["source"] = "ch_prior"
    adapter.reconcile("cs_fixture")
    assert service.balance("run")["posted_micro_usd"] == 14_410_000
    refs = [row["reference"] for row in service.ledger("run")]
    assert "txn_prior" in refs and "txn_failure" in refs
    assert service.payment("stripe", ACCOUNT, "test", "cs_fixture")["status"] == "settled"


def test_expanded_source_private_customer_fields_are_not_persisted(setup):
    service, stripe, adapter = setup
    stripe.records["/balance_transactions/txn_receipt"]["source"] = {"id": "ch_fixture", "billing_details": {"email": "private@example.invalid"}}
    adapter.reconcile("cs_fixture")
    assert "private@example.invalid" not in json.dumps(service.ledger("run"))


def test_legacy_charge_refund_updated_event_maps_refund_object(setup):
    service, stripe, adapter = setup
    stripe.records["/refunds/re_fixture"] = {"id": "re_fixture", "charge": "ch_fixture"}
    adapter.handle_webhook(*signed(event("charge.refund.updated", "re_fixture")))
    assert service.balance("run")["posted_micro_usd"] == 14_410_000


# Recovery fixtures are authenticated synthetic Stripe records, never live payments.
def recovery_fixture(setup, *, request="lost-request", status="open", url="https://checkout.stripe.com/c/pay/recovered"):
    service, stripe, adapter = setup
    claim = service.record_checkout("run", "stripe", ACCOUNT, "test", request, "job", 10_000_000,
                                    {"description": "WideMind order", "success_url": adapter.success_url, "cancel_url": adapter.cancel_url})
    created = int(datetime.fromisoformat(claim["created_at"]).timestamp())
    request_key = hashlib.sha256(json.dumps(["stripe", ACCOUNT, "test", "run", request], separators=(",", ":")).encode()).hexdigest()
    session = dict(stripe.records["/checkout/sessions/cs_fixture"], id="cs_recovered", created=created,
                   client_reference_id=request_key, metadata={"widemind_request": request_key}, status=status, url=url)
    stripe.records["/checkout/sessions/cs_recovered"] = session
    stripe.records["/checkout/sessions/cs_fixture"] = session
    return claim, session


def test_recover_after_safe_post_window_reads_existing_session_without_new_post(setup):
    service, stripe, adapter = setup
    claim, session = recovery_fixture(setup)
    adapter.clock = lambda: time.time()+25*3600
    result = adapter.recover_checkout("lost-request")
    assert result["payment_id"] == "cs_recovered" and result["url"] == session["url"]
    assert service.payment("stripe", ACCOUNT, "test", "cs_recovered")["run_id"] == "run"
    assert service.balance("run")["posted_micro_usd"] == 5_000_000
    assert not stripe.checkout_keys and all(call[0] == "GET" for call in stripe.calls)
    listing = next(call for call in stripe.calls if call[1] == "/checkout/sessions")
    assert listing[2]["created[gte]"] == str(session["created"]-300)
    assert listing[2]["created[lte]"] == str(session["created"]+23*3600+300)
    calls = list(stripe.calls)
    assert adapter.recover_checkout("lost-request") == result and stripe.calls == calls
    # Repeating normal creation after recovery also cannot create another session.
    assert adapter.create_checkout("run", "lost-request", "job", 10_000_000) == result
    assert not stripe.checkout_keys


@pytest.mark.parametrize("status", ["complete", "expired"])
def test_recover_completed_session_without_hosted_url_and_do_not_repost(status, setup):
    service, stripe, adapter = setup
    recovery_fixture(setup, status=status, url=None)
    result = adapter.recover_checkout("lost-request")
    assert result["payment_id"] == "cs_recovered" and result["url"] is None
    assert adapter.create_checkout("run", "lost-request", "job", 10_000_000) == result
    assert not stripe.checkout_keys
    assert service.balance("run")["posted_micro_usd"] == 5_000_000


@pytest.mark.parametrize("mutation", [
    {"amount_total": 999}, {"currency": "eur"}, {"livemode": True}, {"mode": "subscription"},
    {"created": 0}, {"created": 10**20}, {"created": True}, {"created": 1.5}, {"status": "unknown"},
    {"client_reference_id": "other"}, {"metadata": {"widemind_request": "other"}},
])
def test_recovery_rejects_existing_record_mismatches_without_binding(mutation, setup):
    service, stripe, adapter = setup
    _, session = recovery_fixture(setup)
    session.update(mutation)
    with pytest.raises(EconomyError):
        adapter.recover_checkout("lost-request")
    assert service.checkout("stripe", ACCOUNT, "test", "lost-request")["payment_id"] is None
    assert service.balance("run")["posted_micro_usd"] == 5_000_000 and not stripe.checkout_keys


def test_recovery_revalidates_fresh_get_instead_of_trusting_list(setup):
    service, stripe, adapter = setup
    _, session = recovery_fixture(setup)
    def stale_list(request, path):
        if path == "/checkout/sessions":
            listed = dict(session)
            session["amount_total"] = 999
            return httpx.Response(200, json={"data": [listed], "has_more": False})
    stripe.extra = stale_list
    with pytest.raises(EconomyError) as exc:
        adapter.recover_checkout("lost-request")
    assert exc.value.code == "checkout_recovery_mismatch"
    assert service.checkout("stripe", ACCOUNT, "test", "lost-request")["payment_id"] is None


def test_recovery_requires_exactly_one_match_across_complete_pagination(setup):
    service, stripe, adapter = setup
    _, session = recovery_fixture(setup)
    def duplicates(request, path):
        if path == "/checkout/sessions":
            first = request.url.params.get("starting_after") is None
            item = dict(session, id="cs_first" if first else "cs_second")
            return httpx.Response(200, json={"data": [item], "has_more": first})
    stripe.extra = duplicates
    with pytest.raises(EconomyError) as exc:
        adapter.recover_checkout("lost-request")
    assert exc.value.code == "checkout_ambiguous"
    assert sum(call[1] == "/checkout/sessions" for call in stripe.calls) == 2
    assert service.checkout("stripe", ACCOUNT, "test", "lost-request")["payment_id"] is None
    assert not stripe.checkout_keys


def test_recovery_does_not_bind_from_partial_listing(setup):
    service, stripe, adapter = setup
    _, session = recovery_fixture(setup)
    def incomplete(request, path):
        if path == "/checkout/sessions":
            if request.url.params.get("starting_after") is None:
                return httpx.Response(200, json={"data": [session], "has_more": True})
            return httpx.Response(200, json={"data": [], "has_more": True})
    stripe.extra = incomplete
    with pytest.raises(EconomyError) as exc:
        adapter.recover_checkout("lost-request")
    assert exc.value.code == "incomplete_provider_snapshot"
    assert service.checkout("stripe", ACCOUNT, "test", "lost-request")["payment_id"] is None


def test_recovery_without_matching_provider_object_keeps_unknown_request(setup):
    service, stripe, adapter = setup
    recovery_fixture(setup)
    stripe.records["/checkout/sessions/cs_fixture"]["client_reference_id"] = "unrelated"
    with pytest.raises(EconomyError) as exc:
        adapter.recover_checkout("lost-request")
    assert exc.value.code == "checkout_not_found"
    assert service.checkout("stripe", ACCOUNT, "test", "lost-request")["payment_id"] is None
    assert not stripe.checkout_keys


@pytest.mark.parametrize("url", [None, "http://checkout.stripe.com/x", "https://other.invalid/x", "https://user@checkout.stripe.com/x", "https://checkout.stripe.com:0/x", "https://checkout.stripe.com:wrong/x", "https://checkout.stripe.com:444/x", "https://checkout.stripe.com/\nsecret"])
def test_recovery_open_session_requires_safe_hosted_url(url, setup):
    service, stripe, adapter = setup
    recovery_fixture(setup, url=url)
    with pytest.raises(EconomyError):
        adapter.recover_checkout("lost-request")
    assert service.checkout("stripe", ACCOUNT, "test", "lost-request")["payment_id"] is None


def test_webhook_before_local_binding_remains_retryable_until_explicit_recovery(setup):
    service, stripe, adapter = setup
    recovery_fixture(setup)
    raw, signature = signed(event(identifier="cs_recovered"))
    with pytest.raises(EconomyError) as exc:
        adapter.handle_webhook(raw, signature)
    assert exc.value.status == 404
    assert service.balance("run")["posted_micro_usd"] == 5_000_000
    adapter.recover_checkout("lost-request")
    adapter.handle_webhook(raw, signature)
    assert service.balance("run")["posted_micro_usd"] == 14_410_000


def test_durable_verified_event_processes_after_original_delivery_timestamp_expires(setup):
    service, stripe, adapter = setup
    raw, signature = signed(event())
    verified, digest = adapter.verify_webhook(raw, signature)
    adapter.clock = lambda: time.time()+3600
    with pytest.raises(EconomyError, match="allowed window"):
        adapter.verify_webhook(raw, signature)
    minimal = {"id": verified["id"], "type": verified["type"], "livemode": verified["livemode"],
               "data": {"object": {"id": verified["data"]["object"]["id"]}}}
    result = adapter.process_verified_event(minimal, digest)
    assert result["handled"] is True
    assert service.balance("run")["posted_micro_usd"] == 14_410_000
    adapter.process_verified_event(minimal, digest)
    assert service.balance("run")["posted_micro_usd"] == 14_410_000


@pytest.mark.parametrize("digest", [None, "", "x"*64, "é"*64, "1"*63])
def test_verified_queue_dispatch_still_rejects_invalid_digest(digest, setup):
    _, stripe, adapter = setup
    with pytest.raises(EconomyError):
        adapter.process_verified_event(event(), digest)
    assert not stripe.calls


@pytest.mark.parametrize("url", ["https://user@checkout.stripe.com/pay/x", "https://checkout.stripe.com:0/pay/x", "https://checkout.stripe.com:wrong/pay/x", "https://checkout.stripe.com/\nsecret"])
def test_checkout_creation_rejects_unsafe_hosted_url(url, setup):
    service, stripe, adapter = setup
    def bad_url(request, path):
        if request.method == "POST" and path == "/checkout/sessions":
            return httpx.Response(200, json={"id": "cs_badurl", "livemode": False, "url": url})
    stripe.extra = bad_url
    with pytest.raises(EconomyError) as exc:
        adapter.create_checkout("run", "badurl", "job", 10_000_000)
    assert exc.value.code == "invalid_provider_record"
    assert service.checkout("stripe", ACCOUNT, "test", "badurl")["payment_id"] is None


def test_nested_verified_dispatch_shares_one_request_budget_and_preserves_cash_on_exhaustion(setup, monkeypatch):
    import widemind.economy.stripe as module
    service, stripe, adapter = setup
    monkeypatch.setattr(module, 'MAX_OPERATION_REQUESTS', 4)
    with pytest.raises(EconomyError) as exc:
        adapter.handle_webhook(*signed(event()))
    assert exc.value.code == 'provider_operation_budget'
    assert len(stripe.calls) == 4
    assert service.balance('run')['posted_micro_usd'] == 5_000_000
    # A later operator/queue retry starts a new bounded attempt, never a partial posting.
    monkeypatch.setattr(module, 'MAX_OPERATION_REQUESTS', 16)
    adapter.handle_webhook(*signed(event()))
    assert service.balance('run')['posted_micro_usd'] == 14_410_000


def test_elapsed_provider_budget_fails_before_posting_and_later_operation_can_retry(setup):
    service, stripe, adapter = setup
    elapsed = [0.0]
    adapter._monotonic = lambda: elapsed[0]
    def expired(request, path):
        if path == '/account':
            elapsed[0] = 61.0
    stripe.extra = expired
    with pytest.raises(EconomyError) as exc:
        adapter.reconcile('cs_fixture')
    assert exc.value.code == 'provider_operation_budget'
    assert len(stripe.calls) == 1
    assert service.balance('run')['posted_micro_usd'] == 5_000_000
    stripe.extra = None
    adapter.reconcile('cs_fixture')
    assert service.balance('run')['posted_micro_usd'] == 14_410_000


def test_each_provider_timeout_is_limited_to_remaining_operation_budget(setup):
    service, stripe, adapter = setup
    elapsed, timeouts = [0.0], []
    adapter._monotonic = lambda: elapsed[0]
    def near_deadline(request, path):
        timeouts.append(request.extensions['timeout'])
        if path == '/account':
            elapsed[0] = 55.0
    stripe.extra = near_deadline
    adapter.reconcile('cs_fixture')
    assert timeouts[0]['read'] == 20.0 and timeouts[0]['connect'] == 10.0
    assert all(timeout['read'] == 5.0 and timeout['connect'] == 5.0 for timeout in timeouts[1:])
    assert service.balance('run')['posted_micro_usd'] == 14_410_000


def test_concurrent_operations_on_shared_adapter_have_independent_budgets(setup, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    import widemind.economy.stripe as module
    _, stripe, adapter = setup
    monkeypatch.setattr(module, 'MAX_OPERATION_REQUESTS', 1)
    barrier = Barrier(2)
    def parallel(request, path):
        assert path == '/account'
        barrier.wait(timeout=5)
    stripe.extra = parallel
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(adapter.verify_setup) for _ in range(2)]
        assert [future.result(timeout=10)['account'] for future in futures] == [ACCOUNT, ACCOUNT]
    assert len(stripe.calls) == 2


def test_oversized_provider_response_fails_closed_without_private_body_reflection(setup):
    from widemind.economy.stripe import MAX_PROVIDER_BYTES
    service, stripe, adapter = setup
    private_marker = 'fixture-private-metadata'
    stripe.extra = lambda request, path: httpx.Response(200, json={'id': ACCOUNT, 'padding': private_marker+'x'*MAX_PROVIDER_BYTES})
    with pytest.raises(EconomyError) as exc:
        adapter.reconcile('cs_fixture')
    assert exc.value.code == 'invalid_provider_record'
    assert private_marker not in str(exc.value)
    assert service.balance('run')['posted_micro_usd'] == 5_000_000


def test_redirects_are_rejected_even_if_external_client_enables_them(setup):
    service, stripe, adapter = setup
    adapter.client.follow_redirects = True
    stripe.extra = lambda request, path: httpx.Response(302, headers={'Location': 'https://other.invalid/redirect'}, json={'id': ACCOUNT})
    with pytest.raises(EconomyError) as exc:
        adapter.reconcile('cs_fixture')
    assert exc.value.code == 'provider_rejected'
    assert len(stripe.calls) == 1
    assert service.balance('run')['posted_micro_usd'] == 5_000_000


def test_operation_budget_is_checked_between_streamed_body_chunks_and_closes_response(setup):
    service, stripe, adapter = setup
    elapsed, closed = [0.0], []
    adapter._monotonic = lambda: elapsed[0]
    class SlowFixtureStream(httpx.SyncByteStream):
        def __iter__(self):
            yield b'{"id":'
            elapsed[0] = 61.0
            yield b'"acct_fixture"}'
        def close(self):
            closed.append(True)
    stripe.extra = lambda request, path: httpx.Response(200, stream=SlowFixtureStream())
    with pytest.raises(EconomyError) as exc:
        adapter.reconcile('cs_fixture')
    assert exc.value.code == 'provider_operation_budget'
    assert closed == [True]
    assert service.balance('run')['posted_micro_usd'] == 5_000_000
