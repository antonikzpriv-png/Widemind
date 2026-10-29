"""Explicit offline Stripe transport fixtures; no provider calls or money."""
import copy
import hashlib
import hmac
import json
import unittest
from urllib.parse import parse_qs, urlsplit

from widemind.economy.stripe import StripeClient, StripeError


class StripeTransportFixture:
    def __init__(self):
        self.calls = []
        self.routes = {}

    def add(self, method, path, value, status=200):
        self.routes[(method, path)] = (status, value)

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append((method, url, headers, body, timeout))
        parsed = urlsplit(url)
        key = (method, parsed.path)
        if key not in self.routes:
            raise AssertionError(f"unconfigured fixture route: {key}")
        status, document = self.routes[key]
        if callable(document):
            document = document(method, url, headers, body)
        raw = document if isinstance(document, bytes) else json.dumps(document).encode()
        return status, {}, raw


def session_fixture(**changes):
    result = {"id": "cs_test_fixture", "object": "checkout.session", "livemode": False,
              "currency": "usd", "status": "complete", "payment_status": "paid",
              "amount_total": 200, "url": "https://checkout.stripe.com/c/pay/cs_test_fixture",
              "metadata": {"run_id": "run-fixture", "job_id": "job-fixture", "action_id": "action-fixture"},
              "payment_intent": {"id": "pi_fixture", "object": "payment_intent", "livemode": False,
                  "currency": "usd", "status": "succeeded", "latest_charge": {
                    "id": "ch_fixture", "object": "charge", "livemode": False, "currency": "usd",
                    "payment_intent": "pi_fixture", "paid": True, "captured": True,
                    "amount": 200, "amount_captured": 200, "amount_refunded": 0,
                    "refunds": {"object": "list", "data": [], "has_more": False},
                    "balance_transaction": {"id": "txn_fixture", "object": "balance_transaction",
                        "currency": "usd", "status": "available", "amount": 200, "fee": 36, "net": 164}}}}
    result.update(changes)
    return result


def event_fixture(**changes):
    result = {"id": "evt_fixture", "type": "checkout.session.completed", "livemode": False,
              "data": {"object": session_fixture()}}
    result.update(changes)
    return result


