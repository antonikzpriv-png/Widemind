"""Explicit fixture-provider tests. No network, real accounts, money, or customer."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest

from widemind.business.contracts import Outcome
from widemind.economy import EconomyService


class FixtureStripe:
    mode, account_id = "test", None

    def __init__(self):
        self.creates = 0
        self.lost_create_response = False
        self.sessions = {}
        self.current = {"mode":"test", "run_id":"run", "job_id":"job", "payment_intent_id":"pi_fixture", "status":"pending",
                        "receipts_micro_usd":0, "refunds_micro_usd":0, "fees_micro_usd":0,
                        "provider_reference":"txn_fixture", "provider_native":{"currency":"usd"}}

    def create_checkout(self, **kwargs):
        self.creates += 1
        result = {"id":"cs_fixture", "url":"https://checkout.example.invalid/test"}
        self.sessions[kwargs["action_id"]] = result
        if self.lost_create_response:
            raise TimeoutError("explicit fixture lost reply")
        return result

    def reconcile_checkout(self, *, action_id, session_id=None):
        return Outcome("succeeded",self.sessions[action_id]) if action_id in self.sessions else Outcome("unknown",reason="no fixture evidence")

    def inspect_checkout(self, *, session_id):
        return dict(self.current)

    def verify_event(self, raw_body, signature):
        if signature != "fixture-verified":
            raise ValueError("unverified fixture event")
        return json.loads(raw_body)

    def session_ids_for_event(self, event):
        return ["cs_fixture"]


class EconomyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)/"protected.sqlite3"
        self.stripe = FixtureStripe()
        self.service = EconomyService(self.path,mode="test",stripe=self.stripe)
        self.service.create_run("run",evidence_reference="explicit fixture funding")

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def reserve(self, action="reservation", maximum=1_000_000):
        outcome = self.service.reserve(run_id="run",job_id="job",action_id=action,maximum_micro_usd=maximum)
        self.assertEqual(outcome.state,"succeeded")
        return outcome.data["reservation_id"]

    def link(self):
        return self.service.payment_link(run_id="run",job_id="job",action_id="link",amount_micro_usd=2_000_000,currency="USD")

    def paid(self, **changes):
        self.link()
        self.stripe.current.update(status="settled",receipts_micro_usd=2_000_000,fees_micro_usd=100_000)
        self.stripe.current.update(changes)
        return self.service.payment(run_id="run",job_id="job")

    def test_funding_is_explicit_and_never_repeats_on_reopen(self):
        self.service.create_run("run",evidence_reference="same fixture grant")
        self.assertEqual(self.service.balance(run_id="run")["opening_micro_usd"],5_000_000)
        other=EconomyService(self.path,mode="test")
        try:
            self.assertEqual(other.balance(run_id="run")["receipts_micro_usd"],0)
            with self.assertRaises(ValueError):
                other.balance(run_id="unfunded")
            with self.assertRaises(ValueError):
                other.create_run("run",opening_micro_usd=6_000_000,evidence_reference="cannot rewrite opening")
        finally:
            other.close()

    def test_modes_are_pinned_and_test_payments_cannot_become_live(self):
        with self.assertRaises(ValueError):
            EconomyService(self.path,mode="live")
        with self.assertRaises(ValueError):
            EconomyService(Path(self.temp.name)/"live.sqlite3",mode="live",stripe=self.stripe)

    def test_concurrent_reservations_cannot_spend_same_credit(self):
        barrier=threading.Barrier(2)
        def worker(index):
            service=EconomyService(self.path,mode="test")
            try:
                barrier.wait()
                return service.reserve(run_id="run",job_id=f"job-{index}",action_id=f"reserve-{index}",maximum_micro_usd=4_000_000).state
            finally:
                service.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes=list(pool.map(worker,range(2)))
        self.assertEqual(sorted(outcomes),["failed","succeeded"])
        self.assertEqual(self.service.balance(run_id="run")["spendable_micro_usd"],1_000_000)

    def test_identity_conflicts_and_fractional_money_are_rejected(self):
        self.reserve()
        with self.assertRaises(ValueError):
            self.service.reserve(run_id="run",job_id="job",action_id="reservation",maximum_micro_usd=2)
        for amount in (True,1.1,-1):
            with self.subTest(amount=amount),self.assertRaises(ValueError):
                self.service.reserve(run_id="run",job_id="job",action_id="bad",maximum_micro_usd=amount)

    def test_reservation_claimed_by_only_one_execution(self):
        reservation=self.reserve()
        first=self.service.begin_execution(run_id="run",job_id="job",reservation_id=reservation,action_id="call",maximum_micro_usd=1_000_000)
        self.assertEqual(first.state,"succeeded")
        self.assertEqual(self.service.begin_execution(run_id="run",job_id="job",reservation_id=reservation,action_id="call",maximum_micro_usd=1_000_000).state,"succeeded")
        self.assertEqual(self.service.begin_execution(run_id="run",job_id="job",reservation_id=reservation,action_id="other",maximum_micro_usd=1).state,"failed")
        self.assertEqual(self.service.begin_execution(run_id="run",job_id="job",reservation_id=reservation,action_id="call",maximum_micro_usd=1_000_001).state,"failed")

    def test_billable_failure_is_charged_and_duplicate_settlement_is_idempotent(self):
        reservation=self.reserve()
        self.service.begin_execution(run_id="run",job_id="job",reservation_id=reservation,action_id="failed-paid-call",maximum_micro_usd=1_000_000)
        kwargs=dict(reservation_id=reservation,action_id="failed-paid-call",actual_micro_usd=600_000,evidence_reference="provider error usage")
        self.service.settle_execution(**kwargs)
        self.service.settle_execution(**kwargs)
        balance=self.service.balance(run_id="run")
        self.assertEqual(balance["operating_cost_micro_usd"],600_000)
        self.assertEqual(balance["spendable_micro_usd"],4_400_000)
        self.assertEqual(self.service.begin_execution(run_id="run",job_id="job",reservation_id=reservation,action_id="failed-paid-call",maximum_micro_usd=1).state,"failed")
        self.assertEqual(self.service.payment(run_id="run",job_id="job")["operating_cost_micro_usd"],600_000)

    def test_unknown_execution_retains_liability_across_restart(self):
        reservation=self.reserve()
        self.service.begin_execution(run_id="run",job_id="job",reservation_id=reservation,action_id="unknown",maximum_micro_usd=1_000_000)
        self.service.close()
        self.service=EconomyService(self.path,mode="test",stripe=self.stripe)
        self.assertEqual(self.service.balance(run_id="run")["reserved_micro_usd"],1_000_000)
        release=self.service.release(run_id="run",job_id="job",action_id="release",reservation_id=reservation)
        self.assertEqual(release.state,"unknown")
        self.assertFalse(self.service.payment(run_id="run",job_id="job")["costs_final"])
        self.service.settle_execution(reservation_id=reservation,action_id="unknown",actual_micro_usd=500_000,evidence_reference="provider reconciled")
        self.assertEqual(self.service.reconcile(action_id="release").state,"succeeded")

    def test_provisional_cost_posts_minimum_and_preserves_remaining_reservation(self):
        reservation=self.reserve()
        self.service.begin_execution(run_id="run",job_id="job",reservation_id=reservation,action_id="provisional",maximum_micro_usd=1_000_000)
        self.service.settle_execution(reservation_id=reservation,action_id="provisional",actual_micro_usd=300_000,evidence_reference="known partial usage",provisional=True)
        balance=self.service.balance(run_id="run")
        self.assertEqual(balance["reserved_micro_usd"],700_000)
        self.assertEqual(balance["spendable_micro_usd"],4_000_000)
        self.assertFalse(balance["costs_final"])
        self.service.settle_execution(reservation_id=reservation,action_id="provisional",actual_micro_usd=400_000,evidence_reference="final provider usage")
        self.assertEqual(self.service.balance(run_id="run")["operating_cost_micro_usd"],400_000)

    def test_overlimit_charge_remains_visible_and_stops_further_spending(self):
        reservation=self.reserve(maximum=100)
        self.service.begin_execution(run_id="run",job_id="job",reservation_id=reservation,action_id="over",maximum_micro_usd=100)
        self.service.settle_execution(reservation_id=reservation,action_id="over",actual_micro_usd=6_000_000,evidence_reference="unexpected actual liability")
        balance=self.service.balance(run_id="run")
        self.assertEqual(balance["posted_micro_usd"],-1_000_000)
        self.assertTrue(balance["breached"])
        self.assertEqual(self.service.reserve(run_id="run",job_id="job",action_id="blocked",maximum_micro_usd=1).state,"failed")

    def test_pending_receipts_and_invoice_claims_do_not_create_credit(self):
        self.link()
        snapshot=self.service.payment(run_id="run",job_id="job")
        self.assertEqual(snapshot["status"],"pending")
        self.assertEqual(snapshot["receipts_micro_usd"],0)
        self.assertEqual(self.service.balance(run_id="run")["posted_micro_usd"],5_000_000)

    def test_customer_receipts_held_until_confirmed_fulfillment_and_distinct_from_earned(self):
        snapshot=self.paid()
        balance=self.service.balance(run_id="run")
        self.assertEqual(snapshot["earned_micro_usd"],0)
        self.assertEqual(balance["posted_micro_usd"],6_900_000)
        self.assertEqual(balance["holds_micro_usd"],2_000_000)
        self.assertEqual(balance["spendable_micro_usd"],4_900_000)
        self.service.record_fulfillment(run_id="run",job_id="job",action_id="fulfilled",delivery_reference="confirmed private delivery")
        self.assertEqual(self.service.payment(run_id="run",job_id="job")["earned_micro_usd"],2_000_000)
        self.assertEqual(self.service.balance(run_id="run")["holds_micro_usd"],0)

    def test_partial_full_refund_and_out_of_order_notifications_query_current_truth(self):
        self.paid()
        self.service.record_fulfillment(run_id="run",job_id="job",action_id="fulfilled",delivery_reference="confirmed")
        self.stripe.current.update(status="partial",refunds_micro_usd=500_000)
        for event_id in ("new-refund","old-payment-event","new-refund"):
            body=json.dumps({"id":event_id,"type":"charge.refunded"}).encode()
            self.assertEqual(self.service.handle_webhook(body,"fixture-verified"),["job"])
        self.assertEqual(self.service.balance(run_id="run")["refunds_micro_usd"],500_000)
        self.assertEqual(self.service.payment(run_id="run",job_id="job")["earned_micro_usd"],1_500_000)
        self.stripe.current.update(status="refunded",refunds_micro_usd=2_000_000)
        self.assertEqual(self.service.payment(run_id="run",job_id="job")["earned_micro_usd"],0)
        self.assertEqual(self.service.balance(run_id="run")["posted_micro_usd"],4_900_000)

    def test_active_disputes_and_pending_refunds_hold_even_fulfilled_cash(self):
        self.paid()
        self.service.record_fulfillment(run_id="run",job_id="job",action_id="fulfilled",delivery_reference="confirmed")
        self.stripe.current.update(status="disputed",disputed_micro_usd=1_000_000,pending_refunds_micro_usd=200_000)
        snapshot=self.service.payment(run_id="run",job_id="job")
        self.assertEqual(snapshot["earned_micro_usd"],0)
        self.assertEqual(self.service.balance(run_id="run")["holds_micro_usd"],1_200_000)
        self.stripe.current.update(status="refunded",disputed_micro_usd=0,pending_refunds_micro_usd=0,refunds_micro_usd=2_200_000)
        self.service.payment(run_id="run",job_id="job")
        self.assertEqual(self.service.balance(run_id="run")["refunds_micro_usd"],2_200_000)

    def test_lost_checkout_response_is_reconciled_without_second_creation(self):
        self.stripe.lost_create_response=True
        self.assertEqual(self.link().state,"unknown")
        self.assertEqual(self.link().state,"succeeded")
        self.assertEqual(self.stripe.creates,1)
        self.assertEqual(self.service.reconcile(action_id="link").state,"succeeded")

    def test_unverified_or_conflicting_webhook_cannot_post_money(self):
        self.link()
        body=json.dumps({"id":"event","type":"checkout.session.completed"}).encode()
        with self.assertRaises(ValueError):
            self.service.handle_webhook(body,"wrong")
        self.assertEqual(self.service.balance(run_id="run")["receipts_micro_usd"],0)
        self.service.handle_webhook(body,"fixture-verified")
        with self.assertRaises(ValueError):
            self.service.handle_webhook(json.dumps({"id":"event","type":"other"}).encode(),"fixture-verified")

    def test_append_only_corrections_preserve_original_charge(self):
        reservation=self.reserve()
        self.service.begin_execution(run_id="run",job_id="job",reservation_id=reservation,action_id="call",maximum_micro_usd=1_000_000)
        self.service.settle_execution(reservation_id=reservation,action_id="call",actual_micro_usd=600_000,evidence_reference="provider bill")
        charge=self.service.one("SELECT * FROM ledger WHERE kind='operating'")
        with self.assertRaises(sqlite3.IntegrityError):
            self.service.db.execute("UPDATE ledger SET delta_micro_usd=0 WHERE id=?",(charge["id"],))
        with self.assertRaises(sqlite3.IntegrityError):
            self.service.db.execute("DELETE FROM ledger WHERE id=?",(charge["id"],))
        kwargs=dict(run_id="run",entry_id=charge["id"],delta_micro_usd=100_000,external_event_id="invoice correction",evidence_reference="verified credit note")
        self.service.correct_operating_charge(**kwargs)
        self.service.correct_operating_charge(**kwargs)
        self.assertEqual(self.service.balance(run_id="run")["operating_cost_micro_usd"],500_000)
        self.assertEqual(self.service.one("SELECT delta_micro_usd FROM ledger WHERE id=?",(charge["id"],))["delta_micro_usd"],-600_000)

    def test_owner_topups_and_subsidies_never_count_as_customer_revenue(self):
        kwargs=dict(run_id="run",amount_micro_usd=1_000_000,external_event_id="owner-1",evidence_reference="owner transfer")
        self.service.owner_funding(**kwargs)
        self.service.owner_funding(**kwargs)
        self.service.owner_funding(**{**kwargs,"external_event_id":"subsidy-1","subsidy":True})
        balance=self.service.balance(run_id="run")
        self.assertEqual(balance["owner_funding_micro_usd"],1_000_000)
        self.assertEqual(balance["subsidies_micro_usd"],1_000_000)
        self.assertEqual(balance["receipts_micro_usd"],0)
        self.assertEqual(balance["earned_micro_usd"],0)

    def test_payment_revisions_change_when_reservations_change_cost_finality(self):
        before=self.service.payment(run_id="run",job_id="job")
        reservation=self.reserve()
        held=self.service.payment(run_id="run",job_id="job")
        self.assertGreater(held["revision"],before["revision"])
        self.assertFalse(held["costs_final"])
        self.service.release(run_id="run",job_id="job",action_id="release",reservation_id=reservation)
        released=self.service.payment(run_id="run",job_id="job")
        self.assertGreater(released["revision"],held["revision"])
        self.assertTrue(released["costs_final"])

    def test_stale_concurrent_provider_result_cannot_erase_newer_refund(self):
        self.paid()
        original_inspect=self.stripe.inspect_checkout
        old=dict(self.stripe.current)
        def race(**kwargs):
            self.stripe.inspect_checkout=original_inspect
            self.stripe.current.update(status="refunded",refunds_micro_usd=2_000_000)
            self.service.refresh_payment(run_id="run",job_id="job")
            return old
        self.stripe.inspect_checkout=race
        with self.assertRaises(ValueError):
            self.service.refresh_payment(run_id="run",job_id="job")
        self.assertEqual(self.service.balance(run_id="run")["refunds_micro_usd"],2_000_000)

    def test_reservation_cannot_be_swapped_between_runs_or_jobs(self):
        reservation=self.reserve()
        self.service.create_run("other-run",evidence_reference="separate explicit fixture grant")
        for run_id,job_id in (("other-run","job"),("run","other-job")):
            with self.subTest(run_id=run_id,job_id=job_id):
                result=self.service.begin_execution(run_id=run_id,job_id=job_id,reservation_id=reservation,
                                                    action_id="swapped",maximum_micro_usd=1)
                self.assertEqual(result.state,"failed")
        self.assertIsNone(self.service.one("SELECT execution_action_id FROM reservations WHERE id=?",(reservation,))["execution_action_id"])
        self.assertEqual(self.service.balance(run_id="other-run")["operating_cost_micro_usd"],0)

    def test_execution_ceiling_is_frozen_at_first_authorization(self):
        reservation=self.reserve()
        self.service.begin_execution(run_id="run",job_id="job",reservation_id=reservation,action_id="call",maximum_micro_usd=100)
        result=self.service.begin_execution(run_id="run",job_id="job",reservation_id=reservation,action_id="call",maximum_micro_usd=1000)
        self.assertEqual(result.state,"failed")
        self.service.settle_execution(reservation_id=reservation,action_id="call",actual_micro_usd=101,evidence_reference="observed lower-ceiling overrun")
        self.assertTrue(self.service.balance(run_id="run")["breached"])

    def test_refund_liability_blocks_reserved_but_not_dispatched_work(self):
        self.paid()
        reservation=self.reserve(maximum=4_000_000)
        self.stripe.current.update(status="refunded",refunds_micro_usd=7_000_000)
        self.service.payment(run_id="run",job_id="job")
        self.assertLess(self.service.balance(run_id="run")["spendable_micro_usd"],0)
        result=self.service.begin_execution(run_id="run",job_id="job",reservation_id=reservation,
                                            action_id="must-not-dispatch",maximum_micro_usd=4_000_000)
        self.assertEqual(result.state,"failed")

    def test_stale_unknown_checkout_result_cannot_overwrite_confirmed_success(self):
        self.stripe.lost_create_response=True
        self.assertEqual(self.link().state,"unknown")
        self.assertEqual(self.service.reconcile(action_id="link").state,"succeeded")
        late=self.service._save_link("link",Outcome("unknown",reason="stale in-flight timeout"))
        self.assertEqual(late.state,"succeeded")
        self.assertEqual(self.service.reconcile(action_id="link").state,"succeeded")

    def test_provider_financial_mode_mismatch_cannot_credit_cash(self):
        self.link()
        self.stripe.current.update(mode="live",status="settled",receipts_micro_usd=2_000_000)
        with self.assertRaises(ValueError):
            self.service.payment(run_id="run",job_id="job")
        self.assertEqual(self.service.balance(run_id="run")["receipts_micro_usd"],0)


if __name__ == "__main__":
    unittest.main()
