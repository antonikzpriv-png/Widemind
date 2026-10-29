"""Regression cases found in the independent audit of the first business slice."""
import json
import unittest

import test_business as original
from widemind.business.contracts import Outcome
from widemind.business.engine import Business
from widemind.business.store import Store, encode
from widemind.demo import drain


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.h = original.BusinessTests()
        self.h.setUp()

    def tearDown(self):
        self.h.tearDown()

    def test_a_run_cannot_reopen_in_another_mode(self):
        h = self.h
        h.request()
        with self.assertRaises(ValueError):
            Business(h.store, run_id="test-run", economy=h.adapters, tools=h.adapters,
                     runtime=h.adapters, mode="demo")

    def test_failed_paid_creation_cannot_reuse_its_settled_reservation(self):
        h = self.h
        job = h.order()
        original_execute = h.adapters.execute
        def fail(**kwargs):
            if kwargs["action"] == "research.collect":
                return h.adapters.save(kwargs["action_id"], Outcome("failed", reason="billable provider failure"))
            return original_execute(**kwargs)
        h.adapters.execute = fail
        h.paid(job)
        drain(h.business)
        failed = h.store.one("SELECT * FROM effects WHERE action='research.collect'")
        with self.assertRaises(ValueError):
            h.business.retry_effect(failed["id"], evidence_reference="cannot reuse consumed spending authorization")

    def test_malformed_delivery_evidence_never_completes_an_order(self):
        h = self.h
        job = h.order()
        h.paid(job)
        h.until(job, "ready")
        h.adapters.delivery_state = "accepted"
        drain(h.business)
        effect = h.store.one("SELECT * FROM effects WHERE action='gmail.send' AND request LIKE '%attachment%'")
        h.adapters.save(effect["id"], Outcome("succeeded", {"delivery_state": "confirmed", "message_id": 123}))
        h.business.notify(source="audit", external_id="invalid-evidence", kind="reconcile", payload={"id": effect["id"]})
        drain(h.business)
        h.wake(job)
        drain(h.business)
        self.assertEqual(h.business.order(job)["status"], "ready")
        self.assertEqual(h.count("record_fulfillment"), 0)

    def test_confirmed_terminal_delivery_is_not_rewritten_by_reconciliation(self):
        h = self.h
        job = h.order()
        h.paid(job)
        drain(h.business)
        effect = h.store.one("SELECT * FROM effects WHERE action='gmail.send' AND request LIKE '%attachment%'")
        h.adapters.save(effect["id"], Outcome("failed", reason="contradictory stale adapter record"))
        h.business.notify(source="audit", external_id="terminal-recheck", kind="reconcile", payload={"id": effect["id"]})
        drain(h.business)
        self.assertEqual(h.store.one("SELECT state FROM effects WHERE id=?", (effect["id"],))["state"], "succeeded")

    def test_run_id_is_normalized_before_all_storage_queries(self):
        h = self.h
        business = Business(h.store, run_id=" padded-run ", economy=h.adapters, tools=h.adapters,
                            runtime=h.adapters, mode="test", clock=lambda: h.now)
        self.assertFalse(business.tick())
        self.assertIsNone(h.store.one("SELECT 1 FROM settings WHERE run_id=' padded-run '"))

    def test_completed_creation_is_recovered_before_refund_hold_and_resume(self):
        h = self.h
        job = h.order()
        h.paid(job)
        original_execute = h.adapters.execute
        def die(**kwargs):
            result = original_execute(**kwargs)
            if kwargs["action"] == "research.collect":
                raise KeyboardInterrupt("process died after provider persisted success")
            return result
        h.adapters.execute = die
        with self.assertRaises(KeyboardInterrupt):
            drain(h.business)
        h.adapters.execute = original_execute
        creation = h.store.one("SELECT * FROM effects WHERE action='research.collect'")
        h.business.notify(source="audit", external_id="recover-create", kind="reconcile", payload={"id": creation["id"]})
        h.adapters.set_payment(job, refunds=2000000, status="refunded")
        h.wake(job, "payment.changed")
        drain(h.business)
        self.assertEqual(h.business.order(job)["status"], "payment_hold")
        self.assertIsNotNone(h.business.order(job)["artifact"])
        h.adapters.set_payment(job, receipts=4000000, refunds=2000000)
        h.business.resolve_hold(job, decision="resume", evidence_reference="verified replacement payment")
        drain(h.business)
        self.assertEqual(h.business.order(job)["status"], "completed")
        self.assertEqual(h.count("research.collect"), 1)
        self.assertEqual(h.count("reserve"), 1)

    def test_refunds_refresh_while_stopped_without_starting_fulfillment(self):
        h = self.h
        job = h.order()
        h.paid(job)
        h.until(job, "funded")
        h.adapters.set("state", "stopped")
        before = len(h.adapters.calls)
        h.adapters.set_payment(job, refunds=2000000, status="refunded")
        h.wake(job, "payment.changed")
        drain(h.business)
        self.assertEqual(json.loads(h.business.order(job)["payment"])["status"], "refunded")
        self.assertEqual(h.business.order(job)["status"], "funded")
        self.assertEqual(len(h.adapters.calls), before)

    def test_legacy_mode_adoption_requires_review_and_rejects_fixture_promotion(self):
        import sqlite3
        h = self.h
        h.order()  # Persist a known test-mode snapshot in the legacy copy.
        path = h.directory / "legacy.sqlite3"
        old = sqlite3.connect(path)
        h.store.db.backup(old)
        old.execute("DROP TABLE business_runs")
        old.execute("DELETE FROM business_migrations WHERE version='005_run_modes.sql'")
        old.commit()
        old.close()
        store = Store(path)
        try:
            self.assertEqual(store.one("SELECT mode FROM business_runs")["mode"], "unclassified")
            with self.assertRaises(ValueError):
                Business(store, run_id="test-run", mode="test", economy=h.adapters, tools=h.adapters, runtime=h.adapters)
            with self.assertRaises(ValueError):
                store.adopt_run_mode("test-run", "live", "cannot promote known fixtures")
            store.adopt_run_mode("test-run", "test", "reviewed known fixture database")
            Business(store, run_id="test-run", mode="test", economy=h.adapters, tools=h.adapters, runtime=h.adapters)
            with self.assertRaises(ValueError):
                store.adopt_run_mode("test-run", "demo", "classified runs cannot be changed")
        finally:
            store.close()

    def test_reconciliation_result_size_and_schema_are_checked(self):
        h = self.h
        h.adapters.unknown_actions.add("gmail.send")
        h.request()
        drain(h.business)
        effect = h.store.one("SELECT * FROM effects WHERE state='unknown'")
        for bad in (Outcome("succeeded", []), Outcome("succeeded", {"huge": "a" * 2_000_001}),
                    Outcome(["succeeded"], {}), Outcome("succeeded", {"delivery_state": "confirmed", "message_id": ""}),
                    Outcome("succeeded", {"delivery_state": "confirmed", "message_id": "mail-id\r\n"})):
            with self.subTest(outcome=type(bad.data).__name__):
                h.adapters.reconcile = lambda **kwargs: bad
                h.business._reconcile(effect["id"])
                self.assertEqual(h.store.one("SELECT state FROM effects WHERE id=?", (effect["id"],))["state"], "unknown")

    def test_reassessment_preserves_new_and_previous_lessons(self):
        h = self.h
        h.adapters.disabled.add("research.collect")
        opportunity = h.request()
        drain(h.business)
        h.adapters.disabled.clear()
        h.business.reconsider(opportunity, evidence_reference="tool became available")
        drain(h.business)
        assessments = h.store.all("SELECT document FROM memory WHERE entity_id=? AND kind LIKE 'assessment:%'", (opportunity,))
        self.assertEqual(len(assessments), 2)
        self.assertEqual({json.loads(row["document"])["feasible"] for row in assessments}, {True, False})

    def test_delivered_and_cancelled_jobs_do_not_gain_false_overdue_failures(self):
        h = self.h
        job = h.order()
        h.paid(job)
        h.until(job, "delivered")
        h.now += 90000
        # Delay economy recognition without pretending physical delivery was late.
        h.business.notify(source="audit", external_id="late-deadline", kind="deadline", payload={"id": job}, due_at=0)
        h.business.tick()
        self.assertIsNone(h.store.one("SELECT 1 FROM memory WHERE entity_id=? AND kind='deadline'", (job,)))
        # An explicitly cancelled delivery is likewise no longer due.
        h.store.db.execute("UPDATE orders SET status='cancelled' WHERE id=?", (job,))
        h.business.notify(source="audit", external_id="cancelled-deadline", kind="deadline", payload={"id": job})
        drain(h.business)
        self.assertIsNone(h.store.one("SELECT 1 FROM memory WHERE entity_id=? AND kind='deadline'", (job,)))

    def test_attachment_headers_and_customer_addresses_reject_control_characters(self):
        h = self.h
        with self.assertRaises(ValueError):
            h.request(customer_email="bad\x00address@example.invalid")
        artifact = {"filename": "report\r\nBcc:other.txt", "media_type": "text/plain", "content": "text", "checks": {"scope": True}}
        for filename in (artifact["filename"], "report.txt\r\n"):
            with self.subTest(filename=filename), self.assertRaises(ValueError):
                h.business._validate_artifact({**artifact, "filename": filename})
        with self.assertRaises(ValueError):
            h.business._validate_artifact({**artifact, "filename": "report.txt", "media_type": "text/plain\r\nother"})

    def test_sources_reject_urls_with_controls_or_invalid_ports(self):
        from widemind.business.services import web_url
        for value in ("https://example.com/\r\nfake", "https://example.com:bad/source", "https://example.com:99999/source"):
            with self.subTest(url=value), self.assertRaises(ValueError):
                web_url(value)

    def test_uncertain_delivery_records_missing_confirmation_not_proven_lateness(self):
        h = self.h
        job = h.order()
        h.paid(job)
        h.until(job, "ready")
        h.adapters.delivery_state = "accepted"
        drain(h.business)
        h.now += 90000
        drain(h.business)
        record = h.store.one("SELECT document FROM memory WHERE entity_id=? AND kind='deadline'", (job,))
        self.assertEqual(json.loads(record["document"])["state"], "delivery_confirmation_overdue")
        self.assertEqual(h.business.order(job)["status"], "ready")


if __name__ == "__main__":
    unittest.main()
