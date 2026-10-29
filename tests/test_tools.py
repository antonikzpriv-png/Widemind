"""EXPLICIT TEST FIXTURES: no live email, model calls, source requests, or money."""
import base64
from dataclasses import replace
from email import message_from_bytes
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest

from widemind.business.contracts import Outcome
from widemind.tools import GmailClient, GmailConfig, LiveTools, OpenAIResearch, ResearchConfig
from widemind.tools.gmail import normalize_message, rfc_message_id
from widemind.tools.http import HTTPS, Response, TransportError
from widemind.tools.research import public_source


def response(data, status=200, **headers):
    return Response(status, headers, json.dumps(data).encode())


def public_dns(host, port, **kwargs):
    return [(2, 1, 6, "", ("93.184.216.34", port))]


class FixtureRuntime:
    mode = "test"
    def __init__(self):
        self.current = "active"
        self.generation = 0
        self.disabled = set()
    def state(self, run_id):
        return self.current
    def capability_enabled(self, run_id, action):
        return action not in self.disabled
    def cancelled(self, run_id):
        return self.current == "stopped"
    def cancellation_generation(self, run_id):
        return self.generation


class FixtureEconomy:
    mode = "test"
    def __init__(self):
        self.authorizations = []
        self.settlements = []
        self.deny = False
        self.fail_settlement = False
        self.releases = []
    def begin_execution(self, **kwargs):
        self.authorizations.append(kwargs)
        return Outcome("failed" if self.deny else "succeeded", kwargs)
    def release(self, **kwargs):
        self.releases.append(kwargs)
        return Outcome("succeeded", {"released": True})
    def settle_execution(self, **kwargs):
        self.settlements.append(kwargs)
        if self.fail_settlement:
            raise ValueError("explicit fixture settlement unavailable")
        return Outcome("succeeded", {"charged_micro_usd": kwargs["actual_micro_usd"]})


class FixtureTransport:
    """A canned in-memory transport; unexpected requests fail instead of networking."""
    def __init__(self):
        self.requests = []
        self.send_uncertain = False
        self.openai_uncertain = False
        self.openai_finish = "stop"
        self.openai_status = 200
        self.cached_tokens = 0
        self.inbox = []
        self.after_source = None
        self.profile_email = "widemind@example.invalid"
        self.openai_payload = None
        self.stored_completion = None
    def request(self, method, url, **kwargs):
        self.requests.append((method, url, kwargs))
        if url == GmailClient.TOKEN:
            return response({"access_token": "TEST_ONLY_ACCESS_TOKEN", "expires_in": 3600})
        if url == GmailClient.API + "/profile":
            return response({"emailAddress": self.profile_email})
        if url == GmailClient.API + "/messages/send":
            if self.send_uncertain:
                raise TransportError("explicit fixture lost send response")
            return response({"id": "fixture-sent-1", "threadId": "fixture-thread-1"})
        if url.startswith(GmailClient.API + "/messages?"):
            if "in%3Asent" in url:
                return response({"messages": [{"id": "fixture-sent-1", "threadId": "fixture-thread-1"}]})
            return response({"messages": [{"id": item["id"]} for item in self.inbox]})
        if url.startswith(GmailClient.API + "/messages/"):
            return response(next(item for item in self.inbox if "/" + item["id"] + "?" in url))
        if method == "GET" and url.startswith(OpenAIResearch.API + "?"):
            return response({"data": [self.stored_completion] if self.stored_completion else []})
        if url == OpenAIResearch.API:
            self.openai_payload = json.loads(kwargs["body"])
            data = json.loads(self.openai_payload["messages"][1]["content"])
            sources = data["evidence"]
            completion = {"id": "chatcmpl_TEST_ONLY", "metadata": self.openai_payload["metadata"],
                          "usage": {"prompt_tokens": 100, "completion_tokens": 40, "prompt_tokens_details": {"cached_tokens": self.cached_tokens}},
                          "choices": [{"finish_reason": self.openai_finish, "message": {"content": json.dumps({"sections": [
                              {"heading": h, "text": "TEST source-supported statement", "source_urls": [sources[0]["url"]]}
                              for h in data["scope"]["sections"]]})}}]}
            self.stored_completion = completion
            if self.openai_uncertain:
                raise TransportError("explicit fixture lost completion response")
            return response(completion, status=self.openai_status)
        if method == "GET" and url.startswith("https://example.com/"):
            if self.after_source:
                self.after_source()
            return Response(200, {"content-type": "text/html; charset=utf-8"}, b"<title>TEST evidence</title><script>untrusted hidden text</script><p>Observed source fact for testing.</p>")
        raise AssertionError("unexpected fixture request: " + method + " " + url)


