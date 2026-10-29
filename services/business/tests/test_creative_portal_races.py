"""SYNTHETIC concurrent customer requests against independent SQLite connections."""
from copy import deepcopy
import json
import threading
import unittest

import test_creative_fulfillment as fixtures
from widemind.business.creative_portal import scope_change
from widemind.business.host_server import PortalBusiness
from widemind.business.store import Store


class CreativePortalRaceTests(unittest.TestCase):
    def setUp(self):
        self.case = fixtures.CreativeFulfillmentTests()
        self.case.setUp()
        self.addCleanup(self.case.tearDown)

    def test_concurrent_scope_change_rejects_changed_feedback_for_the_same_id(self):
        case = self.case
        job = case.complete()
        original_order = case.business.order(job)
        original_quote = case.store.one("SELECT * FROM quotes WHERE id=?", (original_order["quote_id"],))
        scope = json.loads(original_order["scope"])
        scope["title"] = "New separately quoted synthetic scope"
        barrier = threading.Barrier(2)
        outcomes, errors = [], []

        def customer(feedback):
            store = Store(case.path)
            try:
                business = PortalBusiness(store, run_id=case.business.run_id,
                    economy=case.economy, tools=case.tools, runtime=case.runtime,
                    clock=lambda: case.now, mode="test", services=list(case.business.services.values()))
                receive = business.receive

                def synchronize_receive(**arguments):
                    # Both requests have observed the absent change row and
                    # claimed the same new private request before either commits.
                    barrier.wait(timeout=5)
                    return receive(**arguments)

                business.receive = synchronize_receive
                result = scope_change(business, {"email": "synthetic-customer@example.invalid"}, original_order,
                    {"token": "a" * 43, "requestId": "same-concurrent-id", "feedback": feedback, "scope": deepcopy(scope)})
                outcomes.append((feedback, result))
            except Exception as error:
                errors.append(error)
            finally:
                store.close()

        threads = [threading.Thread(target=customer, args=(feedback,))
            for feedback in ("Synthetic feedback A", "Different synthetic feedback B")]
        for thread in threads:thread.start()
        for thread in threads:thread.join(timeout=10)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(len(outcomes), 1, [(type(error).__name__, str(error)) for error in errors])
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], ValueError)
        self.assertIn("ID reused", str(errors[0]))
        changes = case.store.all("SELECT * FROM customer_change_requests WHERE order_id=?", (job,))
        self.assertEqual(len(changes), 1)
        self.assertEqual(json.loads(changes[0]["request_json"])["feedback"], outcomes[0][0])
        self.assertEqual(case.store.one("SELECT COUNT(*) AS n FROM opportunities")["n"], 2)
        self.assertEqual(case.store.one("SELECT COUNT(*) AS n FROM host_requests")["n"], 1)
        self.assertEqual(case.store.one("SELECT * FROM quotes WHERE id=?", (original_order["quote_id"],)), original_quote)
        self.assertEqual(case.business.order(job)["scope"], original_order["scope"])
        self.assertEqual(case.business.order(job)["cost_micro_usd"], original_order["cost_micro_usd"])
        case.drain()
        new_quote = case.store.all("SELECT * FROM quotes WHERE opportunity_id=?", (outcomes[0][1]["opportunityId"],))
        self.assertEqual(len(new_quote), 1)
        self.assertEqual(json.loads(new_quote[0]["scope"]), scope)
        self.assertEqual(new_quote[0]["status"], "offered")
        self.assertEqual(case.store.one("SELECT COUNT(*) AS n FROM orders")["n"], 1)
        self.assertEqual(case.count("ai.generate"), 1)

    def test_revision_accepted_before_deadline_keeps_its_caps_when_expiry_waits_for_the_commit(self):
        case = self.case
        order_id = case.complete(revisions=1)
        deadline = case.job(order_id)["revision_deadline"]
        case.now = deadline - 1
        customer_ready, expiry_ready = threading.Event(), threading.Event()
        start_customer, start_expiry = threading.Event(), threading.Event()
        customer_checked, expiry_deciding = threading.Event(), threading.Event()
        release_customer = threading.Event()
        outcomes, errors, cancellations = [], [], []

        def connect():
            store = Store(case.path)
            business = PortalBusiness(store, run_id=case.business.run_id,
                economy=case.economy, tools=case.tools, runtime=case.runtime,
                clock=lambda: case.now, mode="test", services=list(case.business.services.values()))
            return store, business

        def customer():
            store, business = connect()
            try:
                def active():
                    # Acceptance has passed its time check while holding the
                    # write lock, but its new revision has not committed yet.
                    customer_checked.set()
                    if not release_customer.wait(timeout=5):
                        raise AssertionError("expiry never competed with revision acceptance")
                    return True
                business._active = active
                customer_ready.set()
                if not start_customer.wait(timeout=5):raise AssertionError("customer not started")
                outcomes.append(business.request_revision(order_id, request_id="deadline-race",
                    feedback="Keep the agreed calculation; improve the title."))
            except Exception as error:
                errors.append(error)
            finally:
                store.close()

        def expiry():
            store, business = connect()
            try:
                # Install after connection initialization: startup migrations
                # also take a write lock and must precede the simulated race.
                def trace(statement):
                    if statement.strip().upper() == "BEGIN IMMEDIATE":
                        expiry_deciding.set()
                store.db.set_trace_callback(trace)
                cancel = business._cancel_slots
                def cancel_slots(*arguments, **keywords):
                    cancellations.append(True)
                    # Makes the regression fail deterministically against the
                    # old implementation, which cancelled after a stale read.
                    expiry_deciding.set()
                    return cancel(*arguments, **keywords)
                business._cancel_slots = cancel_slots
                expiry_ready.set()
                if not start_expiry.wait(timeout=5):raise AssertionError("expiry not started")
                business._deadline(order_id, case.now)
            except Exception as error:
                errors.append(error)
            finally:
                store.close()

        threads = [threading.Thread(target=customer), threading.Thread(target=expiry)]
        for thread in threads:thread.start()
        try:
            self.assertTrue(customer_ready.wait(timeout=5))
            self.assertTrue(expiry_ready.wait(timeout=5))
            start_customer.set()
            self.assertTrue(customer_checked.wait(timeout=5))
            case.now = deadline + 1
            start_expiry.set()
            self.assertTrue(expiry_deciding.wait(timeout=5))
        finally:
            release_customer.set()
            start_customer.set()
            start_expiry.set()
            for thread in threads:thread.join(timeout=10)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual(len(outcomes), 1)
        self.assertTrue(outcomes[0]["scheduled"])
        self.assertEqual(case.job(order_id)["revision"], 1)
        self.assertEqual(case.job(order_id)["state"], "generate")
        self.assertEqual(cancellations, [])
        revision_slots = case.store.all("SELECT * FROM creative_stages WHERE order_id=? AND revision=1", (order_id,))
        self.assertEqual(len(revision_slots), 4)
        self.assertTrue(all(stage["state"] == "reserved" for stage in revision_slots))
        self.assertTrue(all(case.economy.reservations[stage["reservation_id"]]["state"] == "reserved" for stage in revision_slots))
        case.drain()
        self.assertEqual(case.business.order(order_id)["status"], "completed")
        self.assertEqual(case.store.one("SELECT COUNT(*) AS n FROM artifact_versions WHERE order_id=? AND published_at IS NOT NULL", (order_id,))["n"], 2)
        self.assertEqual(case.count("ai.generate"), 2)
        self.assertEqual(case.count("creative.software"), 2)
