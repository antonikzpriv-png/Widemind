"""Discovery adapters against the real ledger with an explicit loopback fixture.

The opening credit, provider identities and all usage in this file are synthetic
test records. No remote provider or customer is contacted.
"""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import threading
import types
import unittest
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from widemind.business.host_clients import EconomyClient, ToolsClient, Unavailable
from widemind.business.store import Store, encode


def load_economy():
    root = next(path for path in Path(__file__).resolve().parents
                if (path / "services/economy/src/widemind/economy/service.py").exists())
    directory = root / "services/economy/src/widemind/economy"
    name = "_discovery_adapter_fixture_economy"
    package = types.ModuleType(name)
    package.__path__ = [str(directory)]
    sys.modules[name] = package
    spec = importlib.util.spec_from_file_location(name + ".service", directory / "service.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ECONOMY = load_economy()


class FixtureEconomy:
    def __init__(self, path):
        self.service = ECONOMY.EconomyService(path)
        self.service.create_run("fixture-run", "test")
        self.requests = []
        self.mode = "test"
        self.mutate = lambda row: row
        self.mutate_children = lambda rows: rows
        self.children_refusal = None
        self.refusal = None
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                self.respond(None)

            def do_POST(self):
                self.respond(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))

            def respond(self, body):
                fixture.requests.append((self.command, self.path, copy.deepcopy(body)))
                if self.headers.get("Authorization") != "Bearer fixture-economy-token":
                    self.reply(401, {"error": {"code": "unauthorized"}})
                    return
                try:
                    if self.path == "/v1/runs/fixture-run/balance":
                        result = {**fixture.service.balance("fixture-run"), "mode": fixture.mode}
                    elif self.path == "/v1/runs/fixture-run/reservations" and body is None:
                        result = [fixture.mutate(row) for row in fixture.service.reservations("fixture-run")]
                    elif self.path == "/v1/runs/fixture-run/reservations":
                        if fixture.refusal:
                            self.reply(fixture.refusal, {"error": {"code": "fixture_denial"}})
                            return
                        result = fixture.mutate(fixture.service.reserve("fixture-run", **body))
                    elif self.path.startswith("/v1/reservations/") and self.path.endswith("/children"):
                        if fixture.children_refusal:
                            self.reply(fixture.children_refusal, {"error": {"code": "fixture_denial"}})
                            return
                        parent = self.path.split("/")[-2]
                        result = fixture.mutate_children(fixture.service.reservation_children(parent))
                    elif self.path.startswith("/v1/reservations/"):
                        result = fixture.mutate(fixture.service.reservation(self.path.rsplit("/", 1)[1]))
                    else:
                        self.reply(404, {"error": {"code": "not_found"}})
                        return
                    self.reply(200, result)
                except ECONOMY.EconomyError as exc:
                    self.reply(exc.status, {"error": {"code": exc.code}})

            def reply(self, status, result):
                payload = encode(result).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


class DiscoveryAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root / "business.sqlite3")
        self.fixture = FixtureEconomy(self.root / "economy.sqlite3")
        self.client = EconomyClient({
            "WIDEMIND_ECONOMY_URL": self.fixture.url,
            "WIDEMIND_ECONOMY_OPERATOR_TOKEN": "fixture-economy-token",
            "WIDEMIND_TOOLS_ACCOUNT": "fixture-tools-account",
        }, self.store, "test")
        self.request = dict(run_id="fixture-run", job_id="fixture-attempt",
                            action_id="fixture-reserve", tool_action_id="fixture-fetch",
                            tool="research.fetch", maximum_micro_usd=250_000)

    def tearDown(self):
        self.fixture.close()
        self.store.close()
        self.temp.cleanup()

    def reserve(self, **changes):
        return self.client.reserve_work(**{**self.request, **changes})

    def bind(self, reservation_id=None):
        self.store.db.execute("""INSERT INTO effects
            (id,run_id,job_id,provider,action,request,state,result,created_at,logical_id,attempt)
            VALUES (?,?,?,'economy','reserve_work',?,'succeeded',?,0,?,0)""",
            (self.request["action_id"], "fixture-run", "fixture-attempt", encode({
                key: self.request[key] for key in ("tool_action_id", "tool", "maximum_micro_usd")}),
             encode({"reservation_id": reservation_id}) if reservation_id else None,
             self.request["action_id"]))

    def cost(self, reservation_id):
        return self.client.work_cost(run_id="fixture-run", reservation_id=reservation_id)

    def test_discovery_reservation_requires_no_order_customer_or_second_wallet(self):
        result = self.reserve()
        self.assertEqual(result.state, "succeeded")
        row = self.fixture.service.reservation(result.data["reservation_id"])
        self.assertEqual(row["action_id"], "fixture-fetch")
        self.assertEqual(row["provider"], "research.fetch")
        self.assertEqual(row["account"], "fixture-tools-account")
        self.assertEqual(row["limits"], {"max_duration_ms": 120_000, "max_storage_bytes": 1_000_000})
        self.assertEqual(row["state"], "reserved")
        self.assertEqual(self.fixture.service.balance("fixture-run")["available_micro_usd"], 4_750_000)
        self.assertEqual(self.fixture.service.charges("fixture-run"), [])
        self.assertEqual(self.store.one("SELECT COUNT(*) AS n FROM orders")["n"], 0)
        self.assertEqual(self.store.one("SELECT COUNT(*) AS n FROM customers")["n"], 0)

    def test_exact_stable_request_is_idempotent_and_changed_allowance_is_refused(self):
        first = self.reserve()
        self.assertEqual(self.reserve(), first)
        self.assertEqual(self.reserve(maximum_micro_usd=300_000).state, "failed")
        self.assertEqual(len(self.fixture.service.reservations("fixture-run")), 1)
        self.assertEqual(self.fixture.service.charges("fixture-run"), [])

    def test_real_budget_exhaustion_is_definitive_without_tool_dispatch(self):
        before = self.fixture.service.ledger("fixture-run")
        result = self.reserve(maximum_micro_usd=5_000_001)
        self.assertEqual(result.state, "failed")
        self.assertEqual(result.data["denial"], "insufficient_funds")
        self.assertEqual(self.fixture.service.reservations("fixture-run"), [])
        self.assertEqual(self.fixture.service.ledger("fixture-run"), before)

    def test_invalid_integer_costs_and_unbounded_identifiers_fail_before_http(self):
        for amount in (True, False, 1.2, "100", -1, 9_000_000_000_000_001, None):
            with self.subTest(amount=amount):
                self.assertEqual(self.reserve(maximum_micro_usd=amount).state, "failed")
        for key in ("run_id", "job_id", "action_id", "tool_action_id", "tool"):
            with self.subTest(key=key):
                self.assertEqual(self.reserve(**{key: "invalid\nidentity"}).state, "failed")
        self.assertEqual(self.fixture.requests, [])

    def test_zero_direct_cost_still_has_a_real_reservation(self):
        result = self.reserve(maximum_micro_usd=0)
        self.assertEqual(result.state, "succeeded")
        self.assertEqual(self.fixture.service.reservation(result.data["reservation_id"])["amount_micro_usd"], 0)
        self.assertEqual(self.fixture.service.balance("fixture-run")["available_micro_usd"], 5_000_000)

    def test_http_denials_do_not_look_like_unknown_paid_execution(self):
        for status in (400, 402, 409, 422):
            with self.subTest(status=status):
                self.fixture.refusal = status
                self.assertEqual(self.reserve().state, "failed")
        self.assertEqual(self.fixture.service.reservations("fixture-run"), [])

    def test_server_errors_remain_uncertain_for_observational_recovery(self):
        self.fixture.refusal = 503
        self.assertEqual(self.reserve().state, "unknown")
        self.assertEqual(self.fixture.service.reservations("fixture-run"), [])

    def test_run_mode_mismatch_prevents_posting_authorization(self):
        self.fixture.mode = "live"
        self.assertEqual(self.reserve().state, "unknown")
        self.assertEqual([method for method, _, _ in self.fixture.requests], ["GET"])
        self.assertEqual(self.fixture.service.reservations("fixture-run"), [])

    def test_lost_authorization_response_recovers_same_row_without_second_post(self):
        self.bind()
        original = self.client.http.request

        def lose(path, body=None, **kwargs):
            value = original(path, body, **kwargs)
            if body is not None:
                raise Unavailable("explicit fixture response lost after ledger commit")
            return value

        self.client.http.request = lose
        self.assertEqual(self.reserve().state, "unknown")
        recovered = self.client.reconcile(action_id=self.request["action_id"])
        self.assertEqual(recovered.state, "succeeded")
        self.assertEqual(len(self.fixture.service.reservations("fixture-run")), 1)
        self.assertEqual(sum(method == "POST" for method, _, _ in self.fixture.requests), 1)
        self.assertEqual(self.fixture.service.charges("fixture-run"), [])

    def test_recovery_matches_full_original_authorization_binding(self):
        reserved = self.reserve()
        self.bind(reserved.data["reservation_id"])
        cases = [("run_id", "other-run"), ("action_id", "other-action"),
                 ("provider", "ai.generate"), ("account", "other-account"),
                 ("amount_micro_usd", 1), ("amount_micro_usd", True),
                 ("limits", {"max_duration_ms": 999_999})]
        for field, value in cases:
            with self.subTest(field=field, value=value):
                self.fixture.mutate = lambda row, k=field, v=value: {**row, k: v}
                self.assertEqual(self.client.reconcile(action_id=self.request["action_id"]).state, "unknown")
                with self.assertRaises((Unavailable, ValueError)):
                    self.cost(reserved.data["reservation_id"])
        self.assertEqual(sum(method == "POST" for method, _, _ in self.fixture.requests), 1)

    def test_final_cost_is_read_from_settled_ledger_not_planned_allowance(self):
        reservation = self.reserve().data["reservation_id"]
        self.bind(reservation)
        self.fixture.service.start_reservation(reservation)
        self.fixture.service.settle(reservation, 123_456, "research.fetch", "fixture-tools-account", "fixture-charge", {}, "succeeded")
        cost = self.cost(reservation)
        self.assertEqual(cost["actual_micro_usd"], 123_456)
        self.assertTrue(cost["costs_final"])
        self.assertEqual(cost["state"], "settled")

    def test_unknown_and_partial_costs_never_become_free_success(self):
        reservation = self.reserve().data["reservation_id"]
        self.bind(reservation)
        self.assertEqual(self.cost(reservation)["actual_micro_usd"], None)
        self.assertFalse(self.cost(reservation)["costs_final"])
        self.fixture.service.start_reservation(reservation)
        self.fixture.service.settle(reservation, 10_000, "research.fetch", "fixture-tools-account", "fixture-subtotal", {}, "unknown")
        cost = self.cost(reservation)
        self.assertIsNone(cost["actual_micro_usd"])
        self.assertFalse(cost["costs_final"])
        self.assertEqual(cost["provisional_micro_usd"], 10_000)

    def test_cancelled_unstarted_reservation_has_verified_zero_cost(self):
        reservation = self.reserve().data["reservation_id"]
        self.bind(reservation)
        self.fixture.service.cancel_reservation(reservation)
        cost = self.cost(reservation)
        self.assertEqual(cost["actual_micro_usd"], 0)
        self.assertTrue(cost["costs_final"])
        self.fixture.mutate = lambda row: {**row, "provisional_micro_usd": 1}
        with self.assertRaises(Unavailable):
            self.cost(reservation)

    def test_cost_read_requires_durable_original_binding_and_valid_actual_amount(self):
        reservation = self.reserve().data["reservation_id"]
        with self.assertRaises(Unavailable):
            self.cost(reservation)
        self.bind(reservation)
        self.fixture.service.start_reservation(reservation)
        self.fixture.service.settle(reservation, 1, "research.fetch", "fixture-tools-account", "fixture-charge", {}, "failed")
        for amount in (None, True, -1, 1.25, "1"):
            with self.subTest(amount=amount):
                self.fixture.mutate = lambda row, value=amount: {**row, "actual_micro_usd": value}
                with self.assertRaises(ValueError):
                    self.cost(reservation)

    def research_parent(self):
        self.request.update(tool="research.collect", tool_action_id="fixture-research")
        parent = self.reserve().data["reservation_id"]
        self.bind(parent)
        self.fixture.service.start_reservation(parent)
        return parent

    def research_child(self, parent, identity, amount, provider="ai.generate"):
        return self.fixture.service.reserve_child(parent, action_id=identity,
            amount_micro_usd=amount, idempotency_key=identity, provider=provider,
            account="fixture-tools-account", limits=dict(EconomyClient.WORK_LIMITS))["reservation_id"]

    def test_research_parent_closure_retains_partial_and_settled_child_costs(self):
        parent = self.research_parent()
        settled = self.research_child(parent, "fixture-completed-generation", 40_000)
        unknown = self.research_child(parent, "fixture-uncertain-generation", 50_000)
        unstarted = self.research_child(parent, "fixture-not-started-generation", 10_000)
        self.fixture.service.start_reservation(settled)
        self.fixture.service.settle(settled, 20_000, "ai.generate", "fixture-tools-account", "fixture-final-charge")
        self.fixture.service.start_reservation(unknown)
        self.fixture.service.settle(unknown, 7_000, "ai.generate", "fixture-tools-account", "fixture-subtotal", outcome="unknown")
        self.fixture.service.settle(parent, 0, "research.collect", "fixture-tools-account", "fixture-parent-charge")

        cost = self.cost(parent)
        self.assertIsNone(cost["actual_micro_usd"])
        self.assertFalse(cost["costs_final"])
        self.assertEqual(cost["provisional_micro_usd"], 27_000)
        self.assertEqual(cost["state"], "unknown")
        self.assertEqual(self.fixture.service.reservation(unknown)["state"], "unknown")
        self.assertEqual(self.fixture.requests[-1][:2], ("GET", f"/v1/reservations/{parent}/children"))
        self.fixture.service.settle(unknown, 9_000, "ai.generate", "fixture-tools-account", "fixture-subtotal")
        self.fixture.service.cancel_reservation(unstarted)
        final = self.cost(parent)
        self.assertEqual(final["actual_micro_usd"], 29_000)
        self.assertTrue(final["costs_final"])

    def test_research_actual_cost_is_final_only_after_every_member_closes(self):
        parent = self.research_parent()
        paid = self.research_child(parent, "fixture-paid-generation", 20_000)
        cancelled = self.research_child(parent, "fixture-cancelled-generation", 30_000)
        self.fixture.service.start_reservation(paid)
        # Actual provider overage remains a liability rather than being hidden at
        # the allocation ceiling. The native ledger records this test bill.
        self.fixture.service.settle(paid, 25_000, "ai.generate", "fixture-tools-account", "fixture-overage")
        self.fixture.service.cancel_reservation(cancelled)
        pending = self.cost(parent)
        self.assertIsNone(pending["actual_micro_usd"])
        self.assertFalse(pending["costs_final"])
        self.assertEqual(pending["provisional_micro_usd"], 25_000)
        self.assertEqual(pending["state"], "running")

        self.fixture.service.settle(parent, 0, "research.collect", "fixture-tools-account", "fixture-parent-charge")
        final = self.cost(parent)
        self.assertEqual(final["actual_micro_usd"], 25_000)
        self.assertTrue(final["costs_final"])
        self.assertEqual(final["provisional_micro_usd"], 0)
        self.assertEqual(final["state"], "settled")
        self.assertEqual(self.cost(parent), final)

    def test_childless_closed_research_has_verified_zero_cost(self):
        parent = self.research_parent()
        self.fixture.service.settle(parent, 0, "research.collect", "fixture-tools-account", "fixture-parent-charge")
        self.assertEqual(self.cost(parent)["actual_micro_usd"], 0)
        self.assertTrue(self.cost(parent)["costs_final"])

    def test_research_child_binding_limits_membership_and_usage_fail_closed(self):
        parent = self.research_parent()
        child = self.research_child(parent, "fixture-child-generation", 20_000)
        self.fixture.service.settle(parent, 0, "research.collect", "fixture-tools-account", "fixture-parent-charge")
        changes = [
            ("run_id", "other-run"), ("account", "other-account"),
            ("provider", "gmail.send"), ("state", "invalid"),
            ("reservation_id", parent), ("action_id", "fixture-research"),
            ("reservation_id", "invalid\nidentity"), ("amount_micro_usd", True),
            ("amount_micro_usd", -1), ("amount_micro_usd", "20000"),
            ("amount_micro_usd", 250_001), ("limits", {}),
            ("limits", {"max_storage_bytes": 1_000_001}),
            ("limits", {"max_duration_ms": 120_001}),
            ("limits", {"max_tokens": True}), ("limits", {"max_tokens": 0}),
            ("limits", {"unknown_limit": 1}), ("provisional_micro_usd", True),
            ("actual_micro_usd", 10),
        ]
        for key, value in changes:
            with self.subTest(key=key, value=value):
                self.fixture.mutate_children = lambda rows, k=key, v=value: [{**rows[0], k: v}]
                with self.assertRaises((Unavailable, ValueError)):
                    self.cost(parent)
        self.fixture.mutate_children = lambda rows: rows + rows
        with self.assertRaises(Unavailable):
            self.cost(parent)
        self.fixture.mutate_children = lambda rows: [rows[0], {**rows[0], "reservation_id": "different-child"}]
        with self.assertRaises(Unavailable):
            self.cost(parent)
        self.fixture.mutate_children = lambda rows: [{**rows[0], "state": "settled", "actual_micro_usd": True}]
        with self.assertRaises(ValueError):
            self.cost(parent)
        self.fixture.mutate_children = lambda rows: [{**rows[0], "state": "cancelled", "provisional_micro_usd": 1}]
        with self.assertRaises(Unavailable):
            self.cost(parent)
        self.assertEqual(self.fixture.service.reservation(child)["state"], "reserved")

    def test_research_child_endpoint_failure_never_becomes_final_free_work(self):
        parent = self.research_parent()
        self.fixture.service.settle(parent, 0, "research.collect", "fixture-tools-account", "fixture-parent-charge")
        self.fixture.children_refusal = 503
        with self.assertRaises(urllib.error.HTTPError):
            self.cost(parent)
        self.fixture.children_refusal = None
        for value in ({"children": []}, None, [None], [{}] * 65):
            with self.subTest(value=value):
                self.fixture.mutate_children = lambda _rows, v=value: v
                with self.assertRaises(Unavailable):
                    self.cost(parent)


