"""Deterministic SYNTHETIC provider fixtures for durable PayPal creative stages.

No customer, money, provider/model response, sandbox result or payment is live.
Real generated-software execution is tested separately by the tools Docker suite.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from widemind.business.contracts import Outcome
from widemind.business.creative_services import CREATIVE_SERVICES
from widemind.business.host_server import PortalBusiness
from widemind.business.host_services import LOCAL_SERVICES
from widemind.business.policy import Policy
from widemind.business.store import Store, encode, identity


def calculator_scope(revisions=1):
    return {"title": "Synthetic studio estimator", "description": "Multiply project hours by the agreed hourly rate.",
        "category": "calculator", "inputs": [
            {"id": "hours", "label": "Hours", "min": 0, "max": 500, "default": 8},
            {"id": "rate", "label": "Hourly rate", "min": 1, "max": 1000, "default": 60}],
        "outputs": [{"id": "estimate", "label": "Total"}],
        "acceptance_cases": [
            {"name": "Full day", "inputs": {"hours": 8, "rate": 60}, "expected": {"estimate": 480}, "tolerance": 0},
            {"name": "Partial day", "inputs": {"hours": 2.5, "rate": 80}, "expected": {"estimate": 200}, "tolerance": 0.000001}],
        "requirements": ["Calculate the agreed results", "Reject invalid or out-of-range inputs", "Update results when an input changes"],
        "max_revisions": revisions}


def graphic_scope(revisions=1):
    return {"title": "Synthetic graphic", "description": "A clear customer-specific title card.", "width": 640, "height": 480,
        "required_text": ["SYNTHETIC EXAMPLE"], "palette": ["#ffffff", "#112233"],
        "visual_requirements": ["High contrast title, with a wide margin"], "max_revisions": revisions}


def synthetic_bundle(design):
    """Correctly hashed transport fixture; never claims actual rendering/execution."""
    marker = design.get("heading", "synthetic design")
    document = "<!doctype html><title>Synthetic fixture</title><p>" + marker + "</p>"
    items = [("index.html", "text/html", document, "original"),
        ("calculator.js", "text/javascript", "// SYNTHETIC transport fixture; actual execution is covered by the tools Docker test.\n", "source"),
        ("README.md", "text/markdown", "Synthetic business transport fixture. No actual customer delivery or provider behavior is asserted here.\n", "instructions")]
    files = [{"filename": name, "media_type": media, "content": content, "encoding": "utf-8", "sha256": hashlib.sha256(content.encode()).hexdigest(), "size": len(content.encode()), "role": role} for name, media, content, role in items]
    return {"verified": True, "filename": "index.html", "media_type": "text/html", "content": document,
        "checks": {"synthetic_transport_fixture": True, "source_created": True, "isolated_execution": True,
            "generated_javascript_executed": True, "acceptance_cases": 2, "invalid_inputs_rejected": 22,
            "boundaries_checked": 4}, "files": files}


class SyntheticRuntime:
    mode = "test"
    desired = "active"
    def state(self, _run): return self.desired


class SyntheticEconomy:
    """Independent fake authority; reservations and money never reach a real ledger."""
    mode = "test"
    def __init__(self):
        self.credit = 5000000
        self.reservations, self.outcomes, self.payments = {}, {}, {}
        self.calls, self.charges = [], {}
        self.store = None
    def _once(self, action_id, kind, invoke):
        if action_id not in self.outcomes:
            self.calls.append((kind, action_id))
            self.outcomes[action_id] = invoke()
        return self.outcomes[action_id]
    def reserve_action(self, *, run_id, job_id, action_id, tool_action_id, tool, maximum_micro_usd, max_tokens=None):
        def create():
            held = sum(r["maximum"] for r in self.reservations.values() if r["state"] == "reserved")
            if held + sum(self.charges.values()) + maximum_micro_usd > self.credit:
                return Outcome("failed", reason="synthetic exhausted budget")
            reservation = "synthetic-reservation-" + tool_action_id
            self.reservations[reservation] = {"run_id": run_id, "job_id": job_id, "action_id": tool_action_id, "tool": tool, "maximum": maximum_micro_usd, "max_tokens": max_tokens, "state": "reserved"}
            return Outcome("succeeded", {"reservation_id": reservation})
        return self._once(action_id, "reserve_action", create)
    def reserve(self, *, run_id, job_id, action_id, maximum_micro_usd):
        return self.reserve_action(run_id=run_id, job_id=job_id, action_id=action_id,
            tool_action_id=identity(run_id, job_id, "create"), tool=json.loads(self.store.one("SELECT plan FROM orders WHERE id=?", (job_id,))["plan"])["action"], maximum_micro_usd=maximum_micro_usd)
    def payment_link(self, *, run_id, job_id, action_id, amount_micro_usd, currency):
        return self._once(action_id, "payment_link", lambda: Outcome("succeeded", {"url": "https://payments.example.invalid/SYNTHETIC/" + job_id}))
    def payment(self, *, run_id, job_id):
        snapshot = self.payments.get(job_id, {"run_id": run_id, "job_id": job_id, "revision": 0, "status": "pending", "currency": "USD", "mode": "test", "receipts_micro_usd": 0, "refunds_micro_usd": 0, "fees_micro_usd": 0, "earned_micro_usd": 0, "operating_cost_micro_usd": 0, "costs_final": False})
        return deepcopy(snapshot)
    def pay(self, job_id, run_id="synthetic-run", *, refunded=False):
        previous = self.payment(run_id=run_id, job_id=job_id)
        self.payments[job_id] = {**previous, "revision": previous["revision"] + 1, "status": "refunded" if refunded else "settled", "receipts_micro_usd": 3000000, "refunds_micro_usd": 3000000 if refunded else 0}
    def settle_synthetic(self, reservation, charge):
        row = self.reservations[reservation]
        assert charge <= row["maximum"]
        self.charges.setdefault(row["action_id"], charge)
        row["state"] = "settled"
        snapshot = self.payments.get(row["job_id"])
        if snapshot:
            snapshot["revision"] += 1
            snapshot["operating_cost_micro_usd"] = sum(self.charges.values())
            snapshot["costs_final"] = True
    def record_fulfillment(self, *, run_id, job_id, action_id, delivery_reference):
        def record():
            assert self.store.one("SELECT 1 FROM host_notifications WHERE id=? AND entity_id=? AND purpose='delivery'", (delivery_reference, job_id))
            snapshot = self.payments[job_id]
            snapshot["revision"] += 1
            snapshot["earned_micro_usd"] = snapshot["receipts_micro_usd"] - snapshot["refunds_micro_usd"]
            return Outcome("succeeded", {"delivery_reference": delivery_reference})
        return self._once(action_id, "record_fulfillment", record)
    def release(self, *, run_id, job_id, action_id, reservation_id):
        def release():
            row = self.reservations[reservation_id]
            assert row["run_id"] == run_id and row["job_id"] == job_id
            if row["state"] == "reserved": row["state"] = "cancelled"
            return Outcome("succeeded", {"reservation_id": reservation_id})
        return self._once(action_id, "release", release)
    def reconcile(self, *, action_id):
        return self.outcomes.get(action_id, Outcome("unknown", reason="synthetic authority has no observed outcome"))


class SyntheticTools:
    mode = "test"
    def __init__(self, economy):
        self.economy, self.calls, self.saved, self.disabled = economy, [], {}, set()
        self.malformed_models = 0
        self.failed_checks = 0
        self.validation_failures = 0
        self.unknown_tool = None
        self.recovery_ready = False
        self.crash_after_effect = False
    def available(self, tool): return tool not in self.disabled
    def count(self, tool): return sum(call["action"] == tool for call in self.calls)
    def execute(self, **request):
        action_id = request["action_id"]
        if action_id in self.saved: return self.saved[action_id]
        reservation = self.economy.reservations[request["reservation_id"]]
        assert (reservation["action_id"], reservation["tool"], reservation["run_id"], reservation["job_id"], reservation["maximum"]) == (action_id, request["action"], request["run_id"], request["job_id"], request["maximum_micro_usd"])
        assert reservation["state"] == "reserved"
        self.calls.append(deepcopy(request))
        tool = request["action"]
        if tool == "ai.generate":
            if self.malformed_models:
                self.malformed_models -= 1
                data = {"text": "SYNTHETIC invalid model JSON"}
            else:
                data = {"text": encode({"heading": "Synthetic design " + str(self.count(tool)), "description": "Synthetic fixture, no live provider output.", "accent": "#bbddcc", "outputs": [{"id": "estimate", "expression": {"op": "multiply", "args": [{"input": "hours"}, {"input": "rate"}]}}]})}
            charge = 17
        elif tool == "creative.software":
            if self.validation_failures:
                self.validation_failures -= 1
                data = {"_accounting": {"charged_micro_usd": 0, "provisional": False}}
                result = Outcome("failed", data, "confirmed invalid design before any execution")
                self.saved[action_id] = result
                self.economy.settle_synthetic(request["reservation_id"], 0)
                return result
            if self.failed_checks:
                self.failed_checks -= 1
                data = {"verified": False, "checks": {"synthetic_acceptance_failed": False}, "errors": ["SYNTHETIC wrong arithmetic result"]}
            else: data = synthetic_bundle(request["arguments"]["design"])
            charge = 0
        elif tool == "software.create":
            data = {"filename": "starter.html", "media_type": "text/html", "content": "<!doctype html><h1>Synthetic static template</h1>", "checks": {"static_html": True}}
            charge = 0
        else: raise AssertionError("unimplemented synthetic tool " + tool)
        data["_accounting"] = {"action_id": action_id, "reservation_id": request["reservation_id"], "charged_micro_usd": charge, "provisional": False}
        result = Outcome("succeeded", data)
        self.saved[action_id] = result
        self.economy.settle_synthetic(request["reservation_id"], charge)
        if tool == self.unknown_tool:
            return Outcome("unknown", reason="synthetic transport lost response; inspect original effect")
        if self.crash_after_effect and tool == "ai.generate":
            self.crash_after_effect = False
            raise KeyboardInterrupt("synthetic process death after durable provider effect")
        return result
    def reconcile(self, *, action_id):
        if self.recovery_ready:
            return self.saved.get(action_id, Outcome("unknown", reason="synthetic outcome absent"))
        return Outcome("unknown", reason="synthetic effect still uncertain")


class CreativeFulfillmentTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "business.sqlite3"
        self.now, self.sequence = 1800000000.0, 0
        self.runtime, self.economy = SyntheticRuntime(), SyntheticEconomy()
        self.tools = SyntheticTools(self.economy)
        self.open()
    def open(self):
        self.store = Store(self.path)
        self.economy.store = self.store
        policy = None if self.store.one("SELECT 1 FROM settings WHERE run_id='synthetic-run'") else Policy(capabilities=("graphics", "software"), prices={"interactive-calculator": 3000000, "custom-graphic": 2000000, "software-starter": 3000000}, max_job_micro_usd=240000, experiment_budget_micro_usd=2000000)
        self.business = PortalBusiness(self.store, run_id="synthetic-run", economy=self.economy, tools=self.tools, runtime=self.runtime, clock=lambda: self.now, mode="test", policy=policy, services=[*CREATIVE_SERVICES, *LOCAL_SERVICES])
    def tearDown(self):
        self.store.close()
        self.directory.cleanup()
    def drain(self, maximum=100):
        for _ in range(maximum):
            if not self.business.tick(): return
        self.fail("Synthetic runtime exceeded its free event bound: " + encode(self.business.diagnostics()))
    def wake(self, order_id, kind="advance"):
        self.sequence += 1
        self.business.notify(source="synthetic-test", external_id=str(self.sequence), kind=kind, payload={"id": order_id})
    def request(self, *, revisions=1, service="interactive-calculator", key="request-1", scope=None):
        scope = scope or (calculator_scope(revisions) if service == "interactive-calculator" else {"title": "Synthetic starter", "description": "A deliberately static template"})
        op = self.business.receive(source="synthetic-fixture", external_id=key, service=service, scope=scope, customer_email="synthetic-customer@example.invalid")
        self.drain()
        quote = self.store.one("SELECT * FROM quotes WHERE opportunity_id=?", (op,))
        self.assertIsNotNone(quote, self.store.one("SELECT assessment FROM opportunities WHERE id=?", (op,)))
        return op, quote
    def order(self, *, revisions=1, service="interactive-calculator"):
        _op, quote = self.request(revisions=revisions, service=service)
        job = self.business.accept(quote["id"], customer_email="synthetic-customer@example.invalid", evidence_reference="SYNTHETIC accepted terms")
        self.drain()
        self.assertEqual(self.business.order(job)["status"], "awaiting_payment")
        return job
    def paid(self, job):
        self.economy.pay(job)
        self.wake(job, "payment.changed")
    def job(self, order_id): return self.store.one("SELECT * FROM creative_jobs WHERE order_id=?", (order_id,))
    def count(self, tool): return self.tools.count(tool)
    def complete(self, *, revisions=1):
        job = self.order(revisions=revisions)
        self.paid(job)
        self.drain()
        self.assertEqual(self.business.order(job)["status"], "completed", self.business.diagnostics())
        return job

    def test_unpaid_order_and_missing_capabilities_never_call_a_model(self):
        job = self.order()
        for _ in range(3): self.wake(job); self.drain()
        self.assertEqual(self.tools.calls, [])
        self.assertEqual(self.economy.reservations, {})
        self.tools.disabled.add("creative.software")
        op = self.business.receive(source="synthetic", external_id="unavailable", service="interactive-calculator", scope=calculator_scope(), customer_email="synthetic-customer@example.invalid")
        self.drain()
        self.assertEqual(self.store.one("SELECT status FROM opportunities WHERE id=?", (op,))["status"], "needs_review")
        self.assertEqual(self.count("ai.generate"), 0)

    def test_all_attempt_revision_reservations_fit_the_frozen_accepted_budget(self):
        job = self.complete(revisions=2)
        order = self.business.order(job)
        stages = self.store.all("SELECT * FROM creative_stages WHERE order_id=?", (job,))
        self.assertEqual(len(stages), 12)
        self.assertEqual(sum(stage["maximum_micro_usd"] for stage in stages), order["cost_micro_usd"])
        self.assertLessEqual(order["cost_micro_usd"], 240000)
        model = next(call for call in self.tools.calls if call["action"] == "ai.generate")
        self.assertEqual(len(self.economy.reservations), 12)
        self.assertEqual(model["arguments"]["max_cost_microusd"], order["cost_micro_usd"] // 6)
        self.assertEqual(model["arguments"]["max_output_tokens"], 2048)
        self.assertEqual(self.economy.reservations[model["reservation_id"]]["max_tokens"], 2048)
        manifest = json.loads(self.store.one("SELECT manifest FROM artifact_versions WHERE order_id=?", (job,))["manifest"])
        self.assertEqual(manifest["scope"], calculator_scope(2))
        self.assertEqual(manifest["accepted_budget_micro_usd"], order["cost_micro_usd"])
        self.assertTrue(all(cost["charged_micro_usd"] is not None for cost in manifest["creation_costs"]))

    def test_malformed_model_output_consumes_only_one_bounded_repair(self):
        self.tools.malformed_models = 1
        job = self.complete()
        self.assertEqual(self.count("ai.generate"), 2)
        self.assertEqual(self.count("creative.software"), 1)
        self.assertEqual(self.job(job)["attempt"], 1)
        self.assertEqual(sum(self.economy.charges.values()), 34)
        self.assertEqual(len(self.store.all("SELECT * FROM artifact_versions WHERE order_id=?", (job,))), 1)

    def test_failed_independent_checks_get_only_one_separately_authorized_repair(self):
        self.tools.failed_checks = 1
        self.complete()
        self.assertEqual(self.count("ai.generate"), 2)
        self.assertEqual(self.count("creative.software"), 2)
        self.assertEqual(len({call["reservation_id"] for call in self.tools.calls}), 4)

    def test_confirmed_renderer_validation_failure_is_repairable_without_replaying_it(self):
        self.tools.validation_failures = 1
        self.complete()
        self.assertEqual(self.count("ai.generate"), 2)
        self.assertEqual(self.count("creative.software"), 2)

    def test_exhausted_repair_limit_keeps_diagnostics_and_never_spins_paid_models(self):
        self.tools.malformed_models = 20
        job = self.order(revisions=2)
        self.paid(job); self.drain()
        self.assertEqual(self.job(job)["state"], "needs_review")
        self.assertEqual(self.count("ai.generate"), 2)
        self.assertEqual(self.store.all("SELECT * FROM artifact_versions WHERE order_id=?", (job,)), [])
        for _ in range(5): self.wake(job); self.drain()
        self.assertEqual(self.count("ai.generate"), 2)
        self.assertFalse(any(row["state"] == "reserved" for row in self.economy.reservations.values()))

    def test_unknown_effect_requires_observation_and_cannot_be_retried_or_released(self):
        self.tools.unknown_tool = "ai.generate"
        job = self.order()
        self.paid(job); self.drain()
        effect = self.store.one("SELECT * FROM effects WHERE job_id=? AND action='ai.generate'", (job,))
        self.assertEqual(effect["state"], "unknown")
        with self.assertRaises(ValueError): self.business.retry_effect(effect["id"], evidence_reference="unknown is not failed")
        for _ in range(3): self.wake(job); self.drain()
        self.assertEqual(self.count("ai.generate"), 1)
        self.assertEqual(self.count("creative.software"), 0)
        self.tools.recovery_ready = True
        self.wake(effect["id"], "reconcile"); self.drain(); self.wake(job); self.drain()
        self.assertEqual(self.business.order(job)["status"], "completed")
        self.assertEqual(self.count("ai.generate"), 1)

    def test_restart_between_generation_and_verification_reuses_completed_generation(self):
        job = self.order(); self.paid(job)
        for _ in range(30):
            if self.job(job) and self.job(job)["state"] == "verify": break
            self.assertTrue(self.business.tick())
        self.assertEqual(self.count("ai.generate"), 1)
        self.assertEqual(self.count("creative.software"), 0)
        self.store.close(); self.open(); self.drain()
        self.assertEqual(self.business.order(job)["status"], "completed")
        self.assertEqual(self.count("ai.generate"), 1)

    def test_process_death_after_model_effect_recovers_original_action_once(self):
        self.tools.crash_after_effect = True
        job = self.order(); self.paid(job)
        with self.assertRaises(KeyboardInterrupt): self.drain()
        self.assertEqual(self.count("ai.generate"), 1)
        original = next(call["action_id"] for call in self.tools.calls if call["action"] == "ai.generate")
        self.store.close(); self.now += 121; self.open()
        self.tools.recovery_ready = True
        self.drain()
        self.assertEqual(self.business.order(job)["status"], "completed")
        self.assertEqual(self.count("ai.generate"), 1)
        self.assertEqual(self.store.one("SELECT state FROM effects WHERE id=?", (original,))["state"], "succeeded")

    def test_included_revision_is_bounded_idempotent_and_keeps_old_artifacts(self):
        job = self.complete()
        first = self.store.one("SELECT * FROM artifact_versions WHERE order_id=? AND version=1", (job,))
        request = self.business.request_revision(job, request_id="synthetic-customer-change", feedback="Use a friendlier heading; keep all calculations and accepted scope.")
        self.assertEqual(self.business.request_revision(job, request_id="synthetic-customer-change", feedback="Use a friendlier heading; keep all calculations and accepted scope."), request)
        with self.assertRaises(ValueError): self.business.request_revision(job, request_id="synthetic-customer-change", feedback="Different feedback with the same ID")
        self.drain()
        versions = self.store.all("SELECT * FROM artifact_versions WHERE order_id=? ORDER BY version", (job,))
        self.assertEqual(len(versions), 2)
        self.assertEqual(versions[0], first)
        self.assertNotEqual(versions[0]["artifact"], versions[1]["artifact"])
        manifest = json.loads(versions[1]["manifest"])
        self.assertEqual(manifest["revision_history"][0]["manifest_sha256"], hashlib.sha256(first["manifest"].encode()).hexdigest())
        self.assertEqual(manifest["scope"], calculator_scope())
        with self.assertRaises(ValueError): self.business.request_revision(job, request_id="too-many", feedback="Another included round")
        self.assertEqual(self.count("ai.generate"), 2)

    def test_scope_expansion_requires_new_quote_and_cannot_mutate_accepted_work(self):
        job = self.complete()
        original = self.business.order(job)
        opportunity = self.store.one("SELECT opportunity_id FROM quotes WHERE id=?", (original["quote_id"],))["opportunity_id"]
        changed = calculator_scope(); changed["description"] = "Additional separately scoped calculator work"
        with self.assertRaises(ValueError): self.business.reconsider(opportunity, evidence_reference="new requirements", scope=changed)
        _op, quote = self.request(key="scope-change-separate-quote", scope=changed)
        self.assertNotEqual(quote["id"], original["quote_id"])
        self.assertEqual(self.business.order(job)["scope"], original["scope"])
        self.assertEqual(self.business.order(job)["plan"], original["plan"])
        self.assertEqual(self.count("ai.generate"), 1)  # quoting additional scope is free local work

    def test_expiry_releases_provably_unused_revision_caps_without_model_calls(self):
        job = self.complete(revisions=2)
        self.assertTrue(any(r["state"] == "reserved" and r["maximum"] > 0 for r in self.economy.reservations.values()))
        self.now = self.job(job)["revision_deadline"] + 1
        self.drain()
        self.assertEqual(self.job(job)["state"], "expired")
        self.assertFalse(any(r["state"] == "reserved" for r in self.economy.reservations.values()))
        self.assertEqual(self.count("ai.generate"), 1)
        with self.assertRaises(ValueError): self.business.request_revision(job, request_id="expired", feedback="Late revision")

    def test_revision_accepted_before_expiry_finishes_once_and_releases_future_caps(self):
        job = self.complete(revisions=2)
        deadline = self.job(job)["revision_deadline"]
        self.now = deadline - 1
        self.tools.unknown_tool = "ai.generate"
        self.business.request_revision(job, request_id="accepted-before-expiry", feedback="A new heading within the frozen scope")
        self.drain()
        effect = self.store.one("SELECT * FROM effects WHERE job_id=? AND action='ai.generate' AND state='unknown'", (job,))
        self.assertIsNotNone(effect)
        self.now = deadline + 1
        self.drain()  # the original expiry may observe the accepted, uncertain revision
        self.assertEqual(self.count("ai.generate"), 2)
        self.tools.recovery_ready = True
        self.wake(effect["id"], "reconcile"); self.drain(); self.wake(job); self.drain()
        self.assertEqual(len(self.store.all("SELECT * FROM artifact_versions WHERE order_id=?", (job,))), 2)
        self.assertFalse(any(row["state"] == "reserved" for row in self.economy.reservations.values()))
        self.assertEqual(self.count("ai.generate"), 2)

    def test_crash_after_manifest_insert_reuses_exact_immutable_version_and_timestamp(self):
        job = self.order(); self.paid(job)
        original = self.business._cancel_slots
        def crash_after_insert(order, **options):
            if self.store.one("SELECT 1 FROM artifact_versions WHERE order_id=?", (job,)):
                raise KeyboardInterrupt("synthetic crash after version commit, before ready transition")
            return original(order, **options)
        self.business._cancel_slots = crash_after_insert
        with self.assertRaises(KeyboardInterrupt): self.drain()
        first = self.store.one("SELECT * FROM artifact_versions WHERE order_id=? AND version=1", (job,))
        self.assertIsNotNone(first)
        self.store.close(); self.now += 121; self.open(); self.drain()
        published = json.loads(self.business.order(job)["artifact"])
        self.assertEqual(published, json.loads(first["artifact"]))
        self.assertEqual(published["manifest"]["created_at"], json.loads(first["manifest"])["created_at"])
        self.assertEqual(self.count("ai.generate"), 1)
        self.assertEqual(self.count("creative.software"), 1)

    def test_scope_or_budget_tampering_after_acceptance_cannot_start_paid_generation(self):
        job = self.order(); self.paid(job)
        for _ in range(30):
            if self.job(job): break
            self.assertTrue(self.business.tick())
        scope = json.loads(self.business.order(job)["scope"])
        scope["title"] = "Synthetic changed accepted scope"
        self.store.db.execute("UPDATE orders SET scope=? WHERE id=?", (encode(scope), job))
        self.drain()
        self.assertEqual(self.count("ai.generate"), 0)
        self.assertEqual(self.count("creative.software"), 0)
        self.assertTrue(self.store.one("SELECT 1 FROM events WHERE state='blocked' AND error LIKE '%immutable%'"))

    def test_unsupported_features_and_out_of_adapter_ranges_never_get_a_quote(self):
        for index, mutation in enumerate((
            lambda scope: scope.update(requirements=["Upload files to an external service"]),
            lambda scope: scope["inputs"][0].update(max=10**10),
            lambda scope: scope["inputs"][0].update(id="then"),
        )):
            scope = calculator_scope(); mutation(scope)
            op = self.business.receive(source="synthetic", external_id="unsupported-" + str(index), service="interactive-calculator", scope=scope, customer_email="synthetic-customer@example.invalid")
            self.drain()
            self.assertIsNone(self.store.one("SELECT * FROM quotes WHERE opportunity_id=?", (op,)))
            self.assertEqual(self.store.one("SELECT status FROM opportunities WHERE id=?", (op,))["status"], "needs_review")
        self.assertEqual(self.count("ai.generate"), 0)

    def test_exhaustion_and_pause_prevent_model_work(self):
        self.economy.credit = 1
        job = self.order(); self.paid(job); self.drain()
        self.assertEqual(self.count("ai.generate"), 0)
        self.assertEqual(self.count("creative.software"), 0)
        self.runtime.desired = "paused"
        self.economy.credit = 5000000
        self.wake(job)
        self.drain()
        self.assertEqual(self.count("ai.generate"), 0)

    def test_pause_after_generation_preserves_stage_until_explicit_resume(self):
        job = self.order(); self.paid(job)
        for _ in range(30):
            if self.job(job) and self.job(job)["state"] == "verify": break
            self.assertTrue(self.business.tick())
        self.runtime.desired = "paused"
        self.drain()
        self.assertEqual(self.count("ai.generate"), 1)
        self.assertEqual(self.count("creative.software"), 0)
        self.runtime.desired = "active"
        self.drain()
        self.assertEqual(self.business.order(job)["status"], "completed")
        self.assertEqual(self.count("ai.generate"), 1)

    def test_existing_static_template_offering_remains_compatible_without_model_or_revisions(self):
        job = self.order(service="software-starter")
        self.paid(job); self.drain()
        self.assertEqual(self.business.order(job)["status"], "completed")
        self.assertEqual(self.count("software.create"), 1)
        self.assertEqual(self.count("ai.generate"), 0)
        self.assertIsNone(self.job(job))
        with self.assertRaises(ValueError): self.business.request_revision(job, request_id="template", feedback="Unsupported enhanced revision")

    def test_enhanced_software_requires_category_specific_independent_check_evidence(self):
        service = next(item for item in CREATIVE_SERVICES if item.name == "interactive-calculator")
        good = synthetic_bundle({"heading": "Synthetic artifact"})
        self.assertEqual(service.deliverable(calculator_scope(), good)["filename"], "index.html")
        mutations = [
            lambda result: result.update(checks={"generic_claim": True}),
            lambda result: result["checks"].pop("generated_javascript_executed"),
            lambda result: result["checks"].update(acceptance_cases=1),
            lambda result: result["checks"].update(boundaries_checked=2),
            lambda result: result["checks"].update(invalid_inputs_rejected=0),
            lambda result: result["checks"].update(isolated_execution=False),
        ]
        for mutate in mutations:
            result = deepcopy(good); mutate(result)
            with self.assertRaises(ValueError): service.deliverable(calculator_scope(), result)

    def test_payment_loss_preserves_uncertainty_before_releasing_any_unused_caps(self):
        self.tools.unknown_tool = "ai.generate"
        job = self.order(); self.paid(job); self.drain()
        effect = self.store.one("SELECT * FROM effects WHERE job_id=? AND action='ai.generate' AND state='unknown'", (job,))
        self.economy.pay(job, refunded=True)
        before = len([kind for kind, _ in self.economy.calls if kind == "release"])
        self.wake(job, "payment.changed"); self.drain()
        self.assertEqual(len([kind for kind, _ in self.economy.calls if kind == "release"]), before)
        self.assertEqual(self.count("ai.generate"), 1)
        self.tools.recovery_ready = True
        self.wake(effect["id"], "reconcile"); self.drain(); self.wake(job); self.drain()
        self.assertEqual(self.job(job)["state"], "payment_hold")
        self.assertFalse(any(row["state"] == "reserved" for row in self.economy.reservations.values()))
        self.economy.pay(job); self.wake(job, "payment.changed"); self.drain()
        self.assertEqual(self.count("ai.generate"), 1)
        self.assertEqual(self.count("creative.software"), 0)
        self.assertEqual(self.job(job)["state"], "payment_hold")


if __name__ == "__main__": unittest.main()
