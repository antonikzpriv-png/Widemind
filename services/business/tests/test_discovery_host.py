"""Private bridge qualification. All market/contact/source data is test input."""
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

from widemind.business.host_server import Host


class DiscoveryHostTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.runtime = self.root / "runtime.sqlite3"
        with sqlite3.connect(self.runtime) as db:
            db.execute("CREATE TABLE runtime_runs(id TEXT PRIMARY KEY,state TEXT,desired TEXT,profile TEXT)")
            db.execute("INSERT INTO runtime_runs VALUES ('discovery-fixture-run','paused','pause','foundation')")
        self.env = {
            "WIDEMIND_BUSINESS_DB": str(self.root / "business.sqlite3"),
            "WIDEMIND_BUSINESS_TOKEN": "b" * 48,
            "WIDEMIND_BUSINESS_MODE": "test",
            "WIDEMIND_RUNTIME_DATABASE": str(self.runtime),
        }
        self.host = Host(self.env)
        self.run = "discovery-fixture-run"

    def tearDown(self):
        self.temp.cleanup()

    def configure(self):
        # An illustrative HTTPS URL is never fetched by configuration/status.
        return self.host.handle("/v1/discovery-configure", {
            "runId": self.run,
            "config": {
                "enabled": True,
                "fixture_mode": False,
                "allowance_micro_usd": 10000,
                "adaptation_limit": 3,
                "adaptation_min_samples": 2,
                "markets": [{
                    "id": "fixture-market",
                    "label": "Explicit bridge test market",
                    "need_terms": ["needs a graphic"],
                    "source_urls": ["https://fixture.example.test/needs"],
                    "service": "svg-graphic",
                    "scope": {"title": "Fixture prototype"},
                    "kind": "experiment",
                    "proposed_offering": "A bounded illustration prototype",
                    "research_allowance_micro_usd": 1000,
                    "interval_seconds": 3600,
                    "freshness_seconds": 300,
                    "cooldown_seconds": 3600,
                    "max_attempts": 2,
                    "priority": 5,
                }],
            },
        })

    def test_configuration_status_and_restart_schedule_without_provider_calls(self):
        with patch("widemind.business.host_clients.HTTP.request", side_effect=AssertionError("No provider calls from operator reads/configuration")) as request:
            self.assertEqual(self.configure(), {"saved": True})
            state = self.host.handle("/v1/discovery-status", {"runId": self.run})
            self.assertEqual(state["mode"], "test")
            self.assertEqual(state["runtime_state"], "paused")
            self.assertEqual(state["config"]["markets"][0]["id"], "fixture-market")
            work = self.host.handle("/v1/work-state", {"runId": self.run})
            self.assertIsNotNone(work["eventId"])
            restarted = Host(self.env)
            self.assertEqual(restarted.handle("/v1/work-state", {"runId": self.run}), work)
            private = restarted.handle("/v1/discovery-status", {"runId": self.run, "offset": 0, "limit": 1})
            self.assertEqual(private["history_limit"], 1)
            request.assert_not_called()
        business = self.host.business(self.run)
        try:
            self.assertEqual(business.store.all("SELECT * FROM customers"), [])
            self.assertEqual(business.store.all("SELECT * FROM orders"), [])
            self.assertEqual(business.store.all("SELECT * FROM effects"), [])
        finally:
            business.store.close()

    def test_paused_step_keeps_scheduled_work_and_public_projection_private(self):
        self.configure()
        work = self.host.handle("/v1/work-state", {"runId": self.run})
        if work["dueAt"] > time.time():
            business = self.host.business(self.run)
            try:
                business.store.db.execute("UPDATE events SET due_at=? WHERE id=?", (time.time() - 1, work["eventId"]))
            finally:
                business.store.close()
        result = self.host.handle("/v1/step", {"runId": self.run, "eventId": work["eventId"], "actionId": "explicit-paused-fixture-step"})
        self.assertFalse(result["processed"])
        self.assertEqual(result["eventState"], "queued")
        with patch.object(self.host, "offerings", return_value=[]):
            public = self.host.handle("/v1/public", {"runId": self.run})
        serialized = json.dumps(public)
        self.assertNotIn("fixture-market", serialized)
        self.assertNotIn("need_terms", serialized)
        self.assertNotIn("source_urls", serialized)
        self.assertNotIn("prototype", serialized)

    def test_missing_opportunity_or_cross_run_never_creates_an_experiment(self):
        self.configure()
        with self.assertRaises(ValueError):
            self.host.handle("/v1/experiment-start", {"runId": self.run, "opportunityId": "not-a-real-opportunity", "requestId": "explicit-missing-fixture", "spec": {"hypothesis": "Missing evidence is not an opportunity", "allowance_micro_usd": 0, "deadline": time.time() + 3600, "success_criteria": {"artifact_valid": True}, "stop_conditions": ["deadline", "allowance_exhausted", "single_attempt", "scope_failure", "operator_stop"]}})
        with self.assertRaises(ValueError):
            self.host.handle("/v1/discovery-status", {"runId": "another-run"})
        business = self.host.business(self.run)
        try:
            self.assertEqual(business.store.all("SELECT * FROM customers"), [])
            self.assertEqual(business.store.all("SELECT * FROM orders"), [])
            self.assertEqual(business.store.all("SELECT * FROM effects"), [])
        finally:
            business.store.close()

    def test_reconciliation_schedules_original_unknown_experiment_without_dispatch(self):
        self.configure()
        business = self.host.business(self.run)
        try:
            opportunity = business.receive(source="explicit-test-fixture", external_id="unknown-prototype", service="svg-graphic", scope={"title": "Fixture prototype"})
            business.store.db.execute("""INSERT INTO discovery_experiments
                (id,run_id,opportunity_id,request_id,fingerprint,hypothesis,allowance_micro_usd,deadline,success_criteria,stop_conditions,plan,state,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,'unknown',?)""",
                ("fixture-experiment", self.run, opportunity, "fixture-request", "fixture-fingerprint", "An interrupted fixture can be observed", 1000, time.time() + 3600, '{"artifact_valid":true}', '["deadline","single_attempt"]', '{"action":"graphics.create","arguments":{"title":"Fixture prototype"},"maximum_micro_usd":1000}', time.time()))
            business.store.db.execute("""INSERT INTO effects
                (id,run_id,job_id,provider,action,request,state,created_at,logical_id,attempt)
                VALUES (?,?,?,'tools','graphics.create','{}','unknown',?,?,0)""", ("fixture-original-effect", self.run, "fixture-experiment", time.time(), "fixture-original-effect"))
        finally:
            business.store.close()

        payload = {"runId": self.run, "actionId": "fixture-original-effect", "mode": "reconcile", "evidenceReference": "explicit-test-reconciliation"}
        with patch("widemind.business.host_clients.HTTP.request", side_effect=AssertionError("Recovery ingress must never dispatch")) as request:
            self.assertTrue(self.host.handle("/v1/recovery", payload)["scheduled"])
            self.host.handle("/v1/recovery", payload)
            request.assert_not_called()
        business = self.host.business(self.run)
        try:
            self.assertEqual(business.store.one("SELECT state FROM effects WHERE id='fixture-original-effect'")["state"], "unknown")
            self.assertEqual(len(business.store.all("SELECT id FROM events WHERE kind='reconcile'")), 1)
            self.assertEqual(len(business.store.all("SELECT id FROM events WHERE kind='experiment.step'")), 1)
            self.assertEqual(business.store.all("SELECT * FROM customers"), [])
            self.assertEqual(business.store.all("SELECT * FROM orders"), [])
        finally:
            business.store.close()

    def test_private_upcoming_history_can_reach_entries_after_the_first_hundred(self):
        self.configure()
        business = self.host.business(self.run)
        try:
            for index in range(135):
                business.notify(source="explicit-history-fixture", external_id=str(index), kind="discovery.observe", payload={}, due_at=time.time() + 3600 + index)
        finally:
            business.store.close()
        first = self.host.handle("/v1/discovery-status", {"runId": self.run, "offset": 0, "limit": 100})
        second = self.host.handle("/v1/discovery-status", {"runId": self.run, "offset": 100, "limit": 100})
        self.assertGreater(first["totals"]["upcoming"], 100)
        self.assertEqual(len(first["upcoming"]), 100)
        self.assertGreater(len(second["upcoming"]), 0)
        self.assertFalse({item["id"] for item in first["upcoming"]} & {item["id"] for item in second["upcoming"]})
        self.assertEqual(second["history_offset"], 100)



if __name__ == "__main__":
    unittest.main()
