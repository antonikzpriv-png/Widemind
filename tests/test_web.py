"""Actual WSGI/business integration; provider fixtures are explicitly test-only."""
import io
import json
from pathlib import Path
import tempfile
import unittest
from widemind.business.api import API
from widemind.business.contracts import Outcome
from widemind.business.engine import Business
from widemind.business.services import ResearchBrief
from widemind.business.store import Store, encode
from widemind.business.views import financial_summary
from widemind.demo import DemoAdapters, drain


class WebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.token = "explicit-test-token-" * 3
        self.business = self.factory()
        self.adapters = self.business.tools
        self.scope = {"topic": "Private customer topic", "sections": ["Overview"], "min_sources": 2}
        self.api = API(self.factory, operator_token=self.token, contact_email="widemind@example.invalid")

    def tearDown(self):
        self.business.close()
        self.temp.cleanup()

    def factory(self):
        adapter = DemoAdapters(self.root / "fixtures.sqlite3", mode="test")
        return Business(Store(self.root / "business.sqlite3"), run_id="web-run", economy=adapter,
                        tools=adapter, runtime=adapter, mode="test")

    def call(self, path, method="GET", payload=None, *, auth=True, headers=None, app=None):
        raw = encode(payload).encode() if payload is not None else b""
        env = {"PATH_INFO": path, "REQUEST_METHOD": method, "CONTENT_TYPE": "application/json",
               "CONTENT_LENGTH": str(len(raw)), "wsgi.input": io.BytesIO(raw)}
        if auth:
            env["HTTP_AUTHORIZATION"] = "Bearer " + self.token
        env.update(headers or {})
        result = []
        body = b"".join((app or self.api)(env, lambda status, values: result.append((status, dict(values)))))
        return result[0][0], result[0][1], body

    def order(self, *, transport="confirmed"):
        self.adapters.delivery_state = transport
        self.business.receive(source="test", external_id="request", service="research-brief", scope=self.scope,
                              customer_email="private-customer@example.invalid")
        drain(self.business)
        quote = self.business.store.one("SELECT id,status FROM quotes")
        self.assertEqual(quote["status"], "offered")
        job = self.business.accept(quote["id"], customer_email="private-customer@example.invalid", evidence_reference="test accepted")
        drain(self.business)
        return job

    def test_public_static_and_projection_do_not_disclose_customer_data(self):
        job = self.order()
        for path in ("/", "/console", "/static/app.js", "/static/style.css"):
            status, headers, body = self.call(path, auth=False)
            self.assertEqual(status, "200 OK")
            self.assertIn("Content-Security-Policy", headers)
            self.assertNotIn(self.token.encode(), body)
        status, _, body = self.call("/api/v1/public", auth=False)
        self.assertEqual(status, "200 OK")
        self.assertNotIn(b"private-customer", body)
        self.assertNotIn(job.encode(), body)
        self.assertNotIn(b"Private customer topic", body)
        self.assertEqual(json.loads(body)["contact_email"], "widemind@example.invalid")
        self.assertEqual(self.call("/api/v1/console", auth=False)[0], "401 Unauthorized")
        self.assertEqual(self.call(f"/api/v1/orders/{job}", auth=False)[0], "401 Unauthorized")
        self.assertEqual(self.call("/static/../host.py", auth=False)[0], "404 Not Found")

    def test_console_is_normalized_without_inline_private_artifacts(self):
        job = self.order()
        status, _, body = self.call("/api/v1/console")
        value = json.loads(body)
        self.assertEqual(status, "200 OK")
        self.assertIsInstance(value["orders"][0]["scope"], dict)
        self.assertIsInstance(value["orders"][0]["payment"], dict)
        self.assertNotIn("artifact", value["orders"][0])
        self.assertEqual(value["orders"][0]["id"], job)
        self.assertTrue(value["uses_fixtures"])
        self.assertTrue(value["controls_available"])

    def test_runtime_controls_can_resume_from_pause_and_deduplicate(self):
        payload = {"state": "paused", "request_id": "pause-1", "evidence_reference": "operator test"}
        self.assertEqual(self.call("/api/v1/runtime", "POST", payload)[0], "200 OK")
        self.assertEqual(self.adapters.state("web-run"), "paused")
        self.assertEqual(self.call("/api/v1/runtime", "POST", payload)[0], "200 OK")
        self.assertEqual(self.call("/api/v1/runtime", "POST", {**payload, "state": "active", "request_id": "resume-1"})[0], "200 OK")
        self.assertEqual(self.adapters.state("web-run"), "active")
        self.assertEqual(self.call("/api/v1/runtime", "POST", {**payload, "state": "invented"})[0], "400 Bad Request")

    def test_failed_authoritative_financial_read_cannot_show_stale_earned_money(self):
        job = self.order()
        self.adapters.set_payment(job, run_id="web-run")
        self.business.notify(source="test", external_id="settled", kind="payment.changed", payload={"id": job})
        drain(self.business)
        self.assertEqual(json.loads(self.business.order(job)["payment"])["earned_micro_usd"], 2000000)
        def unavailable(**kwargs):
            raise OSError("private provider failure")
        self.business.economy.balance = unavailable
        view = financial_summary(self.business)
        self.assertIsNone(view["earned_micro_usd"])
        self.assertIsNone(view["receipts_micro_usd"])
        self.assertEqual(view["source"], "unavailable")

    def test_real_adapter_shaped_accepted_notices_progress_but_delivery_waits(self):
        job = self.order(transport="accepted")
        self.assertEqual(self.business.order(job)["status"], "awaiting_payment")
        self.adapters.set_payment(job, run_id="web-run")
        self.business.notify(source="test", external_id="settled", kind="payment.changed", payload={"id": job})
        drain(self.business)
        self.assertEqual(self.business.order(job)["status"], "ready")
        effect = self.business.store.one("SELECT * FROM effects WHERE action='gmail.send' AND request LIKE '%attachment%'")
        evidence = json.loads(effect["result"])
        evidence["delivery_state"] = "confirmed"
        self.adapters.save(effect["id"], Outcome("succeeded", evidence))
        original = self.adapters.execute
        def read(**kwargs):
            if kwargs["action"] == "gmail.read":
                return Outcome("succeeded", {"messages": [], "delivery_updates": [{"action_id": effect["id"], "job_id": job}]})
            return original(**kwargs)
        self.adapters.execute = read
        self.business.notify(source="test", external_id="acknowledged", kind="gmail.sync", payload={})
        drain(self.business)
        self.assertEqual(self.business.order(job)["status"], "completed")
        self.assertEqual(json.loads(self.business.order(job)["payment"])["earned_micro_usd"], 2000000)

    def test_gmail_pagination_is_scoped_to_each_sync_cycle(self):
        page = [0]
        def read(**kwargs):
            if kwargs["action"] != "gmail.read":
                return Outcome("succeeded", {"message_id": kwargs["action_id"], "delivery_state": "accepted"})
            if kwargs["arguments"].get("cursor") is None:
                page[0] += 1
                return Outcome("succeeded", {"messages": [], "next_cursor": "same-page-token"})
            message = {"message_id": f"request-cycle-{page[0]}", "thread_id": "thread", "from": "private-customer@example.invalid",
                       "sender_verified": True, "command": {"type": "request", "service": "research-brief", "scope": self.scope}}
            return Outcome("succeeded", {"messages": [message], "next_cursor": None})
        self.adapters.execute = read
        for cycle in range(2):
            self.business.notify(source="test", external_id=f"cycle-{cycle}", kind="gmail.sync", payload={})
            drain(self.business)
        self.assertEqual(self.business.store.one("SELECT COUNT(*) AS n FROM opportunities")["n"], 2)

    def test_provider_test_stripe_webhook_uses_signed_handler_without_bearer(self):
        calls = []
        def factory():
            business = self.factory()
            business.execution_profile = "provider-test"
            business.uses_fixtures = False
            def handle(raw, signature):
                if signature != "signed-test-fixture":
                    raise ValueError("invalid fixture signature")
                calls.append(json.loads(raw)["id"])
                return []
            business.economy.handle_webhook = handle
            return business
        app = API(factory, operator_token=self.token)
        event = {"id": "evt-test", "type": "balance.available"}
        self.assertEqual(self.call("/api/v1/stripe/webhook", "POST", event, auth=False, app=app)[0], "400 Bad Request")
        self.assertEqual(self.call("/api/v1/stripe/webhook", "POST", event, auth=False, app=app,
                                   headers={"HTTP_STRIPE_SIGNATURE": "signed-test-fixture"})[0], "200 OK")
        self.assertEqual(calls, ["evt-test"])
        self.assertTrue(self.business.store.one("SELECT 1 FROM events WHERE kind='payments.reconcile'"))
        self.assertEqual(self.call("/api/v1/stripe/webhook", "POST", event, auth=False)[0], "400 Bad Request")

    def test_unsupported_payment_price_fails_before_offer_or_customer_commitment(self):
        from dataclasses import replace
        self.business.economy.validate_price = lambda **kwargs: kwargs["amount_micro_usd"] % 10000 == 0
        self.business.configure(replace(self.business.policy, prices={"research-brief": 1_000_001}))
        opportunity = self.business.receive(source="test", external_id="price", service="research-brief", scope=self.scope,
                                            customer_email="private-customer@example.invalid")
        drain(self.business)
        self.assertEqual(self.business.store.one("SELECT status FROM opportunities WHERE id=?", (opportunity,))["status"], "needs_review")
        self.assertEqual(self.business.store.one("SELECT COUNT(*) AS n FROM quotes")["n"], 0)
        self.assertFalse(any(action == "gmail.send" for _, action in self.adapters.calls))

    def test_late_reconciliation_cannot_downgrade_concurrent_completion(self):
        self.adapters.unknown_actions.add("gmail.send")
        self.business.receive(source="test", external_id="late-effect", service="research-brief", scope=self.scope,
                              customer_email="private-customer@example.invalid")
        drain(self.business)
        effect = self.business.store.one("SELECT * FROM effects WHERE state='unknown'")
        confirmed = {"message_id": "confirmed-provider-reference", "delivery_state": "confirmed"}
        def race(**kwargs):
            self.business.store.db.execute("UPDATE effects SET state='succeeded',result=?,reason='' WHERE id=?", (encode(confirmed), effect["id"]))
            return Outcome("unknown", reason="stale in-progress observer")
        self.adapters.reconcile = race
        self.business._reconcile(effect["id"])
        result = self.business.store.one("SELECT state,result FROM effects WHERE id=?", (effect["id"],))
        self.assertEqual(result["state"], "succeeded")
        self.assertEqual(json.loads(result["result"]), confirmed)

    def test_demo_reseed_never_targets_an_unrelated_customer_order(self):
        from widemind.demo import demonstrate, make_demo
        from widemind.business.store import identity
        directory = self.root / "multi-order-demo"
        original = demonstrate(directory)
        business, adapter = make_demo(directory)
        try:
            seed_quote = business.store.one("SELECT id FROM quotes")["id"]
            for number in range(1000):
                external = f"other-customer-{number}"
                opp = identity(business.run_id, "operator", external)
                quote = identity(business.run_id, "quote", opp)
                order = identity(business.run_id, "order", quote)
                if quote < seed_quote and order < original["order_id"]:
                    break
            else:
                self.fail("could not construct deterministic ordering regression")
            business.receive(source="operator",external_id=external,service="research-brief",scope=self.scope,
                             customer_email="other-customer@example.invalid")
            drain(business)
            other = business.accept(quote,customer_email="other-customer@example.invalid",evidence_reference="explicit fixture acceptance")
            drain(business)
            self.assertEqual(business.order(other)["status"],"awaiting_payment")
        finally:
            business.close()
        repeated = demonstrate(directory)
        self.assertEqual(repeated["order_id"],original["order_id"])
        business, adapter = make_demo(directory)
        try:
            self.assertEqual(json.loads(business.order(other)["payment"])["receipts_micro_usd"],0)
            self.assertEqual(business.order(other)["status"],"awaiting_payment")
        finally:
            business.close()

    def test_research_scope_carries_real_source_urls_without_changing_service(self):
        scope = {**self.scope, "source_urls": ["https://example.com/source", "https://example.org/source"]}
        plan = ResearchBrief().plan(scope, 250000)
        self.assertEqual(plan.arguments["source_urls"], scope["source_urls"])
        with self.assertRaises(ValueError):
            ResearchBrief().plan({**scope, "source_urls": ["http://insecure.invalid"]}, 250000)


if __name__ == "__main__":
    unittest.main()