def inbox_message(message_id="fixture-inbound", *, sender="customer@example.invalid", auth=True, command=None, reply_to=None):
    headers = [{"name": "From", "value": "Customer <" + sender + ">"}]
    if auth:
        headers.append({"name": "Authentication-Results", "value": "mx.google.com; dkim=pass header.i=@example.invalid; dmarc=pass header.from=example.invalid"})
    if reply_to:
        headers.append({"name": "In-Reply-To", "value": reply_to})
    raw = json.dumps(command or {"type": "request", "service": "research-brief", "scope": {"topic": "TEST", "sections": ["Overview"]}}).encode()
    return {"id": message_id, "threadId": "fixture-thread", "payload": {"mimeType": "text/plain", "headers": headers,
            "body": {"data": base64.urlsafe_b64encode(raw).decode().rstrip("=")}}}


class ToolsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.transport = FixtureTransport()
        self.runtime, self.economy = FixtureRuntime(), FixtureEconomy()
        self.gmail = GmailClient(GmailConfig("widemind@example.invalid", "TEST_CLIENT", "TEST_SECRET", "TEST_REFRESH"), transport=self.transport)
        self.research = OpenAIResearch(ResearchConfig("TEST_OPENAI_KEY", input_micro_usd_per_million_tokens=400000,
            output_micro_usd_per_million_tokens=1600000, cached_input_micro_usd_per_million_tokens=100000,
            source_urls=("https://example.com/a", "https://example.com/b")),
                                      transport=self.transport, resolver=public_dns)
        self.path = Path(self.temp.name) / "protected-tools.sqlite3"
        self.tools = LiveTools(self.path, economy=self.economy, runtime=self.runtime, gmail=self.gmail,
                               research=self.research, mode="test")
        self.arguments = {"topic": "TEST topic", "sections": ["Overview", "Risks"], "min_sources": 2}
    def tearDown(self):
        self.tools.close()
        self.temp.cleanup()
    def execute(self, action="research.collect", action_id="TEST_ACTION", arguments=None, **changes):
        params = dict(run_id="TEST_RUN", job_id="TEST_JOB", action_id=action_id, action=action,
            arguments=self.arguments if arguments is None else arguments, reservation_id="TEST_RESERVATION" if action == "research.collect" else None,
            maximum_micro_usd=250000 if action == "research.collect" else 0)
        params.update(changes)
        return self.tools.execute(**params)
    def mail(self, action_id="TEST_MAIL"):
        return self.execute("gmail.send", action_id, {"to": "customer@example.invalid", "subject": "TEST offer", "body": "TEST private scoped offer"})
    def model_calls(self):
        return sum(method == "POST" and url == OpenAIResearch.API for method, url, _ in self.transport.requests)

    def test_real_research_path_collects_evidence_checks_scope_and_posts_actual_usage(self):
        result = self.execute()
        self.assertEqual(result.state, "succeeded")
        self.assertEqual(len(result.data["sources"]), 2)
        self.assertIn("Observed source fact", result.data["sources"][0]["excerpt"])
        self.assertNotIn("untrusted hidden", result.data["sources"][0]["excerpt"])
        self.assertEqual([s["heading"] for s in result.data["sections"]], self.arguments["sections"])
        self.assertEqual(self.economy.authorizations[0]["action_id"], "TEST_ACTION")
        self.assertEqual(self.economy.settlements[0]["actual_micro_usd"], 104)
        self.assertEqual(self.transport.openai_payload["max_completion_tokens"], 1800)
        self.assertNotIn("tools", self.transport.openai_payload)
        self.assertEqual(self.model_calls(), 1)

    def test_paid_failure_remains_charged_and_cannot_reexecute(self):
        self.transport.openai_finish = "length"
        result = self.execute()
        self.assertEqual(result.state, "failed")
        self.assertEqual(self.economy.settlements[0]["actual_micro_usd"], 104)
        self.assertEqual(self.execute().state, "failed")
        self.assertEqual(self.model_calls(), 1)

    def test_unknown_completion_retains_reservation_then_recovers_without_another_call(self):
        self.transport.openai_uncertain = True
        self.assertEqual(self.execute().state, "unknown")
        self.assertEqual(self.economy.settlements, [])
        self.assertEqual(self.execute().state, "unknown")
        self.assertEqual(self.model_calls(), 1)
        self.assertEqual(self.tools.reconcile(action_id="TEST_ACTION").state, "succeeded")
        self.assertEqual(self.economy.settlements[0]["actual_micro_usd"], 104)
        self.assertEqual(self.model_calls(), 1)
        self.assertEqual(sum(method == "GET" and url.startswith("https://example.com") for method, url, _ in self.transport.requests), 2)

    def test_settlement_failure_preserves_provider_evidence_for_restart(self):
        self.economy.fail_settlement = True
        self.assertEqual(self.execute().state, "unknown")
        row = self.tools._row("TEST_ACTION")
        self.assertEqual(row["actual_micro_usd"], 104)
        self.assertEqual(row["settlement_pending"], 1)
        self.tools.close()
        self.tools = LiveTools(self.path, economy=self.economy, runtime=self.runtime, gmail=self.gmail, research=self.research, mode="test")
        self.economy.fail_settlement = False
        self.assertEqual(self.tools.reconcile(action_id="TEST_ACTION").state, "succeeded")
        self.assertEqual(self.model_calls(), 1)

    def test_denied_funds_and_runtime_state_never_dispatch_model(self):
        self.economy.deny = True
        self.assertEqual(self.execute().state, "failed")
        self.assertEqual(self.transport.requests, [])
        self.runtime.current = "paused"
        self.assertEqual(self.execute(action_id="PAUSED").state, "failed")
        self.assertEqual(self.transport.requests, [])

    def test_stop_then_resume_between_sources_and_inference_cancels_the_attempt(self):
        self.transport.after_source = lambda: setattr(self.runtime, "generation", 1)
        self.assertEqual(self.execute().state, "failed")
        self.assertEqual(self.model_calls(), 0)
        self.assertEqual(self.economy.settlements[0]["actual_micro_usd"], 0)

    def test_full_payload_schema_is_included_in_cost_and_input_bounds(self):
        long_urls = ["https://example.com/" + ("a" * 1950), "https://example.com/" + ("b" * 1950)]
        client = OpenAIResearch(replace(self.research.config, max_input_bytes=8000), transport=self.transport, resolver=public_dns)
        # Messages alone fit; repeated URL enums in the response schema make the
        # full request exceed the cap. It must fail before paid dispatch.
        self.tools.research = client
        result = self.execute(arguments={**self.arguments, "source_urls": long_urls})
        self.assertEqual(result.state, "failed")
        self.assertEqual(self.model_calls(), 0)
        self.assertEqual(self.economy.settlements[0]["actual_micro_usd"], 0)

    def test_private_source_and_redirect_are_never_followed(self):
        for url in ("https://127.0.0.1/a", "https://localhost/a", "https://example.com:8443/a"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                public_source(url, resolver=lambda *a, **kw: [(2, 1, 6, "", ("127.0.0.1", 443))])
        self.research.resolver = lambda *a, **kw: [(2, 1, 6, "", ("10.0.0.2", 443))]
        self.assertEqual(self.execute().state, "failed")
        self.assertEqual(self.transport.requests, [])

    def test_mail_send_is_accepted_not_delivered_and_ids_are_stable(self):
        result = self.mail()
        self.assertEqual(result.state, "succeeded")
        self.assertEqual(result.data["delivery_state"], "accepted")
        self.assertEqual(self.mail(), result)
        sends = [kwargs for method, url, kwargs in self.transport.requests if method == "POST" and url.endswith("/messages/send")]
        self.assertEqual(len(sends), 1)
        raw = json.loads(sends[0]["body"])["raw"]
        message = message_from_bytes(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
        self.assertEqual(message["Message-ID"], rfc_message_id("TEST_MAIL"))
        self.assertEqual(message["From"], "widemind@example.invalid")
        self.assertNotIn("TEST_SECRET", message.as_string())

    def test_mail_timeout_reconciles_sent_folder_without_resending_or_claiming_delivery(self):
        self.transport.send_uncertain = True
        self.assertEqual(self.mail().state, "unknown")
        result = self.tools.reconcile(action_id="TEST_MAIL")
        self.assertEqual(result.state, "succeeded")
        self.assertEqual(result.data["delivery_state"], "accepted")
        self.assertEqual(sum(method == "POST" and url.endswith("/messages/send") for method, url, _ in self.transport.requests), 1)

    def test_oauth_identity_mismatch_never_sends(self):
        self.transport.profile_email = "unrelated@example.invalid"
        self.assertEqual(self.mail().state, "failed")
        self.assertFalse(any(url.endswith("/messages/send") for _, url, _ in self.transport.requests))

    def test_oauth_failure_is_a_known_unsent_failure_and_can_retry_after_fix(self):
        original = self.transport.request
        def bad_oauth(method, url, **kwargs):
            if url == GmailClient.TOKEN:
                return response({"error":"invalid_grant"},status=401)
            return original(method,url,**kwargs)
        self.transport.request = bad_oauth
        self.assertEqual(self.mail("OAUTH_FAILURE").state,"failed")
        self.assertFalse(any(url.endswith("/messages/send") for _,url,_ in self.transport.requests))
        self.transport.request = original
        self.assertEqual(self.mail("EXPLICIT_NEW_ATTEMPT").state,"succeeded")
        self.assertEqual(sum(url.endswith("/messages/send") for _,url,_ in self.transport.requests),1)

    def test_death_before_mail_dispatch_is_reconciled_without_send_or_search(self):
        original = self.transport.request
        def interrupted(method,url,**kwargs):
            if url == GmailClient.API + "/profile":
                raise KeyboardInterrupt("explicit process-death fixture during preflight")
            return original(method,url,**kwargs)
        self.transport.request = interrupted
        with self.assertRaises(KeyboardInterrupt):
            self.mail("INTERRUPTED_MAIL_PREFLIGHT")
        self.transport.request = original
        before = len(self.transport.requests)
        result = self.tools.reconcile(action_id="INTERRUPTED_MAIL_PREFLIGHT")
        self.assertEqual(result.state,"failed")
        self.assertEqual(len(self.transport.requests),before)

    def test_sender_authentication_does_not_trust_bare_from_or_injected_lower_auth_results(self):
        good = normalize_message(inbox_message())
        self.assertTrue(good["sender_verified"])
        self.assertFalse(normalize_message(inbox_message(auth=False))["sender_verified"])
        bad = inbox_message()
        bad["payload"]["headers"].insert(1, {"name": "Authentication-Results", "value": "external.example; dmarc=pass header.from=example.invalid"})
        self.assertFalse(normalize_message(bad)["sender_verified"])
        wrong = inbox_message(sender="other@unrelated.invalid")
        self.assertFalse(normalize_message(wrong)["sender_verified"])

    def test_authenticated_reply_updates_only_matching_recipient_delivery(self):
        self.mail()
        self.transport.inbox = [inbox_message(command={"type": "accept", "quote_id": "TEST_QUOTE"}, reply_to=rfc_message_id("TEST_MAIL"))]
        result = self.execute("gmail.read", "TEST_READ", {})
        self.assertEqual(result.state, "succeeded")
        self.assertEqual(result.data["delivery_updates"], [{"action_id": "TEST_MAIL", "job_id": "TEST_JOB", "delivery_state": "confirmed"}])
        self.assertEqual(self.tools.reconcile(action_id="TEST_MAIL").data["delivery_state"], "confirmed")
        self.assertEqual(sum(method == "POST" and url.endswith("/messages/send") for method, url, _ in self.transport.requests), 1)

    def test_unverified_or_wrong_recipient_acknowledgment_cannot_confirm(self):
        self.mail()
        for index, msg in enumerate((inbox_message(auth=False, reply_to=rfc_message_id("TEST_MAIL")),
                                     inbox_message(sender="other@example.invalid", reply_to=rfc_message_id("TEST_MAIL")))):
            self.transport.inbox = [msg]
            self.assertEqual(self.execute("gmail.read", "READ" + str(index), {}).data["delivery_updates"], [])
        self.assertEqual(self.tools.reconcile(action_id="TEST_MAIL").data["delivery_state"], "accepted")
        with self.assertRaises(ValueError):
            self.tools.confirm_delivery(action_id="TEST_MAIL", recipient_email="other@example.invalid", evidence_reference="not actual recipient")

    def test_action_identity_conflict_and_mode_mismatch_fail_closed(self):
        self.mail()
        with self.assertRaises(ValueError):
            self.execute("gmail.send", "TEST_MAIL", {"to": "other@example.invalid", "subject": "changed", "body": "changed"})
        self.tools.close()
        self.runtime.mode = "live"
        self.economy.mode = "live"
        with self.assertRaises(ValueError):
            LiveTools(self.path, economy=self.economy, runtime=self.runtime, mode="live")
        self.runtime.mode = "test"
        self.economy.mode = "test"
        self.tools = LiveTools(self.path, economy=self.economy, runtime=self.runtime, gmail=self.gmail, research=self.research, mode="test")

    def test_recipient_allowlist_and_control_plane_capability_are_enforced(self):
        self.tools.allowed_recipients = frozenset({"controlled@example.invalid"})
        self.assertEqual(self.mail().state, "failed")
        self.assertEqual(self.transport.requests, [])
        self.runtime.disabled.add("research.collect")
        self.assertEqual(self.execute().state, "failed")
        self.assertEqual(self.economy.authorizations, [])

    def test_authorization_is_bound_to_exact_run_and_job_before_any_provider_call(self):
        def swapped(**kwargs):
            return Outcome("succeeded", {**kwargs, "run_id": "UNRELATED_RUN", "job_id": "UNRELATED_JOB"})
        self.economy.begin_execution = swapped
        self.assertEqual(self.execute().state, "failed")
        self.assertEqual(self.transport.requests, [])
        self.assertEqual(self.economy.settlements, [])

    def test_unlisted_source_host_is_rejected_in_local_readiness_without_network(self):
        result = self.tools.validate_arguments("research.collect", {**self.arguments, "source_urls": ["https://unapproved.example.org/a", "https://unapproved.example.org/b"]})
        self.assertFalse(result["ready"])
        self.assertEqual(self.transport.requests, [])

    def test_attachment_contains_a_usable_actual_action_receipt_instruction(self):
        attachment = {"filename": "report.txt", "media_type": "text/plain", "content": "TEST deliverable", "checks": {"scope": True}}
        self.execute("gmail.send", "DELIVERY_ACTION", {"to": "customer@example.invalid", "subject": "TEST delivery", "body": "TEST body", "attachment": attachment})
        sent = next(kwargs for method, url, kwargs in self.transport.requests if method == "POST" and url.endswith("/messages/send"))
        raw = json.loads(sent["body"])["raw"]
        message = message_from_bytes(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
        body = next(part for part in message.walk() if part.get_content_type() == "text/plain" and part.get_content_disposition() != "attachment").get_payload(decode=True).decode()
        self.assertIn('"action_id":"DELIVERY_ACTION"', body)
        self.assertIn('"type":"acknowledge"', body)
        self.assertEqual(message["Message-ID"], rfc_message_id("DELIVERY_ACTION"))

    def test_daily_quota_stops_new_actions_before_account_access(self):
        self.tools.max_gmail_actions_per_day = 1
        self.assertEqual(self.mail().state, "succeeded")
        count = len(self.transport.requests)
        result = self.mail("SECOND_MAIL")
        self.assertEqual(result.state, "failed")
        self.assertIn("quota", result.reason)
        self.assertEqual(len(self.transport.requests), count)
        self.assertEqual(self.mail().state, "succeeded")  # Replaying a fact is free.

    def test_cached_input_usage_uses_configured_cached_rate_not_full_input_price(self):
        self.transport.cached_tokens = 80
        self.assertEqual(self.execute().state, "succeeded")
        self.assertEqual(self.economy.settlements[0]["actual_micro_usd"], 80)
        self.assertEqual(self.transport.openai_payload["service_tier"], "default")

    def test_slow_drip_response_hits_whole_request_deadline(self):
        now = [0.0]
        class SlowDrip:
            status = 200
            headers = {}
            reads = 0
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def read(self, count):
                self.reads += 1
                now[0] += 0.6
                return b"x"
            read1 = read
        drip = SlowDrip()
        class FixtureOpener:
            def open(self, request, timeout):
                return drip
        http = HTTPS(clock=lambda: now[0])
        http.opener = FixtureOpener()
        with self.assertRaises(TransportError):
            http.request("GET", "https://example.com/slow", timeout=1, maximum_bytes=1000)
        self.assertEqual(drip.reads, 2)

    def test_death_after_intent_before_authorization_releases_without_dispatch(self):
        original = self.economy.begin_execution
        def die(**kwargs):
            raise KeyboardInterrupt("TEST process died before economic claim")
        self.economy.begin_execution = die
        with self.assertRaises(KeyboardInterrupt):
            self.execute()
        row = self.tools._row("TEST_ACTION")
        self.assertIsNone(row["context"])
        self.economy.begin_execution = original
        result = self.tools.reconcile(action_id="TEST_ACTION")
        self.assertEqual(result.state, "failed")
        self.assertEqual(len(self.economy.releases), 1)
        self.assertEqual(self.economy.releases[0]["run_id"], "TEST_RUN")
        self.assertEqual(self.economy.releases[0]["job_id"], "TEST_JOB")
        self.assertEqual(self.economy.authorizations, [])
        self.assertEqual(self.economy.settlements, [])
        self.assertEqual(self.transport.requests, [])

    def test_concurrent_reconcile_cannot_abort_an_active_preparation(self):
        entered, resume = threading.Event(), threading.Event()
        original_prepare = self.research.prepare
        def hold(*args, **kwargs):
            entered.set()
            if not resume.wait(2):
                raise AssertionError("TEST barrier timed out")
            return original_prepare(*args, **kwargs)
        self.research.prepare = hold
        results = []
        def worker():
            gateway = LiveTools(self.path, economy=self.economy, runtime=self.runtime, gmail=self.gmail,
                                research=self.research, mode="test")
            try:
                results.append(gateway.execute(run_id="TEST_RUN", job_id="TEST_JOB", action_id="ACTIVE_ACTION",
                    action="research.collect", arguments=self.arguments, reservation_id="TEST_RESERVATION", maximum_micro_usd=250000))
            finally:
                gateway.close()
        thread = threading.Thread(target=worker)
        thread.start()
        try:
            self.assertTrue(entered.wait(2))
            self.assertIsNone(self.tools._row("ACTIVE_ACTION")["context"])
            concurrent = self.tools.reconcile(action_id="ACTIVE_ACTION")
            self.assertEqual(concurrent.state, "unknown")
            self.assertIn("currently executing", concurrent.reason)
            self.assertEqual(self.economy.releases, [])
            self.assertEqual(self.economy.settlements, [])
        finally:
            resume.set()
            thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(results[0].state, "succeeded")
        self.assertEqual(self.economy.settlements[-1]["actual_micro_usd"], 104)
        self.assertEqual(self.model_calls(), 1)
        self.assertEqual(self.tools.reconcile(action_id="ACTIVE_ACTION").state, "succeeded")

    def test_funding_revocation_during_preparation_is_rechecked_before_paid_post(self):
        original_prepare = self.research.prepare
        def revoke(*args, **kwargs):
            prepared = original_prepare(*args, **kwargs)
            self.economy.deny = True  # Fixture refund/breach invalidates coverage.
            return prepared
        self.research.prepare = revoke
        result = self.execute()
        self.assertEqual(result.state, "failed")
        self.assertEqual(len(self.economy.authorizations), 2)
        self.assertEqual(self.model_calls(), 0)
        self.assertEqual(self.economy.settlements[-1]["actual_micro_usd"], 0)

    def test_provider_error_with_explicit_usage_does_not_become_free_failure(self):
        self.transport.openai_status = 429
        result = self.execute()
        self.assertEqual(result.state, "failed")
        self.assertEqual(self.economy.settlements[0]["actual_micro_usd"], 104)
        self.assertEqual(self.economy.settlements[0]["evidence_reference"], "chatcmpl_TEST_ONLY")

    def test_credentials_do_not_appear_in_journal_or_config_repr(self):
        self.execute()
        self.mail()
        contents = "\n".join(str(tuple(row)) for row in self.tools.db.execute("SELECT * FROM tool_actions"))
        for secret in ("TEST_SECRET", "TEST_REFRESH", "TEST_ACCESS_TOKEN", "TEST_OPENAI_KEY"):
            self.assertNotIn(secret, contents)
            self.assertNotIn(secret, repr(self.gmail.config))
            self.assertNotIn(secret, repr(self.research.config))


if __name__ == "__main__":
    unittest.main()