class SourceReferenceTests(unittest.TestCase):
    def setUp(self):
        self.reference = {
            "source_id": "sha256:" + "a" * 64,
            "url": "https://example.test/observed-need",
            "sha256": "a" * 64,
            "retrieved_at": "2026-10-29T12:34:56.123456+00:00",
            "kind": "http", "trust": "untrusted",
        }
        self.data = {"url": self.reference["url"], "text": "An observed need from a fixture page.",
                     "source_id": self.reference["source_id"]}

    def result(self, references=None, **changes):
        return {"state": "succeeded", "data": copy.deepcopy(self.data),
                "receipt": {"status": "succeeded", "result": {
                    "data": copy.deepcopy(self.data),
                    "references": copy.deepcopy([self.reference] if references is None else references),
                    "trust": "untrusted"}}, **changes}

    def test_business_rpc_preserves_original_gateway_observation_time_and_body_hash(self):
        response = self.result()
        outcome = ToolsClient.outcome(response)
        self.assertEqual(outcome.state, "succeeded")
        self.assertEqual(outcome.data["_source_references"], [self.reference])
        self.assertEqual(outcome.data["url"], self.data["url"])
        # Reconciliation of an old receipt retains the original timestamp.
        self.assertEqual(ToolsClient.outcome(response), outcome)
        self.assertNotIn("_source_references", response["data"])

    def test_native_gateway_envelope_unwraps_source_data_with_valid_provenance(self):
        response = self.result()
        outcome = ToolsClient.outcome({"kind": "receipt", "receipt": response["receipt"]})
        self.assertEqual(outcome.data, {**self.data, "_source_references": [self.reference]})

    def test_raw_data_cannot_override_gateway_provenance(self):
        forged = [{**self.reference, "retrieved_at": "2099-01-01T00:00:00+00:00"}]
        response = self.result(data={**self.data, "_source_references": forged})
        self.assertEqual(ToolsClient.outcome(response).data["_source_references"], [self.reference])
        self.assertEqual(response["data"]["_source_references"], forged)

    def test_raw_metadata_without_receipt_is_always_removed(self):
        response = {"state": "succeeded", "data": {**self.data, "_source_references": [self.reference]}}
        self.assertEqual(ToolsClient.outcome(response).data, self.data)

    def test_invalid_reference_shapes_are_not_promoted_to_provenance(self):
        invalid = [
            None, "not-a-reference", {},
            {**self.reference, "source_id": "sha256:" + "b" * 64},
            {**self.reference, "sha256": "not-a-hash"},
            {**self.reference, "sha256": "A" * 64},
            {**self.reference, "retrieved_at": "2026-10-29T12:34:56"},
            {**self.reference, "retrieved_at": "not-a-timestamp"},
            {**self.reference, "retrieved_at": "1" * 65},
            {**self.reference, "url": "https://user:secret@example.test/source"},
            {**self.reference, "url": "file:///private/source"},
            {**self.reference, "url": "https://example.test/\nsource"},
            {**self.reference, "url": "https://example.test/" + "x" * 4096},
            {**self.reference, "url": "https://example.test:99999/source"},
            {**self.reference, "kind": "x" * 41},
            {**self.reference, "trust": "trusted"},
        ]
        for reference in invalid:
            with self.subTest(reference=reference):
                response = self.result(references=[reference],
                                       data={**self.data, "_source_references": [self.reference]})
                self.assertEqual(ToolsClient.outcome(response).data, self.data)

    def test_reference_count_is_bounded_and_empty_or_nonlist_conveys_no_proof(self):
        for references in ([], [self.reference] * 31, {"source": self.reference}, "fixture"):
            with self.subTest(references_type=type(references)):
                response = self.result(references=references,
                                       data={**self.data, "_source_references": [self.reference]})
                self.assertEqual(ToolsClient.outcome(response).data, self.data)
        self.assertEqual(len(ToolsClient.outcome(self.result(references=[self.reference] * 30)).data["_source_references"]), 30)

    def test_unrecognized_reference_fields_do_not_enter_business_memory(self):
        response = self.result(references=[{**self.reference, "unexpected": {"private": "fixture"}}])
        self.assertEqual(ToolsClient.outcome(response).data["_source_references"], [self.reference])

    def test_nonresearch_data_keeps_its_existing_shape(self):
        data = {"filename": "fixture.svg", "content": "<svg/>", "checks": {"valid_svg": True}}
        self.assertEqual(ToolsClient.outcome({"state": "succeeded", "data": data}).data, data)
        envelope = {"data": data, "artifacts": [], "references": [], "trust": "untrusted"}
        self.assertEqual(ToolsClient.outcome({"kind": "receipt", "receipt": {
            "status": "succeeded", "result": envelope}}).data, envelope)


if __name__ == "__main__":
    unittest.main()
