"""Offline accounting regressions; these transports never contact a provider."""
import copy
from pathlib import Path
import tempfile
import threading
import unittest

from widemind.economy import EconomyService
from widemind.economy.stripe import StripeClient, StripeError
from test_economy import FixtureStripe
from test_economy_stripe import StripeTransportFixture, session_fixture


class EconomyAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "economy.sqlite3"
        self.stripe = FixtureStripe()
        self.service = EconomyService(self.path, mode="test", stripe=self.stripe)
        self.service.create_run("run", evidence_reference="explicit offline fixture")

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def link(self, action_id="link"):
        return self.service.payment_link(run_id="run", job_id="job", action_id=action_id,
            amount_micro_usd=2_000_000, currency="USD")

    def test_payment_revision_fences_an_aba_provider_read(self):
        self.link()
        self.stripe.current.update(status="settled", receipts_micro_usd=2_000_000,
                                   fees_micro_usd=100_000, pending_refunds_micro_usd=0)
        self.service.refresh_payment(run_id="run", job_id="job")
        self.service.record_fulfillment(run_id="run", job_id="job", action_id="delivery",
                                       delivery_reference="offline recipient receipt")
        current = copy.deepcopy(self.stripe.current)
        stale = {**current, "status": "partial", "pending_refunds_micro_usd": 2_000_000}
        read_started, resume = threading.Event(), threading.Event()
        failures = []

        def delayed_refresh():
            delayed = FixtureStripe()
            def inspect(**kwargs):
                read_started.set()
                if not resume.wait(5):
                    raise AssertionError("fixture synchronization timed out")
                return stale
            delayed.inspect_checkout = inspect
            service = EconomyService(self.path, mode="test", stripe=delayed)
            try:
                service.refresh_payment(run_id="run", job_id="job")
            except ValueError as exc:
                failures.append(str(exc))
            finally:
                service.close()

        worker = threading.Thread(target=delayed_refresh)
        worker.start()
        try:
            self.assertTrue(read_started.wait(5))
            self.stripe.current = stale
            self.service.refresh_payment(run_id="run", job_id="job")
            self.stripe.current = current
            self.service.refresh_payment(run_id="run", job_id="job")
        finally:
            resume.set()
            worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(failures), 1)
        balance = self.service.balance(run_id="run")
        self.assertEqual(balance["holds_micro_usd"], 0)
        self.assertEqual(balance["earned_micro_usd"], 2_000_000)

    def test_distinct_link_action_preserves_original_cash_and_refund_route(self):
        def create(**kwargs):
            self.stripe.creates += 1
            return {"id": "cs_" + kwargs["action_id"],
                    "url": "https://checkout.example.invalid/" + kwargs["action_id"]}
        self.stripe.create_checkout = create
        original = self.link("first")
        self.stripe.current.update(status="settled", receipts_micro_usd=2_000_000,
                                   fees_micro_usd=100_000)
        self.service.refresh_payment(run_id="run", job_id="job")
        self.assertEqual(self.link("second"), original)
        self.assertEqual(self.stripe.creates, 1)
        self.assertEqual(self.service.reconcile(action_id="second"), original)
        self.assertEqual(self.service.one("SELECT session_id FROM economy_jobs WHERE job_id='job'")["session_id"], "cs_first")
        self.stripe.current.update(status="refunded", refunds_micro_usd=2_000_000)
        self.stripe.session_ids_for_event = lambda event: ["cs_first"]
        jobs = self.service.handle_webhook(b'{"id":"evt_refund"}', "fixture-verified")
        self.assertEqual(jobs, ["job"])
        self.assertEqual(self.service.balance(run_id="run")["refunds_micro_usd"], 2_000_000)

    def test_concurrent_distinct_link_actions_never_dispatch_second_checkout(self):
        dispatched, resume = threading.Event(), threading.Event()
        outcomes = []
        def create(**kwargs):
            self.stripe.creates += 1
            dispatched.set()
            if not resume.wait(5):
                raise AssertionError("fixture synchronization timed out")
            return {"id": "cs_first", "url": "https://checkout.example.invalid/first"}
        self.stripe.create_checkout = create
        def first_link():
            service = EconomyService(self.path, mode="test", stripe=self.stripe)
            try:
                outcomes.append(service.payment_link(run_id="run", job_id="job", action_id="first",
                    amount_micro_usd=2_000_000, currency="USD"))
            finally:
                service.close()
        worker = threading.Thread(target=first_link)
        worker.start()
        try:
            self.assertTrue(dispatched.wait(5))
            second = self.link("second")
            self.assertEqual(second.state, "failed")
            self.assertEqual(self.stripe.creates, 1)
            self.assertEqual(self.service.reconcile(action_id="second"), second)
        finally:
            resume.set()
            worker.join(5)
        self.assertEqual(outcomes[0].state, "succeeded")

    def test_new_link_attempt_after_definitive_rejection_can_dispatch(self):
        def reject(**kwargs):
            raise StripeError("offline rejection", status=400)
        self.stripe.create_checkout = reject
        self.assertEqual(self.link("first").state, "failed")
        self.stripe.create_checkout = lambda **kwargs: {
            "id": "cs_retry", "url": "https://checkout.example.invalid/retry"}
        self.assertEqual(self.link("second").state, "succeeded")


