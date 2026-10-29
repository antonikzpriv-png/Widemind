"""Synthetic process-death checks at creative/publication state commit boundaries.

No model/provider calls, customer records, money or execution results are live.
"""
import json
import unittest

import test_creative_fulfillment as fixtures
from widemind.business.contracts import Outcome


class CreativePublicationRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.case = fixtures.CreativeFulfillmentTests()
        self.case.setUp()
        self.addCleanup(self.case.tearDown)

    def recover_before_order_status(self, status):
        case = self.case
        job = case.order()
        case.paid(job)
        original = case.business._status

        def process_death(order, next_status, **fields):
            if next_status == status:
                raise KeyboardInterrupt("SYNTHETIC process death before order " + status)
            return original(order, next_status, **fields)

        case.business._status = process_death
        with self.assertRaises(KeyboardInterrupt):
            case.drain()
        version_before = case.store.one(
            "SELECT * FROM artifact_versions WHERE order_id=? AND version=1", (job,)
        )
        self.assertIsNotNone(version_before)
        self.assertEqual(case.count("ai.generate"), 1)
        self.assertEqual(case.count("creative.software"), 1)
        self.assertEqual(case.job(job)["state"], "ready" if status == "ready" else "complete")
        self.assertEqual(case.business.order(job)["status"], "funded" if status == "ready" else "ready")

        # Reopen the actual SQLite file and expire the interrupted event lease.
        # The injected economy/tools retain only their synthetic durable receipts.
        case.store.close()
        case.now += 121
        case.open()
        case.drain()

        order = case.business.order(job)
        self.assertEqual(order["status"], "completed", case.business.diagnostics())
        self.assertEqual(case.job(job)["state"], "complete")
        self.assertEqual(case.count("ai.generate"), 1)
        self.assertEqual(case.count("creative.software"), 1)
        versions = case.store.all("SELECT * FROM artifact_versions WHERE order_id=?", (job,))
        self.assertEqual(len(versions), 1)
        self.assertEqual(versions[0]["manifest"], version_before["manifest"])
        self.assertEqual(versions[0]["artifact"], version_before["artifact"])
        self.assertIsNotNone(versions[0]["published_at"])
        self.assertEqual(json.loads(order["artifact"]), json.loads(version_before["artifact"]))
        publications = case.store.all(
            "SELECT * FROM host_notifications WHERE entity_id=? AND purpose='delivery'", (job,)
        )
        self.assertEqual(len(publications), 1)
        self.assertEqual(publications[0]["subject"], "WideMind verified private delivery")
        self.assertEqual(publications[0]["artifact"], version_before["artifact"])
        self.assertEqual(order["delivery_reference"], publications[0]["id"])
        self.assertEqual(case.store.all("SELECT id FROM events WHERE run_id=? AND state='blocked'", (case.business.run_id,)), [])

    def test_restart_after_creative_ready_before_initial_order_ready_uses_same_artifact(self):
        self.recover_before_order_status("ready")

    def test_restart_after_creative_complete_before_initial_order_delivered_uses_canonical_publication(self):
        self.recover_before_order_status("delivered")

    def test_reconciled_expiry_release_resumes_remaining_caps_after_restart_and_operator_wakeup(self):
        case = self.case
        order_id = case.complete(revisions=1)
        original_version = case.store.one("SELECT * FROM artifact_versions WHERE order_id=? AND version=1", (order_id,))
        # A normally running worker has already consumed the earlier delivery
        # deadline. Only the revision-expiry wakeup remains to do the release.
        case.now = case.business.order(order_id)["delivery_due_at"] + 1
        case.drain()
        release = case.economy.release
        lost = []

        def lost_release_response(**request):
            result = release(**request)
            if not lost:
                lost.append(request["action_id"])
                return Outcome("unknown", reason="SYNTHETIC cancellation committed, response lost")
            return result

        case.economy.release = lost_release_response
        case.now = case.job(order_id)["revision_deadline"] + 1
        case.drain()
        self.assertEqual(case.job(order_id)["state"], "expiring")
        self.assertEqual(case.store.one("SELECT state FROM effects WHERE id=?", (lost[0],))["state"], "unknown")
        self.assertTrue(any(row["state"] == "reserved" for row in case.economy.reservations.values()))

        case.store.close()
        case.open()
        # Same recovery path as the operator portal: observe the original effect
        # first, then explicitly wake the order. Never repeat paid generation.
        case.wake(lost[0], "reconcile")
        case.drain()
        self.assertEqual(case.store.one("SELECT state FROM effects WHERE id=?", (lost[0],))["state"], "succeeded")
        case.wake(order_id)
        case.drain()
        self.assertEqual(case.job(order_id)["state"], "expired")
        self.assertEqual(case.business.order(order_id)["status"], "completed")
        self.assertFalse(any(row["state"] == "reserved" for row in case.economy.reservations.values()))
        self.assertEqual(case.store.one("SELECT * FROM artifact_versions WHERE order_id=? AND version=1", (order_id,)), original_version)
        self.assertEqual(case.count("ai.generate"), 1)
        self.assertEqual(case.count("creative.software"), 1)
        release_calls = [action_id for kind, action_id in case.economy.calls if kind == "release"]
        self.assertEqual(len(release_calls), len(set(release_calls)))

    def test_crash_while_scheduling_revision_expiry_recovers_wakeup_and_releases_caps(self):
        case = self.case
        order_id = case.order(revisions=1)
        notify = case.business.notify

        def process_death(**request):
            if request.get("source") == "creative-expiry":
                raise KeyboardInterrupt("SYNTHETIC process death before durable expiry scheduling")
            return notify(**request)

        case.business.notify = process_death
        case.paid(order_id)
        with self.assertRaises(KeyboardInterrupt):
            case.drain()
        original_version = case.store.one("SELECT * FROM artifact_versions WHERE order_id=? AND version=1", (order_id,))
        self.assertIsNotNone(original_version["published_at"])
        self.assertIn(case.job(order_id)["state"], {"ready", "complete"})
        self.assertEqual(case.store.one("SELECT COUNT(*) AS n FROM events WHERE source='creative-expiry'")["n"], 0)

        case.store.close()
        case.now += 121
        case.open()
        case.drain()
        self.assertEqual(case.business.order(order_id)["status"], "completed")
        expiry_events = case.store.all("SELECT * FROM events WHERE source='creative-expiry'")
        self.assertEqual(len(expiry_events), 1)
        self.assertEqual(expiry_events[0]["due_at"], case.job(order_id)["revision_deadline"])
        self.assertEqual(case.store.one("SELECT * FROM artifact_versions WHERE order_id=? AND version=1", (order_id,)), original_version)
        self.assertEqual(case.store.one("SELECT COUNT(*) AS n FROM host_notifications WHERE entity_id=? AND purpose='delivery'", (order_id,))["n"], 1)

        # The unrelated delivery deadline runs earlier, so it cannot hide a
        # missing revision-expiry wakeup by eventually releasing its budget.
        case.now = case.business.order(order_id)["delivery_due_at"] + 1
        case.drain()
        case.now = case.job(order_id)["revision_deadline"] + 1
        case.drain()
        self.assertEqual(case.job(order_id)["state"], "expired")
        self.assertFalse(any(row["state"] == "reserved" for row in case.economy.reservations.values()))
        self.assertEqual(case.count("ai.generate"), 1)
        self.assertEqual(case.count("creative.software"), 1)

    def test_payment_loss_recovers_terminal_effects_before_cancelling_only_unused_caps(self):
        for terminal_state in ("succeeded", "failed"):
            with self.subTest(terminal_state=terminal_state):
                case = fixtures.CreativeFulfillmentTests()
                case.setUp()
                try:
                    order_id = case.order(revisions=1)
                    case.paid(order_id)
                    execute = case.tools.execute

                    def terminal_execution(**request):
                        result = execute(**request)
                        if request["action"] == "ai.generate" and terminal_state == "failed":
                            # A terminal provider failure can still incur a
                            # charge. Preserve its authoritative accounting.
                            result = Outcome("failed", result.data, "SYNTHETIC billed model failure")
                            case.tools.saved[request["action_id"]] = result
                        return result

                    case.tools.execute = terminal_execution
                    tool = case.business._tool

                    def process_death(*arguments, **keywords):
                        try:
                            return tool(*arguments, **keywords)
                        finally:
                            if arguments[2] == "ai.generate":
                                effect = case.store.one("SELECT * FROM effects WHERE job_id=? AND action='ai.generate'", (order_id,))
                                if effect and effect["state"] == terminal_state:
                                    raise KeyboardInterrupt("SYNTHETIC death after terminal effect before stage commit")

                    case.business._tool = process_death
                    with self.assertRaises(KeyboardInterrupt):
                        case.drain()
                    effect = case.store.one("SELECT * FROM effects WHERE job_id=? AND action='ai.generate'", (order_id,))
                    stage = case.store.one("SELECT * FROM creative_stages WHERE id=?", (effect["id"],))
                    self.assertEqual(effect["state"], terminal_state)
                    self.assertEqual(stage["state"], "reserved")
                    self.assertEqual(case.economy.reservations[stage["reservation_id"]]["state"], "settled")
                    self.assertEqual(case.economy.charges[stage["action_id"]], 17)
                    original_result = json.loads(effect["result"])
                    release = case.economy.release
                    rejected = []

                    def native_like_release(**request):
                        # The permissive business fixture otherwise accepts a
                        # settled cap and conceals this recovery failure.
                        if case.economy.reservations[request["reservation_id"]]["state"] != "reserved":
                            rejected.append(request["reservation_id"])
                            return Outcome("failed", reason="SYNTHETIC authority rejects cancelling a consumed cap")
                        return release(**request)

                    case.economy.release = native_like_release
                    case.store.close()
                    case.now += 121
                    case.open()
                    case.economy.pay(order_id, refunded=True)
                    case.wake(order_id, "payment.changed")
                    case.drain()

                    self.assertEqual(case.job(order_id)["state"], "payment_hold")
                    recovered = case.store.one("SELECT * FROM creative_stages WHERE id=?", (stage["id"],))
                    self.assertEqual(recovered["state"], "complete")
                    result = json.loads(recovered["result_json"])
                    self.assertEqual(result["_accounting"], original_result["_accounting"])
                    if terminal_state == "succeeded":
                        self.assertEqual(result, original_result)
                    else:
                        self.assertIs(result["verified"], False)
                        self.assertTrue(result["errors"])
                    self.assertEqual(case.store.one("SELECT result FROM effects WHERE id=?", (effect["id"],))["result"], effect["result"])
                    self.assertEqual(case.economy.charges[stage["action_id"]], 17)
                    self.assertEqual(case.economy.reservations[stage["reservation_id"]]["state"], "settled")
                    self.assertEqual(rejected, [])
                    self.assertFalse(any(row["state"] == "reserved" for row in case.economy.reservations.values()))
                    self.assertEqual(case.count("ai.generate"), 1)
                    self.assertEqual(case.count("creative.software"), 0)
                finally:
                    case.tearDown()