class StripeTests(unittest.TestCase):
    def setUp(self):
        self.transport = StripeTransportFixture()
        self.client = StripeClient(api_key="sk_test_OFFLINE_FIXTURE", webhook_secret="whsec_OFFLINE_FIXTURE",
                                   mode="test", success_url="https://merchant.example.invalid/complete",
                                   cancel_url="https://merchant.example.invalid/cancel", transport=self.transport,
                                   clock=lambda: 1800000000)
        self.session = session_fixture()
        self.transport.add("GET", "/v1/checkout/sessions/cs_test_fixture", lambda *args: self.session)
        self.transport.add("GET", "/v1/disputes", {"object": "list", "data": [], "has_more": False})

    def signature(self, raw, timestamp=1800000000):
        digest = hmac.new(b"whsec_OFFLINE_FIXTURE", str(timestamp).encode() + b"." + raw, hashlib.sha256).hexdigest()
        return f"t={timestamp},v1={digest}"

    def inspect(self):
        return self.client.inspect_checkout(session_id="cs_test_fixture")

    def test_creation_exact_units_metadata_and_idempotency(self):
        self.transport.add("POST", "/v1/checkout/sessions", self.session)
        result = self.client.create_checkout(run_id="run-fixture", job_id="job-fixture", action_id="action-fixture", amount_micro_usd=2000000)
        self.assertEqual(result["id"], "cs_test_fixture")
        method, url, headers, body, timeout = self.transport.calls[-1]
        self.assertEqual(method, "POST")
        self.assertEqual(url, "https://api.stripe.com/v1/checkout/sessions")
        self.assertEqual(headers["Idempotency-Key"], "action-fixture")
        fields = parse_qs(body.decode())
        self.assertEqual(fields["line_items[0][price_data][unit_amount]"], ["200"])
        self.assertEqual(fields["payment_intent_data[metadata][job_id]"], ["job-fixture"])
        self.assertEqual(timeout, 15)
        for invalid in (2000001, True, 1.1, 0, -1):
            with self.subTest(value=invalid), self.assertRaises((ValueError, StripeError)):
                self.client.create_checkout(run_id="r", job_id="j", action_id="a", amount_micro_usd=invalid)
        self.assertEqual(len(self.transport.calls), 1)

    def test_pending_balance_is_not_a_settled_receipt(self):
        self.session["payment_intent"]["latest_charge"]["balance_transaction"]["status"] = "pending"
        result = self.inspect()
        self.assertEqual(result["status"], "pending")
        self.assertEqual(result["receipts_micro_usd"], 0)
        self.assertEqual(result["fees_micro_usd"], 0)
        self.assertEqual(len(self.transport.calls), 2)  # Independent disputes must still be checked.

    def test_available_balance_retains_gross_fees_and_native_reference(self):
        result = self.inspect()
        self.assertEqual(result["status"], "settled")
        self.assertEqual(result["receipts_micro_usd"], 2000000)
        self.assertEqual(result["fees_micro_usd"], 360000)
        self.assertEqual(result["provider_native"]["gross_cents"], 200)
        self.assertNotIn("api_key", json.dumps(result))
        self.assertEqual(result["run_id"], "run-fixture")

    def test_partial_and_full_refunds_query_current_authority(self):
        charge = self.session["payment_intent"]["latest_charge"]
        charge["amount_refunded"] = 50
        charge["refunds"]["data"] = [{"id": "re_fixture", "object": "refund", "currency": "usd", "charge": "ch_fixture", "amount": 50, "status": "succeeded", "balance_transaction": None}]
        first = self.inspect()
        self.assertEqual(first["status"], "partial")
        self.assertEqual(first["refunds_micro_usd"], 500000)
        charge["amount_refunded"] = 200
        charge["refunds"]["data"][0]["amount"] = 200
        second = self.inspect()
        self.assertEqual(second["status"], "refunded")
        self.assertEqual(second["refunds_micro_usd"], 2000000)
        self.assertEqual(second["receipts_micro_usd"], 2000000)

    def test_pending_refund_creates_exposure_not_completed_refund(self):
        charge = self.session["payment_intent"]["latest_charge"]
        charge["refunds"]["data"] = [{"id": "re_pending", "currency": "usd", "charge": "ch_fixture", "amount": 200, "status": "pending"}]
        result = self.inspect()
        self.assertEqual(result["refunds_micro_usd"], 0)
        self.assertEqual(result["pending_refunds_micro_usd"], 2000000)
        self.assertEqual(result["status"], "partial")

    def test_disputed_won_and_lost_outcomes_are_current_not_event_order(self):
        dispute = {"id": "dp_fixture", "object": "dispute", "currency": "usd", "charge": "ch_fixture", "amount": 200, "status": "under_review"}
        self.transport.add("GET", "/v1/disputes", {"object": "list", "data": [dispute], "has_more": False})
        active = self.inspect()
        self.assertEqual(active["status"], "disputed")
        self.assertEqual(active["disputed_micro_usd"], 2000000)
        self.assertEqual(active["refunds_micro_usd"], 0)
        dispute["status"] = "lost"
        lost = self.inspect()
        self.assertEqual(lost["status"], "refunded")
        self.assertEqual(lost["refunds_micro_usd"], 2000000)
        dispute["status"] = "won"
        won = self.inspect()
        self.assertEqual(won["status"], "settled")
        self.assertEqual(won["refunds_micro_usd"], 0)

    def test_overlapping_confirmed_liabilities_are_not_capped_away(self):
        charge = self.session["payment_intent"]["latest_charge"]
        charge["amount_refunded"] = 200
        charge["refunds"]["data"] = [{"id": "re_fixture", "currency": "usd", "charge": "ch_fixture", "amount": 200, "status": "succeeded"}]
        self.transport.add("GET", "/v1/disputes", {"object": "list", "data": [{"id": "dp_fixture", "currency": "usd", "charge": "ch_fixture", "amount": 200, "status": "lost"}], "has_more": False})
        result = self.inspect()
        self.assertEqual(result["refunds_micro_usd"], 4000000)
        self.assertEqual(result["provider_native"]["refund_cents"], 200)
        self.assertEqual(result["provider_native"]["lost_dispute_cents"], 200)

    def test_unknown_currency_mismatched_identity_and_fractional_native_money_fail(self):
        for change in (("currency", "eur"), ("amount", 200.0), ("amount", True)):
            with self.subTest(change=change):
                self.session = session_fixture()
                self.session["payment_intent"]["latest_charge"]["balance_transaction"][change[0]] = change[1]
                with self.assertRaises(StripeError):
                    self.inspect()
        self.session = session_fixture()
        self.session["payment_intent"]["latest_charge"]["payment_intent"] = "pi_foreign"
        with self.assertRaises(StripeError):
            self.inspect()

    def test_webhook_signature_timestamp_mode_and_account(self):
        raw = json.dumps(event_fixture()).encode()
        verified = self.client.verify_event(raw, self.signature(raw))
        self.assertEqual(verified["id"], "evt_fixture")
        for body, signature in ((raw + b" ", self.signature(raw)), (raw, self.signature(raw, 1799999699)), (raw, "t=1800000000,v1=" + "0" * 64)):
            with self.subTest(signature=signature), self.assertRaises(ValueError):
                self.client.verify_event(body, signature)
        for change in ({"livemode": True}, {"account": "acct_foreign"}, {"livemode": "false"}):
            wrong = json.dumps(event_fixture(**change)).encode()
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.client.verify_event(wrong, self.signature(wrong))
        self.assertEqual(self.transport.calls, [])

    def test_webhook_rejects_duplicate_keys_and_unbounded_bodies(self):
        raw = b'{"id":"evt_a","id":"evt_b","type":"balance.available","livemode":false,"data":{"object":{}}}'
        with self.assertRaises(ValueError):
            self.client.verify_event(raw, self.signature(raw))
        raw = b"x" * 2000001
        with self.assertRaises(ValueError):
            self.client.verify_event(raw, self.signature(raw))

    def test_reconciliation_only_gets_and_does_not_reissue_unknown_checkout(self):
        self.transport.add("GET", "/v1/checkout/sessions", {"object": "list", "data": [self.session], "has_more": False})
        result = self.client.reconcile_checkout(action_id="action-fixture")
        self.assertEqual(result.state, "succeeded")
        self.assertTrue(all(call[0] == "GET" for call in self.transport.calls))
        self.transport.add("GET", "/v1/checkout/sessions", {"object": "list", "data": [], "has_more": False})
        self.assertEqual(self.client.reconcile_checkout(action_id="unknown").state, "unknown")
        self.assertTrue(all(call[0] == "GET" for call in self.transport.calls))

    def test_expired_session_confirms_terminal_failure_and_carries_id(self):
        self.session["status"] = "expired"
        result = self.client.reconcile_checkout(action_id="action-fixture", session_id="cs_test_fixture")
        self.assertEqual(result.state, "failed")
        self.assertEqual(result.data["id"], "cs_test_fixture")

    def test_reconciliation_is_bounded_and_sanitizes_provider_errors(self):
        self.transport.add("GET", "/v1/checkout/sessions", {"object": "list", "data": [{**self.session, "metadata": {"action_id": "not-the-request"}}], "has_more": True})
        self.assertEqual(self.client.reconcile_checkout(action_id="missing").state, "unknown")
        self.assertEqual(len(self.transport.calls), 10)
        self.transport.calls.clear()
        self.transport.add("GET", "/v1/checkout/sessions", b"provider secret contents", 500)
        result = self.client.reconcile_checkout(action_id="missing")
        self.assertEqual(result.state, "unknown")
        self.assertNotIn("secret contents", result.reason)

    def test_response_limits_and_redirect_do_not_leak_credentials(self):
        self.transport.add("POST", "/v1/checkout/sessions", b"x" * 2000001)
        with self.assertRaises(StripeError):
            self.client.create_checkout(run_id="r", job_id="j", action_id="a", amount_micro_usd=10000)
        self.transport.add("POST", "/v1/checkout/sessions", b"", 302)
        with self.assertRaises(StripeError) as error:
            self.client.create_checkout(run_id="r", job_id="j", action_id="a", amount_micro_usd=10000)
        self.assertEqual(error.exception.status, 302)
        self.assertNotIn("sk_test", str(error.exception))

    def test_event_mapping_uses_payment_intent_and_current_sessions(self):
        self.transport.add("GET", "/v1/checkout/sessions", {"object": "list", "data": [{**self.session, "payment_intent": "pi_fixture"}], "has_more": False})
        event = event_fixture(type="charge.refunded", data={"object": {"id": "ch_fixture", "object": "charge", "payment_intent": "pi_fixture"}})
        self.assertEqual(self.client.session_ids_for_event(event), ["cs_test_fixture"])
        self.assertEqual(parse_qs(urlsplit(self.transport.calls[-1][1]).query)["payment_intent"], ["pi_fixture"])
        self.assertEqual(self.client.session_ids_for_event(event_fixture(type="balance.available", data={"object": {"object": "balance"}})), [])

    def test_dispute_and_refund_fee_adjustments_are_not_lost(self):
        charge = self.session["payment_intent"]["latest_charge"]
        charge["amount_refunded"] = 100
        charge["refunds"]["data"] = [{"id": "re_fixture", "currency": "usd", "charge": "ch_fixture", "amount": 100,
            "status": "succeeded", "balance_transaction": {"id": "txn_refund", "currency": "usd", "fee": -10}}]
        self.transport.add("GET", "/v1/disputes", {"object": "list", "data": [{"id": "dp_fixture", "currency": "usd",
            "charge": "ch_fixture", "amount": 100, "status": "lost", "balance_transactions": [
                {"id": "txn_dispute", "currency": "usd", "amount": -100, "fee": 1500}]}], "has_more": False})
        result = self.inspect()
        self.assertEqual(result["fees_micro_usd"], (36 - 10 + 1500) * 10000)
        self.assertEqual(result["refunds_micro_usd"], 2000000)

    def test_bounded_request_budget_prevents_unbounded_transitive_fee_reads(self):
        charge = self.session["payment_intent"]["latest_charge"]
        refunds = [{"id": f"re_{index}", "currency": "usd", "charge": "ch_fixture", "amount": 1,
                    "status": "succeeded", "balance_transaction": f"txn_{index}"} for index in range(60)]
        charge["amount_refunded"] = 60
        charge["refunds"]["data"] = refunds
        for index in range(60):
            self.transport.add("GET", f"/v1/balance_transactions/txn_{index}", {"id": f"txn_{index}", "currency": "usd", "fee": 0})
        with self.assertRaises(StripeError):
            self.inspect()
        self.assertEqual(len(self.transport.calls), 50)

    def test_duplicate_checkout_identity_is_uncertain_not_arbitrarily_selected(self):
        self.transport.add("GET", "/v1/checkout/sessions", {"object": "list", "data": [self.session,
            {**self.session, "id": "cs_test_duplicate"}], "has_more": False})
        self.assertEqual(self.client.reconcile_checkout(action_id="action-fixture").state, "unknown")
        self.assertTrue(all(call[0] == "GET" for call in self.transport.calls))

    def test_expanded_resource_reference_must_match_response_identity(self):
        intent = copy.deepcopy(self.session["payment_intent"])
        self.session["payment_intent"] = "pi_expected"
        self.transport.add("GET", "/v1/payment_intents/pi_expected", intent)
        with self.assertRaises(StripeError):
            self.inspect()

    def test_pending_refund_may_appear_in_provider_aggregate_but_is_only_exposure(self):
        charge = self.session["payment_intent"]["latest_charge"]
        charge["amount_refunded"] = 200
        charge["refunds"]["data"] = [{"id": "re_pending", "currency": "usd", "charge": "ch_fixture", "amount": 200, "status": "pending"}]
        result = self.inspect()
        self.assertEqual(result["refunds_micro_usd"], 0)
        self.assertEqual(result["pending_refunds_micro_usd"], 2000000)

    def test_idempotency_conflicts_remain_uncertain_until_reconciled(self):
        self.transport.add("POST", "/v1/checkout/sessions", b"", 409)
        with self.assertRaises(StripeError) as error:
            self.client.create_checkout(run_id="r", job_id="j", action_id="a", amount_micro_usd=10000)
        self.assertTrue(error.exception.uncertain)

    def test_host_batch_budget_is_shared_across_nested_provider_reads(self):
        with self.client.operation_budget(seconds=5,requests=2):
            self.assertEqual(self.inspect()["status"],"settled")
            with self.assertRaises(StripeError):
                self.inspect()
        self.assertEqual(len(self.transport.calls),2)

    def test_credential_mode_conflicts_fail_before_any_checkout_post(self):
        for mode,key in (("test","sk_live_OFFLINE_CONTRADICTION"),
                         ("test","rk_live_OFFLINE_CONTRADICTION"),
                         ("live","sk_test_OFFLINE_CONTRADICTION"),
                         ("live","rk_test_OFFLINE_CONTRADICTION")):
            with self.subTest(mode=mode,key_prefix=key[:8]),self.assertRaises(ValueError):
                client=StripeClient(api_key=key,webhook_secret="whsec_OFFLINE_FIXTURE",mode=mode,
                    success_url="https://merchant.example.invalid/complete",cancel_url="https://merchant.example.invalid/cancel",
                    transport=self.transport)
                client.create_checkout(run_id="r",job_id="j",action_id="a",amount_micro_usd=2000000)
        self.assertEqual(self.transport.calls,[])

    def test_real_transport_requires_mode_pinned_key_but_fixture_transport_is_explicit(self):
        kwargs={"api_key":"FICTIONAL_OFFLINE_KEY","webhook_secret":"whsec_OFFLINE_FIXTURE","mode":"test",
                "success_url":"https://merchant.example.invalid/complete","cancel_url":"https://merchant.example.invalid/cancel"}
        with self.assertRaises(ValueError):
            StripeClient(**kwargs)
        StripeClient(**kwargs,transport=self.transport)
        for mode,key in (("test","rk_test_OFFLINE_FIXTURE"),("live","sk_live_OFFLINE_FIXTURE")):
            StripeClient(**{**kwargs,"api_key":key,"mode":mode},transport=self.transport)
        self.assertEqual(self.transport.calls,[])


if __name__ == "__main__":
    unittest.main()
