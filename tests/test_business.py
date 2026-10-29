import io
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from widemind.business.api import API
from widemind.business.contracts import Outcome, Plan
from widemind.business.engine import Business
from widemind.business.policy import Policy
from widemind.business.services import ResearchBrief
from widemind.business.store import Store, encode
from widemind.demo import DemoAdapters, demonstrate, drain
from widemind.worker import worker_lock


class BusinessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.now = 1800000000.0
        self.adapters = DemoAdapters(self.directory / "fixtures.sqlite3", mode="test")
        self.open()
        self.scope = {"topic": "Private customer SQLite decision", "sections": ["Overview", "Risks"], "min_sources": 2}
        self.sequence = 0

    def open(self):
        self.store = Store(self.directory / "business.sqlite3")
        self.business = Business(self.store, run_id="test-run", economy=self.adapters, tools=self.adapters,
                                 runtime=self.adapters, clock=lambda: self.now, mode="test")

    def tearDown(self):
        self.store.close()
        self.adapters.close()
        self.temp.cleanup()

    def request(self, key="request-1", **changes):
        fields = dict(source="test", external_id=key, service="research-brief", scope=self.scope,
                      customer_email="private@example.invalid")
        fields.update(changes)
        return self.business.receive(**fields)

    def offer(self):
        self.request()
        drain(self.business)
        return self.store.one("SELECT * FROM quotes")

    def order(self):
        quote = self.offer()
        job = self.business.accept(quote["id"], customer_email="private@example.invalid", evidence_reference="test-accept")
        drain(self.business)
        self.assertEqual(self.business.order(job)["status"], "awaiting_payment")
        return job

    def wake(self, job, kind="advance"):
        self.sequence += 1
        self.business.notify(source="test", external_id=f"wake-{self.sequence}", kind=kind, payload={"id": job})

    def paid(self, job):
        self.adapters.set_payment(job)
        self.wake(job, "payment.changed")

    def until(self, job, status):
        for _ in range(30):
            if self.business.order(job)["status"] == status:
                return
            if not self.business.tick():
                break
        self.fail(f"did not reach {status}: {self.business.diagnostics()}")

    def count(self, action):
        return sum(a == action for _, a in self.adapters.calls)

    def test_full_flow_keeps_receipts_separate_from_earnings_and_obligations(self):
        job = self.order()
        self.assertIsNone(self.business.order(job)["delivery_due_at"])
        self.paid(job)
        self.until(job, "ready")
        before = self.business.order(job)
        payment = json.loads(before["payment"])
        self.assertEqual(payment["receipts_micro_usd"], 2_000_000)
        self.assertEqual(payment["earned_micro_usd"], 0)
        self.assertGreater(before["delivery_due_at"], self.now)
        self.assertEqual(self.count("research.collect"), 1)
        drain(self.business)
        after = self.business.order(job)
        self.assertEqual(after["status"], "completed")
        self.assertEqual(json.loads(after["payment"])["earned_micro_usd"], 2_000_000)
        self.assertEqual(self.count("gmail.send"), 4)  # offer, payment, commitment, delivery
        artifact = json.loads(after["artifact"])
        self.assertIn("## Risks", artifact["content"])
        self.assertTrue(artifact["checks"]["citations"])
        evaluation = self.store.one("SELECT document FROM memory WHERE kind LIKE 'evaluation:%'")
        self.assertEqual(json.loads(evaluation["document"])["measured_contribution_micro_usd"], 1_875_000)
        public = encode(self.business.public_activity())
        self.assertNotIn("private", public)
        self.assertNotIn("SQLite", public)
        self.assertNotIn(job, public)

    def test_insufficient_funds_block_then_explicit_bounded_retry(self):
        job = self.order()
        self.adapters.set("funds", 0)
        self.paid(job)
        drain(self.business)
        self.assertEqual(self.business.order(job)["status"], "awaiting_payment")
        self.assertEqual(self.count("research.collect"), 0)
        self.assertIsNone(self.business.order(job)["delivery_due_at"])
        failed = self.store.one("SELECT * FROM effects WHERE action='reserve'")
        self.assertEqual(failed["state"], "failed")
        self.adapters.set("funds", 500000)
        self.business.retry_effect(failed["id"], evidence_reference="new funding verified by economy")
        self.wake(job)
        drain(self.business)
        self.assertEqual(self.business.order(job)["status"], "completed")
        self.assertEqual(self.count("reserve"), 2)
        self.assertEqual(self.store.one("SELECT COUNT(*) AS n FROM effects WHERE action='reserve'")["n"], 2)

    def test_unavailable_tool_has_no_offer_or_spend(self):
        self.adapters.disabled.add("research.collect")
        opportunity = self.request()
        drain(self.business)
        self.assertEqual(self.store.one("SELECT status FROM opportunities WHERE id=?", (opportunity,))["status"], "needs_review")
        self.assertEqual(self.adapters.calls, [])
        self.adapters.disabled.clear()
        self.business.reconsider(opportunity, evidence_reference="tool configured")
        drain(self.business)
        self.assertEqual(self.count("gmail.send"), 1)

    def test_tool_disappears_after_payment(self):
        job = self.order()
        self.adapters.disabled.add("research.collect")
        self.paid(job)
        drain(self.business)
        self.assertEqual(self.count("reserve"), 0)
        self.assertEqual(self.count("research.collect"), 0)
        self.adapters.disabled.clear()
        self.wake(job)
        drain(self.business)
        self.assertEqual(self.business.order(job)["status"], "completed")

    def test_process_death_after_tool_effect_reconciles_without_reexecution(self):
        job = self.order()
        self.paid(job)
        original = self.adapters.execute
        crashed = []
        def die(**kwargs):
            result = original(**kwargs)
            if kwargs["action"] == "research.collect" and not crashed:
                crashed.append(True)
                raise KeyboardInterrupt("simulate process death")
            return result
        self.adapters.execute = die
        with self.assertRaises(KeyboardInterrupt):
            drain(self.business)
        self.assertEqual(self.count("research.collect"), 1)
        self.store.close()
        self.now += 121
        self.open()
        self.adapters.execute = original
        drain(self.business)
        self.assertEqual(self.business.order(job)["status"], "completed")
        self.assertEqual(self.count("research.collect"), 1)
        self.assertEqual(self.count("reserve"), 1)

    def test_duplicates_do_not_duplicate_effects_or_orders(self):
        job = self.order()
        self.request()
        quote = self.store.one("SELECT id FROM quotes")
        self.assertEqual(self.business.accept(quote["id"], customer_email="private@example.invalid", evidence_reference="same acceptance"), job)
        self.paid(job)
        for _ in range(5):
            self.wake(job, "payment.changed")
        drain(self.business)
        self.assertEqual(self.count("research.collect"), 1)
        self.assertEqual(self.count("record_fulfillment"), 1)
        self.assertEqual(len(self.business.public_activity()), 1)
        self.assertEqual(self.store.one("SELECT COUNT(*) AS n FROM orders")["n"], 1)

    def test_event_identity_conflicts_are_rejected(self):
        args = dict(source="test", external_id="unique", kind="gmail.sync", payload={})
        first = self.business.notify(**args)
        self.assertEqual(self.business.notify(**args), first)
        with self.assertRaises(ValueError):
            self.business.notify(**{**args, "payload": {"cursor": "different"}})
        self.request()
        with self.assertRaises(ValueError):
            self.request(scope={"topic": "different"})

    def test_uncertain_delivery_cannot_be_completed_or_resent(self):
        job = self.order()
        self.paid(job)
        self.until(job, "ready")
        self.adapters.delivery_state = "accepted"
        drain(self.business)
        sends = self.count("gmail.send")
        self.assertEqual(self.business.order(job)["status"], "ready")
        self.assertEqual(self.count("record_fulfillment"), 0)
        self.wake(job)
        drain(self.business)
        self.assertEqual(self.count("gmail.send"), sends)
        effect = self.store.one("SELECT * FROM effects WHERE job_id=? AND action='gmail.send' AND request LIKE '%attachment%'", (job,))
        data = json.loads(effect["result"])
        data["delivery_state"] = "confirmed"
        self.adapters.save(effect["id"], Outcome("succeeded", data))
        self.business.notify(source="test", external_id="delivery-confirmed", kind="reconcile", payload={"id": effect["id"]})
        drain(self.business)
        self.wake(job)
        drain(self.business)
        self.assertEqual(self.business.order(job)["status"], "completed")
        self.assertEqual(self.count("gmail.send"), sends)

    def test_timeout_reconciles_offer_no_duplicate_send(self):
        self.adapters.unknown_actions.add("gmail.send")
        opportunity = self.request()
        drain(self.business)
        self.assertEqual(self.count("gmail.send"), 1)
        self.business.notify(source="test", external_id="recover-offer", kind="offer", payload={"id": opportunity})
        drain(self.business)
        self.assertEqual(self.count("gmail.send"), 1)
        self.assertEqual(self.store.one("SELECT status FROM quotes")["status"], "offered")

    def test_refund_before_creation_holds_commitment_and_releases_reservation(self):
        job = self.order()
        self.paid(job)
        self.until(job, "funded")
        self.adapters.set_payment(job, refunds=2_000_000, status="refunded")
        drain(self.business)
        self.assertEqual(self.business.order(job)["status"], "payment_hold")
        self.assertEqual(self.count("research.collect"), 0)
        self.assertEqual(self.count("release"), 1)
        self.assertEqual(json.loads(self.business.order(job)["payment"])["earned_micro_usd"], 0)

    def test_partial_refund_before_delivery_does_not_deliver(self):
        job = self.order()
        self.paid(job)
        self.until(job, "ready")
        sends = self.count("gmail.send")
        self.adapters.set_payment(job, refunds=1, status="partial")
        drain(self.business)
        self.assertEqual(self.business.order(job)["status"], "payment_hold")
        self.assertEqual(self.count("gmail.send"), sends)

    def test_refund_during_uncertain_delivery_preserves_uncertainty(self):
        job = self.order()
        self.paid(job)
        self.until(job, "ready")
        self.adapters.unknown_actions.add("gmail.send")
        drain(self.business)
        self.adapters.set_payment(job, refunds=2_000_000, status="refunded")
        self.wake(job, "payment.changed")
        drain(self.business)
        self.assertEqual(self.business.order(job)["status"], "ready")
        self.assertEqual(self.count("release"), 0)
        effect = self.store.one("SELECT * FROM effects WHERE job_id=? AND state='unknown'", (job,))
        self.business.notify(source="test", external_id="reconcile-refunded-delivery", kind="reconcile", payload={"id": effect["id"]})
        drain(self.business)
        self.wake(job)
        drain(self.business)
        order = self.business.order(job)
        self.assertEqual(order["status"], "completed")
        self.assertEqual(json.loads(order["payment"])["earned_micro_usd"], 0)

    def test_post_completion_refund_updates_economy_view_not_history(self):
        job = self.order()
        self.paid(job)
        drain(self.business)
        self.adapters.set_payment(job, refunds=2_000_000, status="refunded")
        self.wake(job, "payment.changed")
        drain(self.business)
        self.assertEqual(self.business.order(job)["status"], "completed")
        self.assertEqual(json.loads(self.business.order(job)["payment"])["earned_micro_usd"], 0)
        self.assertEqual(len(self.business.public_activity()), 1)
        self.assertEqual(self.count("research.collect"), 1)

    def test_payment_notification_cannot_claim_money(self):
        job = self.order()
        self.business.notify(source="customer", external_id="fake-payment", kind="payment.changed", payload={"id": job, "receipts_micro_usd": 99999999, "status": "settled"})
        drain(self.business)
        self.assertEqual(self.business.order(job)["status"], "awaiting_payment")
        self.assertEqual(self.count("reserve"), 0)

    def test_wrong_payment_mode_and_floats_fail_closed(self):
        for change in ({"mode": "live"}, {"receipts_micro_usd": 2.0}, {"currency": "EUR"}, {"earned_micro_usd": 123}):
            with self.subTest(change=change):
                job = self.order() if not self.store.one("SELECT id FROM orders") else self.store.one("SELECT id FROM orders")["id"]
                self.adapters.set_payment(job)
                snapshot = self.adapters.payment(run_id="test-run", job_id=job)
                snapshot.update(change)
                self.adapters.set(f"payment:{job}", snapshot)
                self.wake(job)
                drain(self.business)
                self.assertEqual(self.count("reserve"), 0)

    def test_pause_preserves_events_and_configuration_change_stops_execution(self):
        job = self.order()
        self.paid(job)
        self.adapters.set("state", "paused")
        drain(self.business)  # Payment reconciliation remains available while paused.
        self.assertEqual(self.business.order(job)["status"], "awaiting_payment")
        self.assertEqual(self.count("reserve"), 0)
        self.adapters.set("state", "active")
        self.until(job, "funded")
        self.business.configure(replace(self.business.policy, capabilities=()))
        drain(self.business)
        self.assertEqual(self.count("research.collect"), 0)
        self.assertEqual(self.business.order(job)["status"], "funded")

    def test_stopped_runtime_allows_reconciliation_only(self):
        self.adapters.unknown_actions.add("gmail.send")
        self.request()
        drain(self.business)
        effect = self.store.one("SELECT * FROM effects WHERE state='unknown'")
        self.adapters.set("state", "stopped")
        self.business.notify(source="test", external_id="stopped-reconcile", kind="reconcile", payload={"id": effect["id"]})
        drain(self.business)
        self.assertEqual(self.store.one("SELECT state FROM effects WHERE id=?", (effect["id"],))["state"], "succeeded")
        self.assertEqual(self.count("gmail.send"), 1)
        with self.assertRaises(ValueError):
            self.business.retry_effect(effect["id"], evidence_reference="unsafe")

    def test_outbound_is_targeted_configured_and_respects_opt_out(self):
        opportunity = self.request(origin="outbound", source_reference="https://example.com/relevant-request")
        drain(self.business)
        self.assertEqual(self.count("gmail.send"), 0)
        self.business.configure(replace(self.business.policy, allow_outbound=True))
        self.business.reconsider(opportunity, evidence_reference="outbound authorized")
        self.business.opt_out("private@example.invalid", "customer request")
        drain(self.business)
        self.assertEqual(self.count("gmail.send"), 0)
        with self.assertRaises(ValueError):
            self.request("bad-target", origin="outbound", source_reference="javascript:send()")

    def test_enabled_targeted_outbound_produces_a_real_offer_operation(self):
        self.business.configure(replace(self.business.policy, allow_outbound=True))
        self.request(origin="outbound", source_reference="https://example.com/relevant-request")
        drain(self.business)
        self.assertEqual(self.count("gmail.send"), 1)
        self.assertEqual(self.store.one("SELECT status FROM quotes")["status"], "offered")

    def test_discovered_opportunity_never_invents_customer(self):
        opportunity = self.request(origin="discovered", customer_email=None, source_reference="https://example.com/opportunity")
        drain(self.business)
        self.assertEqual(self.count("gmail.send"), 0)
        self.assertEqual(self.store.one("SELECT COUNT(*) AS n FROM customers")["n"], 0)
        self.business.configure(replace(self.business.policy, allow_outbound=True))
        self.business.reconsider(opportunity, customer_email="private@example.invalid", evidence_reference="identified actual requester")
        drain(self.business)
        self.assertEqual(self.count("gmail.send"), 1)

    def test_prioritization_respects_experiment_budget(self):
        self.business.configure(replace(self.business.policy, experiment_budget_micro_usd=250000))
        low = self.request("low", priority=1)
        high = self.request("high", priority=99)
        drain(self.business)
        self.assertEqual(self.store.one("SELECT status FROM opportunities WHERE id=?", (high,))["status"], "offered")
        self.assertEqual(self.store.one("SELECT status FROM opportunities WHERE id=?", (low,))["status"], "budget_wait")
        self.assertEqual(self.count("gmail.send"), 1)

    def test_service_registry_supports_other_work_without_rewriting_engine(self):
        class Writing:
            name, capability = "writing", "writing"
            def plan(self, scope, allowance):
                return Plan("writing.create", {"topic": scope["topic"]}, allowance)
            def deliverable(self, scope, result):
                return {"filename": "writing.txt", "media_type": "text/plain", "content": result["text"], "checks": {"scope": True}}
        self.business.services["writing"] = Writing()
        self.business.configure(replace(self.business.policy, capabilities=("writing",), prices={"writing": 1000000}))
        self.request(service="writing")
        drain(self.business)
        quote = self.store.one("SELECT id FROM quotes")
        job = self.business.accept(quote["id"], customer_email="private@example.invalid", evidence_reference="writing acceptance")
        drain(self.business)
        self.paid(job)
        drain(self.business)
        self.assertEqual(self.business.order(job)["status"], "completed")
        self.assertEqual(self.count("writing.create"), 1)
        self.assertEqual(self.count("research.collect"), 0)

    def test_invalid_research_never_becomes_deliverable(self):
        job = self.order()
        original = self.adapters.execute
        def invalid(**kwargs):
            if kwargs["action"] == "research.collect":
                return Outcome("succeeded", {"sources": [], "sections": []})
            return original(**kwargs)
        self.adapters.execute = invalid
        self.paid(job)
        drain(self.business)
        self.assertEqual(self.business.order(job)["status"], "funded")
        self.assertIsNone(self.business.order(job)["artifact"])
        self.assertEqual(self.count("record_fulfillment"), 0)

    def test_deadline_survives_restart_and_records_missed_commitment(self):
        job = self.order()
        self.paid(job)
        self.until(job, "funded")
        self.adapters.disabled.add("research.collect")
        drain(self.business)
        self.store.close()
        self.now += 90000
        self.open()
        drain(self.business)
        memory = self.store.one("SELECT document FROM memory WHERE entity_id=? AND kind='deadline'", (job,))
        self.assertEqual(json.loads(memory["document"])["state"], "delivery_overdue")
        self.assertEqual(self.business.order(job)["status"], "funded")

    def test_quote_expiry_and_wrong_customer(self):
        quote = self.offer()
        with self.assertRaises(ValueError):
            self.business.accept(quote["id"], customer_email="other@example.invalid", evidence_reference="not owner")
        self.now = quote["expires_at"] + 1
        with self.assertRaises(ValueError):
            self.business.accept(quote["id"], customer_email="private@example.invalid", evidence_reference="expired")

    def test_idle_performs_no_provider_calls(self):
        for _ in range(100):
            self.assertFalse(self.business.tick())
        self.assertEqual(self.adapters.calls, [])

    def test_concurrent_startup_migrations_and_worker_exclusion(self):
        path = self.directory / "new.sqlite3"
        def initialize(_):
            store = Store(path)
            count = store.one("SELECT COUNT(*) AS n FROM business_migrations")["n"]
            store.close()
            return count
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(list(pool.map(initialize, range(2))), [5, 5])
        with worker_lock(path):
            with self.assertRaises(OSError):
                with worker_lock(path):
                    self.fail("two workers acquired same database")

    def test_api_authentication_private_delivery_and_public_projection(self):
        def factory():
            return Business(Store(self.directory / "business.sqlite3"), run_id="test-run", economy=self.adapters,
                tools=self.adapters, runtime=self.adapters, clock=lambda: self.now, mode="test")
        api = API(factory, operator_token="a" * 32)
        def call(path, method="GET", body=None, auth=True):
            raw = encode(body).encode() if body is not None else b""
            environ = {"PATH_INFO": path, "REQUEST_METHOD": method, "CONTENT_TYPE": "application/json",
                "CONTENT_LENGTH": str(len(raw)), "wsgi.input": io.BytesIO(raw)}
            if auth:
                environ["HTTP_AUTHORIZATION"] = "Bearer " + "a" * 32
            responses = []
            payload = b"".join(api(environ, lambda status, headers: responses.append((status, headers))))
            return responses[0][0], json.loads(payload)
        job = self.order()
        self.assertEqual(call(f"/api/v1/orders/{job}", auth=False)[0], "401 Unauthorized")
        self.assertEqual(call(f"/api/v1/orders/{job}")[1]["id"], job)
        self.assertEqual(call("/api/v1/activity", auth=False)[0], "200 OK")
        self.assertEqual(call("/api/v1/policy", "POST", {"max_job_micro_usd": 1.2})[0], "400 Bad Request")
        self.assertEqual(call("/api/v1/unknown")[0], "404 Not Found")
        self.assertNotIn("private@example.invalid", encode(call("/api/v1/activity", auth=False)))

    def test_gmail_inbound_accept_and_optout_are_deduplicated_and_sender_checked(self):
        request = {"message_id": "email-1", "thread_id": "thread-1", "from": "private@example.invalid", "sender_verified": True,
                   "command": {"type": "request", "service": "research-brief", "scope": self.scope}}
        self.adapters.messages = [request, {**request, "message_id": "spoof", "sender_verified": False}]
        self.business.notify(source="test", external_id="inbox-1", kind="gmail.sync", payload={})
        drain(self.business)
        quote = self.store.one("SELECT * FROM quotes")
        self.adapters.messages = [request, {**request, "message_id": "accept-1", "command": {"type": "accept", "quote_id": quote["id"]}}]
        self.business.notify(source="test", external_id="inbox-2", kind="gmail.sync", payload={})
        drain(self.business)
        self.assertEqual(self.store.one("SELECT COUNT(*) AS n FROM opportunities")["n"], 1)
        self.assertEqual(self.store.one("SELECT COUNT(*) AS n FROM orders")["n"], 1)
        self.adapters.messages = [{**request, "message_id": "optout-1", "command": {"type": "opt_out"}}]
        self.business.notify(source="test", external_id="inbox-3", kind="gmail.sync", payload={})
        drain(self.business)
        self.assertEqual(self.store.one("SELECT opted_out FROM customers")["opted_out"], 1)

    def test_payment_hold_resume_requires_fresh_reservation_and_preserves_deadline(self):
        job = self.order()
        self.paid(job)
        self.until(job, "funded")
        due = self.business.order(job)["delivery_due_at"]
        self.adapters.set_payment(job, refunds=2_000_000, status="refunded")
        drain(self.business)
        self.assertEqual(self.business.order(job)["status"], "payment_hold")
        self.adapters.set_payment(job, receipts=4_000_000, refunds=2_000_000)
        self.business.resolve_hold(job, decision="resume", evidence_reference="new verified settlement")
        drain(self.business)
        self.assertEqual(self.business.order(job)["status"], "completed")
        self.assertEqual(self.count("reserve"), 2)
        self.assertEqual(self.count("research.collect"), 1)
        self.assertEqual(self.business.order(job)["delivery_due_at"], due)

    def test_cancel_keeps_refund_exposure_and_later_refund_notification(self):
        job = self.order()
        self.paid(job)
        self.until(job, "ready")
        self.adapters.set_payment(job, status="disputed")
        drain(self.business)
        self.business.resolve_hold(job, decision="cancel", evidence_reference="customer cancellation")
        order = self.business.order(job)
        self.assertEqual(order["status"], "cancelled")
        self.assertFalse(order["delivery_obligation_open"])
        self.assertEqual(order["unrefunded_receipts_micro_usd"], 2_000_000)
        self.adapters.set_payment(job, refunds=2_000_000, status="refunded")
        self.wake(job, "payment.changed")
        drain(self.business)
        self.assertEqual(self.business.order(job)["unrefunded_receipts_micro_usd"], 0)

    def test_stale_payment_revision_cannot_erase_a_refund(self):
        job = self.order()
        self.paid(job)
        self.until(job, "funded")
        old = self.adapters.payment(run_id="test-run", job_id=job)
        self.adapters.set_payment(job, refunds=2_000_000, status="refunded")
        drain(self.business)
        self.adapters.set(f"payment:{job}", old)
        self.wake(job, "payment.changed")
        drain(self.business)
        order = self.business.order(job)
        self.assertEqual(json.loads(order["payment"])["status"], "refunded")
        self.assertEqual(order["status"], "payment_hold")
        self.assertEqual(self.count("research.collect"), 0)

    def test_frozen_plan_survives_service_estimator_change(self):
        job = self.order()
        def changed_plan(*args):
            return Plan("wrong-tool", {}, 1)
        self.business.services["research-brief"].plan = changed_plan
        self.paid(job)
        drain(self.business)
        self.assertEqual(self.business.order(job)["status"], "completed")
        self.assertEqual(self.count("research.collect"), 1)

    def test_api_policy_change_is_reloaded_by_existing_worker(self):
        job = self.order()
        self.paid(job)
        other = Business(Store(self.directory / "business.sqlite3"), run_id="test-run", economy=self.adapters,
            tools=self.adapters, runtime=self.adapters, mode="test")
        other.configure(replace(other.policy, capabilities=()))
        other.store.close()
        drain(self.business)
        self.assertEqual(self.count("reserve"), 0)
        self.assertEqual(self.count("research.collect"), 0)

    def test_retry_of_unknown_effect_is_rejected_and_failed_retry_is_bounded(self):
        self.adapters.unknown_actions.add("gmail.send")
        self.request()
        drain(self.business)
        effect = self.store.one("SELECT * FROM effects WHERE state='unknown'")
        with self.assertRaises(ValueError):
            self.business.retry_effect(effect["id"], evidence_reference="cannot retry uncertainty")
        self.business._reconcile(effect["id"])
        # Separate job: confirmed reserve failures can have at most 3 total attempts.
        self.business.notify(source="test", external_id="finish-offer", kind="offer", payload={"id": effect["job_id"]})
        self.adapters.unknown_actions.clear()
        drain(self.business)
        quote = self.store.one("SELECT id FROM quotes")
        job = self.business.accept(quote["id"], customer_email="private@example.invalid", evidence_reference="accept")
        drain(self.business)
        self.adapters.set("funds", 0)
        self.paid(job)
        drain(self.business)
        for attempt in range(2):
            failed = self.store.one("SELECT * FROM effects WHERE action='reserve' ORDER BY attempt DESC LIMIT 1")
            self.business.retry_effect(failed["id"], evidence_reference=f"retry-{attempt}")
            self.wake(job)
            drain(self.business)
        failed = self.store.one("SELECT * FROM effects WHERE action='reserve' ORDER BY attempt DESC LIMIT 1")
        with self.assertRaises(ValueError):
            self.business.retry_effect(failed["id"], evidence_reference="fourth attempt forbidden")
        self.assertEqual(self.count("reserve"), 3)

    def test_research_rejects_unknown_citations_and_missing_sections(self):
        service = ResearchBrief()
        good = {"sources": [{"url": "https://example.com/a", "title": "a", "excerpt": "a"},
                            {"url": "https://example.com/b", "title": "b", "excerpt": "b"}],
                "sections": [{"heading": h, "text": "summary", "source_urls": ["https://example.com/a"]} for h in self.scope["sections"]]}
        self.assertTrue(service.deliverable(self.scope, good)["checks"]["citations"])
        bad = json.loads(encode(good))
        bad["sections"][0]["source_urls"] = ["https://example.com/not-a-source"]
        with self.assertRaises(ValueError):
            service.deliverable(self.scope, bad)
        with self.assertRaises(ValueError):
            service.deliverable(self.scope, {**good, "sections": good["sections"][:1]})

    def test_malformed_email_does_not_block_valid_request_in_same_batch(self):
        self.adapters.messages = [
            {"message_id": "bad", "thread_id": "t", "from": "private@example.invalid", "sender_verified": True,
             "command": {"type": "accept", "quote_id": "does-not-exist"}},
            {"message_id": "good", "thread_id": "t", "from": "private@example.invalid", "sender_verified": True,
             "command": {"type": "request", "service": "research-brief", "scope": self.scope}},
        ]
        self.business.notify(source="test", external_id="mixed-inbox", kind="gmail.sync", payload={})
        drain(self.business)
        self.assertEqual(self.store.one("SELECT status FROM quotes")["status"], "offered")
        self.assertTrue(self.store.one("SELECT 1 FROM memory WHERE kind='needs_clarification'"))

    def test_fixture_adapters_cannot_enter_live_mode(self):
        with self.assertRaises(ValueError):
            Business(self.store, run_id="live-run", economy=self.adapters, tools=self.adapters,
                     runtime=self.adapters, mode="live")

    def test_policy_rejects_ambiguous_types_and_fake_money(self):
        for changes in ({"capabilities": "research"}, {"goals": "earn"}, {"prices": []},
                        {"max_job_micro_usd": True}, {"prices": {"research-brief": 1.1}},
                        {"allow_outbound": "yes"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                Policy(**changes)

    def test_concurrent_snapshot_cannot_overwrite_newer_refund(self):
        job = self.order()
        self.paid(job)
        self.until(job, "funded")
        old_order = self.business.order(job)
        old_payment = self.adapters.payment(run_id="test-run", job_id=job)
        self.adapters.set_payment(job, refunds=2_000_000, status="refunded")
        new_payment = self.adapters.payment(run_id="test-run", job_id=job)
        original = self.adapters.payment
        def racing_payment(**kwargs):
            # Another API/worker reader committed a newer verified snapshot while
            # this provider query was in flight.
            self.store.db.execute("UPDATE orders SET payment=? WHERE id=?", (encode(new_payment), job))
            return old_payment
        self.adapters.payment = racing_payment
        from widemind.business.engine import Deferred
        with self.assertRaises(Deferred):
            self.business._snapshot(old_order)
        self.adapters.payment = original
        self.assertEqual(json.loads(self.business.order(job)["payment"])["status"], "refunded")

    def test_wakeup_validation_rejects_unschedulable_events(self):
        for due in ("tomorrow", float("nan"), float("inf"), -1, True):
            with self.subTest(due=due), self.assertRaises(ValueError):
                self.business.notify(source="test", external_id="bad-due", kind="gmail.sync", payload={}, due_at=due)
        with self.assertRaises(ValueError):
            self.business.notify(source="test", external_id="bad-payload", kind="gmail.sync", payload=[])

    def test_explicit_demo_runs_and_reruns_without_real_effects(self):
        directory = self.directory / "demo"
        first = demonstrate(directory)
        second = demonstrate(directory)
        self.assertEqual(first["order_id"], second["order_id"])
        self.assertFalse(first["live_effects"])
        self.assertTrue(Path(first["artifact"]).exists())
        self.assertIn("DEMO", Path(first["artifact"]).read_text())


if __name__ == "__main__":
    unittest.main()