class StripeDisputeCashTests(unittest.TestCase):
    def setUp(self):
        self.transport = StripeTransportFixture()
        self.session = session_fixture()
        self.transport.add("GET", "/v1/checkout/sessions/cs_test_fixture", self.session)
        self.client = StripeClient(api_key="sk_test_OFFLINE_FIXTURE", webhook_secret="whsec_OFFLINE_FIXTURE",
            mode="test", success_url="https://merchant.example.invalid/complete",
            cancel_url="https://merchant.example.invalid/cancel", transport=self.transport)

    def inspect(self, *, status="under_review", balances=()):
        dispute = {"id": "dp_fixture", "object": "dispute", "currency": "usd", "charge": "ch_fixture",
                   "amount": 200, "status": status, "balance_transactions": list(balances)}
        self.transport.add("GET", "/v1/disputes", {"object": "list", "data": [dispute], "has_more": False})
        return self.client.inspect_checkout(session_id="cs_test_fixture")

    @staticmethod
    def debit():
        return {"id": "txn_dispute", "object": "balance_transaction", "currency": "usd",
                "status": "available", "amount": -200, "fee": 1500, "net": -1700}

    def test_active_dispute_posts_verified_cash_debit_without_an_extra_hold(self):
        canonical = self.inspect(balances=[self.debit()])
        self.assertEqual(canonical["status"], "disputed")
        self.assertEqual(canonical["refunds_micro_usd"], 2_000_000)
        self.assertEqual(canonical["disputed_micro_usd"], 0)
        with tempfile.TemporaryDirectory() as directory:
            service = EconomyService(Path(directory) / "economy.sqlite3", mode="test", stripe=self.client)
            try:
                service.create_run("run-fixture", evidence_reference="offline fixture")
                with service.transaction():
                    job = service._job("run-fixture", "job-fixture", price=2_000_000)
                    service._apply_payment(job, canonical)
                service.record_fulfillment(run_id="run-fixture", job_id="job-fixture", action_id="delivery",
                                           delivery_reference="offline recipient receipt")
                balance = service.balance(run_id="run-fixture")
                self.assertEqual(balance["posted_micro_usd"], -10_360_000)
                self.assertEqual(balance["holds_micro_usd"], 0)
                self.assertEqual(balance["spendable_micro_usd"], -10_360_000)
                self.assertEqual(balance["earned_micro_usd"], 0)
            finally:
                service.close()

    def test_won_dispute_reverses_the_actual_principal_debit(self):
        reversal = {"id": "txn_return", "object": "balance_transaction", "currency": "usd",
                    "status": "available", "amount": 200, "fee": -1500, "net": 1700}
        canonical = self.inspect(status="won", balances=[self.debit(), reversal])
        self.assertEqual(canonical["status"], "settled")
        self.assertEqual(canonical["refunds_micro_usd"], 0)
        self.assertEqual(canonical["fees_micro_usd"], 360_000)

    def test_partial_dispute_debit_holds_only_remaining_exposure(self):
        debit = {**self.debit(), "amount": -75, "fee": 0, "net": -75}
        canonical = self.inspect(balances=[debit])
        self.assertEqual(canonical["status"], "disputed")
        self.assertEqual(canonical["refunds_micro_usd"], 750_000)
        self.assertEqual(canonical["disputed_micro_usd"], 1_250_000)

    def test_dispute_cash_recovery_is_an_append_only_compensating_entry(self):
        with tempfile.TemporaryDirectory() as directory:
            service = EconomyService(Path(directory) / "economy.sqlite3", mode="test", stripe=self.client)
            try:
                service.create_run("run-fixture", evidence_reference="offline fixture")
                for canonical in (self.inspect(balances=[self.debit()]),
                                  self.inspect(status="won", balances=[self.debit(),
                                      {"id": "txn_return", "currency": "usd", "amount": 200, "fee": -1500}])):
                    with service.transaction():
                        job = service._job("run-fixture", "job-fixture", price=2_000_000)
                        service._apply_payment(job, canonical)
                refunds = service.all("SELECT delta_micro_usd FROM ledger WHERE kind='refund' ORDER BY id")
                self.assertEqual([row["delta_micro_usd"] for row in refunds], [-2_000_000, 2_000_000])
                self.assertEqual(service.balance(run_id="run-fixture")["posted_micro_usd"], 6_640_000)
            finally:
                service.close()

    def test_warning_without_a_cash_debit_retains_exposure(self):
        canonical = self.inspect(status="warning_under_review")
        self.assertEqual(canonical["status"], "disputed")
        self.assertEqual(canonical["refunds_micro_usd"], 0)
        self.assertEqual(canonical["disputed_micro_usd"], 2_000_000)

    def test_dispute_balance_without_principal_amount_fails_closed(self):
        incomplete = self.debit()
        incomplete.pop("amount")
        with self.assertRaises(StripeError):
            self.inspect(balances=[incomplete])


if __name__ == "__main__":
    unittest.main()
