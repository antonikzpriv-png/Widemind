"""Explicit offline provider fixtures with the actual central accounting bridge."""
import base64
import json
from pathlib import Path
import sqlite3

from fastapi.testclient import TestClient
import pytest

from widemind.business_tools.gmail import GmailClient, normalize_message, rfc_message_id
from widemind.business_tools.http import Response, TransportError
from widemind.business_tools.research import OpenAIResearch, ProviderResult
from widemind.business_tools.runtime import Gateway, Settings
from widemind.economy.app import create_app
from widemind.economy.config import Settings as EconomySettings


def response(value, status=200):
    return Response(status, {}, json.dumps(value).encode())


class ProviderFixture:
    """Unexpected calls fail; no external sockets, DNS, money, or mail."""
    def __init__(self):
        self.requests, self.messages = [], []
        self.completion = None
        self.uncertain = False
        self.finish = "stop"
        self.before_post = None

    def request(self, method, url, **kwargs):
        if url == OpenAIResearch.API and self.before_post:
            self.before_post()
        if kwargs.get("before_dispatch"):
            kwargs["before_dispatch"]()
        self.requests.append((method, url))
        if url == GmailClient.TOKEN:
            return response({"access_token": "FIXTURE_ACCESS", "expires_in": 3600})
        if url == GmailClient.API + "/profile":
            return response({"emailAddress": "widemind@example.invalid"})
        if url == GmailClient.API + "/messages/send":
            return response({"id": "fixture-send-1", "threadId": "fixture-thread"})
        if url.startswith(GmailClient.API + "/messages?"):
            if "in%3Asent" in url:
                return response({"messages": [{"id": "fixture-send-1", "threadId": "fixture-thread"}]})
            return response({"messages": [{"id": item["id"]} for item in self.messages]})
        if url.startswith(GmailClient.API + "/messages/"):
            return response(self.messages[0])
        if method == "GET" and url.startswith(OpenAIResearch.API + "?"):
            return response({"data": [self.completion] if self.completion else []})
        if method == "POST" and url == OpenAIResearch.API:
            payload = json.loads(kwargs["body"])
            evidence = json.loads(payload["messages"][1]["content"])
            self.completion = {"id": "chatcmpl_FIXTURE", "metadata": payload["metadata"], "usage": {"prompt_tokens": 100, "completion_tokens": 40, "prompt_tokens_details": {"cached_tokens": 80}},
                "choices": [{"finish_reason": self.finish, "message": {"content": json.dumps({"sections": [{"heading": heading, "text": "Fixture supported statement", "source_urls": [evidence["evidence"][0]["url"]]} for heading in evidence["scope"]["sections"]]})}}]}
            if self.uncertain:
                raise TransportError("fixture lost completion response")
            return response(self.completion)
        if url.startswith("https://example.com/"):
            return Response(200, {"content-type": "text/plain"}, b"Explicit offline source evidence.")
        raise AssertionError("Unexpected provider fixture route")


@pytest.fixture
def setup(tmp_path):
    profiles = {name: {"provider": "openai" if name == "research.collect" else "gmail", "account": "research-account" if name == "research.collect" else "mail-account", "mode": "sandbox"} for name in ("gmail.read", "gmail.send", "research.collect")}
    config = EconomySettings(str(tmp_path / "economy.sqlite"), "operator-" + "o" * 48, "gateway-" + "g" * 48,
        "agent-" + "a" * 48, "run", runtime_auth_secret="proof-" + "p" * 48, runtime_tool_profiles=profiles)
    app = create_app(config)
    env = {"WIDEMIND_BUSINESS_TOOLS_PROFILE": "provider-test", "WIDEMIND_BUSINESS_TOOLS_JOURNAL": str(tmp_path / "tools.sqlite"),
        "WIDEMIND_BUSINESS_STATE_DB": str(tmp_path / "business.sqlite"), "WIDEMIND_ECONOMY_URL": "http://127.0.0.1:8080", "WIDEMIND_GATEWAY_TOKEN": config.gateway_token,
        "WIDEMIND_BUSINESS_ALLOWED_RECIPIENTS": '["customer@example.invalid"]', "WIDEMIND_GMAIL_ADDRESS": "widemind@example.invalid",
        "GMAIL_CLIENT_ID": "FIXTURE_CLIENT", "GMAIL_CLIENT_SECRET": "FIXTURE_SECRET", "GMAIL_REFRESH_TOKEN": "FIXTURE_REFRESH",
        "WIDEMIND_BUSINESS_GMAIL_ACCOUNT": "mail-account", "WIDEMIND_BUSINESS_OPENAI_ACCOUNT": "research-account", "OPENAI_API_KEY": "FIXTURE_OPENAI_KEY", "OPENAI_MODEL": "fixture-model",
        "OPENAI_INPUT_MICRO_USD_PER_MILLION_TOKENS": "400000", "OPENAI_OUTPUT_MICRO_USD_PER_MILLION_TOKENS": "1600000", "OPENAI_CACHED_INPUT_MICRO_USD_PER_MILLION_TOKENS": "100000",
        "WIDEMIND_RESEARCH_SOURCE_URLS": '["https://example.com/a","https://example.com/b"]'}
    scope = {"topic": "Fixture topic", "sections": ["Overview"], "minSources": 2}
    with sqlite3.connect(env["WIDEMIND_BUSINESS_STATE_DB"]) as db:
        db.executescript("""CREATE TABLE runtime_runs(id TEXT,desired TEXT,state TEXT,revision INTEGER);
          CREATE TABLE runtime_events(id INTEGER PRIMARY KEY,run_id TEXT,kind TEXT,at INTEGER,detail TEXT);
          CREATE TABLE runtime_actions(id TEXT,run_id TEXT,action_json TEXT);
          CREATE TABLE business_settings(run_id TEXT,document TEXT);
          CREATE TABLE business_orders(id TEXT,run_id TEXT,quote_id TEXT,status TEXT,price_micros TEXT,cost_micros TEXT,scope TEXT);
          CREATE TABLE business_quotes(id TEXT,scope TEXT,price_micros TEXT,cost_micros TEXT);""")
        db.execute("INSERT INTO runtime_runs VALUES ('run','run','active',1)")
        db.execute("INSERT INTO runtime_events(run_id,kind,at,detail) VALUES ('run','operator.resume',1,'fixture initialization')")
        db.execute("INSERT INTO business_settings VALUES ('run',?)", (json.dumps({"capabilities": ["research"], "maxJobMicros": "250000"}),))
        db.execute("INSERT INTO business_orders VALUES ('job','run','quote','creating','2000000','250000',?)", (json.dumps(scope),))
        db.execute("INSERT INTO business_quotes VALUES ('quote',?,'2000000','250000')", (json.dumps(scope),))
    provider = ProviderFixture()
    payment = {"runId": "run", "jobId": "job", "mode": "sandbox", "currency": "USD", "priceMicros": "2000000", "paymentStatus": "settled", "receiptsMicros": "2000000", "refundsMicros": "0", "chargebacksMicros": "0", "chargebackReversalsMicros": "0", "pendingLiabilityMicros": "0"}
    with TestClient(app) as client:
        def post(route, body, role="gateway"):
            answer = client.post("/v1/runtime/" + route, json=body, headers={"Authorization": "Bearer " + getattr(config, role + "_token")})
            assert answer.status_code == 200, answer.text
            return answer.json()
        post("seed", {"runId": "run", "mode": "sandbox"}, "operator")
        def gateway(changes=None):
            settings = Settings({**env, **(changes or {})})
            original = settings.clients
            def clients(transport, tool, prepared=None):
                gmail, research = original(transport, tool, prepared)
                if research:
                    research.resolver = lambda host, port, **kwargs: [(2, 1, 6, "", ("93.184.216.34", port))]
                return gmail, research
            settings.clients = clients
            return Gateway(settings, verify=lambda request: post("verify", request), transport=provider, payment=lambda request: payment,
                           authorization=lambda request: post("authorize", request))
        def request(tool="research.collect", action_id="action", input=None):
            limit = {"maximum": {"currency": "USD", "micros": "250000" if tool == "research.collect" else "0"}, "maxDurationMs": 120000, "maxOutputBytes": 1000000, "maxTokens": 30000}
            action = {"version": 1, "runId": "run", "actionId": action_id, "workKey": action_id, "tool": tool, "input": input if input is not None else scope, "limit": limit, "jobId": "job"}
            with sqlite3.connect(env["WIDEMIND_BUSINESS_STATE_DB"]) as db:
                db.execute("INSERT INTO runtime_actions VALUES (?,?,?)", (action_id, "run", json.dumps(action)))
            reservation = post("authorize", {key: action[key] for key in ("runId", "actionId", "tool", "limit")})["reservation"]
            return action, reservation
        yield locals()


def invoke(gateway, operation, action, reservation):
    try:
        return gateway.call(operation, action, reservation)
    finally:
        gateway.close()


def test_real_central_verification_one_dispatch_receipt_settlement_and_replay(setup):
    action, reservation = setup["request"]()
    result = invoke(setup["gateway"](), "execute", action, reservation)
    assert result["kind"] == "receipt"
    assert result["receipt"]["usage"]["actualCharge"]["micros"] == "80"
    assert result["receipt"]["result"]["artifact"]["checks"]["citations"] is True
    assert result["receipt"]["result"]["executionStartedAt"].endswith("Z")
    assert setup["post"]("settle", {"reservation": reservation, "receipt": result["receipt"]})["kind"] == "settled"
    replay = invoke(setup["gateway"](), "execute", action, reservation)
    assert replay == result
    with setup["app"].state.economy._read() as db:
        assert db.execute("SELECT COUNT(*) FROM runtime_dispatches").fetchone()[0] == 1
    assert len([r for r in setup["provider"].requests if r == ("POST", OpenAIResearch.API)]) == 1


def test_actual_business_selected_candidate_fits_real_prepare_and_central_gate(setup):
    # Captured from BusinessService.selectWork's creating-order regression.
    candidate = json.loads((Path(__file__).parent / "fixtures/business_candidate.json").read_text())
    action, reservation = setup["request"](input=candidate["input"])
    assert action["limit"] == candidate["limit"]
    action["workKey"] = candidate["workKey"]
    with sqlite3.connect(setup["env"]["WIDEMIND_BUSINESS_STATE_DB"]) as db:
        db.execute("UPDATE runtime_actions SET action_json=? WHERE id=?", (json.dumps(action), action["actionId"]))
        db.execute("UPDATE business_orders SET scope=?", (json.dumps(candidate["input"]),))
        db.execute("UPDATE business_quotes SET scope=?", (json.dumps(candidate["input"]),))
    result = invoke(setup["gateway"](), "execute", action, reservation)
    assert result["receipt"]["status"] == "succeeded"
    assert result["receipt"]["result"]["artifact"]["checks"]["citations"] is True


def test_uncertain_charge_stays_held_then_recovers_at_original_prices(setup):
    action, reservation = setup["request"]()
    setup["provider"].uncertain = True
    assert invoke(setup["gateway"](), "execute", action, reservation)["kind"] == "unknown"
    with sqlite3.connect(setup["env"]["WIDEMIND_BUSINESS_TOOLS_JOURNAL"]) as db:
        original_started = json.loads(db.execute("SELECT context FROM actions WHERE id=?", (action["actionId"],)).fetchone()[0])["executionStartedAt"]
    summary = setup["post"]("summary", {"runId": "run"})
    assert summary["reservedMicros"] == "250000" and summary["operatingChargesMicros"] == "0"
    recovered = invoke(setup["gateway"]({"OPENAI_INPUT_MICRO_USD_PER_MILLION_TOKENS": "1", "OPENAI_OUTPUT_MICRO_USD_PER_MILLION_TOKENS": "1", "OPENAI_CACHED_INPUT_MICRO_USD_PER_MILLION_TOKENS": "0"}), "reconcile", action, reservation)
    assert recovered["receipt"]["usage"]["actualCharge"]["micros"] == "80"
    assert recovered["receipt"]["result"]["executionStartedAt"] == original_started
    assert len([r for r in setup["provider"].requests if r == ("POST", OpenAIResearch.API)]) == 1


def test_lost_journal_cannot_zero_settle_or_repeat_an_existing_central_dispatch(setup):
    action, reservation = setup["request"]()
    setup["provider"].uncertain = True
    assert invoke(setup["gateway"](), "execute", action, reservation)["kind"] == "unknown"
    # Simulate an inconsistent restore: the control ledger survived, tool intent did not.
    with sqlite3.connect(setup["env"]["WIDEMIND_BUSINESS_TOOLS_JOURNAL"]) as db:
        db.execute("DELETE FROM actions")
    setup["payment"]["refundsMicros"] = "1"
    assert invoke(setup["gateway"](), "execute", action, reservation)["kind"] == "unknown"
    summary = setup["post"]("summary", {"runId": "run"})
    assert summary["reservedMicros"] == "250000" and summary["operatingChargesMicros"] == "0"
    assert len([r for r in setup["provider"].requests if r == ("POST", OpenAIResearch.API)]) == 1


def test_late_order_refund_blocks_inference_even_with_other_wallet_cash(setup):
    action, reservation = setup["request"]()
    setup["provider"].before_post = lambda: setup["payment"].update(refundsMicros="1")
    result = invoke(setup["gateway"](), "execute", action, reservation)
    assert result["receipt"]["status"] == "failed"
    assert result["receipt"]["usage"]["actualCharge"]["micros"] == "0"
    assert ("POST", OpenAIResearch.API) not in setup["provider"].requests


def test_known_bill_survives_bad_output_and_final_quality_failure(setup, monkeypatch):
    action, reservation = setup["request"]()
    # Exercise failure in the independent final renderer after usage was known.
    from widemind.business_tools.validation import ResearchBrief
    original = ResearchBrief.deliverable
    calls = []
    def render(self, scope, result):
        calls.append(True)
        if len(calls) > 1:
            raise ValueError("offline artifact review rejection")
        return original(self, scope, result)
    monkeypatch.setattr(ResearchBrief, "deliverable", render)
    result = invoke(setup["gateway"](), "execute", action, reservation)
    assert result["receipt"]["status"] == "failed"
    assert result["receipt"]["usage"]["actualCharge"]["micros"] == "80"
    assert setup["post"]("settle", {"reservation": reservation, "receipt": result["receipt"]})["kind"] == "settled"


def test_stop_then_resume_during_preparation_invalidates_old_execution(setup):
    action, reservation = setup["request"]()
    def stop_resume():
        with sqlite3.connect(setup["env"]["WIDEMIND_BUSINESS_STATE_DB"]) as db:
            db.execute("UPDATE runtime_runs SET revision=revision+2")
            db.execute("INSERT INTO runtime_events(run_id,kind,at,detail) VALUES ('run','operator.stop',2,'fixture stop')")
            db.execute("INSERT INTO runtime_events(run_id,kind,at,detail) VALUES ('run','operator.resume',3,'fixture resume')")
    setup["provider"].before_post = stop_resume
    result = invoke(setup["gateway"](), "execute", action, reservation)
    assert result["receipt"]["status"] == "failed"
    assert ("POST", OpenAIResearch.API) not in setup["provider"].requests


@pytest.mark.parametrize("kind", ["inbox.received", "operator.wake"])
def test_ordinary_incoming_event_or_wake_does_not_cancel_authorized_research(setup, kind):
    action, reservation = setup["request"]()
    def incoming_event():
        with sqlite3.connect(setup["env"]["WIDEMIND_BUSINESS_STATE_DB"]) as db:
            db.execute("UPDATE runtime_runs SET revision=revision+1")
            db.execute("INSERT INTO runtime_events(run_id,kind,at,detail) VALUES ('run',?,2,'ordinary fixture notification')", (kind,))
    setup["provider"].before_post = incoming_event
    result = invoke(setup["gateway"](), "execute", action, reservation)
    assert result["kind"] == "receipt" and result["receipt"]["status"] == "succeeded"
    assert result["receipt"]["usage"]["actualCharge"]["micros"] == "80"
    assert setup["post"]("settle", {"reservation": reservation, "receipt": result["receipt"]})["kind"] == "settled"
    assert setup["post"]("summary", {"runId": "run"})["operatingChargesMicros"] == "80"
    assert len([r for r in setup["provider"].requests if r == ("POST", OpenAIResearch.API)]) == 1
    with setup["app"].state.economy._read() as db:
        assert db.execute("SELECT COUNT(*) FROM runtime_dispatches").fetchone()[0] == 1


def test_forged_reservation_never_contacts_provider(setup):
    action, reservation = setup["request"]()
    reservation["authorization"] = "fake"
    with pytest.raises(ValueError):
        invoke(setup["gateway"](), "execute", action, reservation)
    assert setup["provider"].requests == []


def test_daily_attempt_quota_is_durable_and_replay_does_not_dispatch(setup):
    action, reservation = setup["request"]()
    first = invoke(setup["gateway"]({"WIDEMIND_BUSINESS_RESEARCH_DAILY_LIMIT": "1"}), "execute", action, reservation)
    assert first["kind"] == "receipt"
    other, other_reservation = setup["request"](action_id="second")
    denied = invoke(setup["gateway"]({"WIDEMIND_BUSINESS_RESEARCH_DAILY_LIMIT": "1"}), "execute", other, other_reservation)
    assert denied["receipt"]["status"] == "failed" and denied["receipt"]["usage"]["actualCharge"]["micros"] == "0"
    assert len([r for r in setup["provider"].requests if r == ("POST", OpenAIResearch.API)]) == 1
    assert invoke(setup["gateway"]({"WIDEMIND_BUSINESS_RESEARCH_DAILY_LIMIT": "1"}), "execute", action, reservation) == first


def test_real_authenticated_reply_confirms_only_bound_message(setup):
    action, reservation = setup["request"]("gmail.send", "mail", {"to": "customer@example.invalid", "subject": "Private scoped work", "body": "Fixture body", "attachment": {"filename": "fixture.md", "mediaType": "text/markdown", "content": "Explicit fixture content"}})
    sent = invoke(setup["gateway"](), "execute", action, reservation)
    assert sent["receipt"]["result"]["deliveryState"] == "accepted"
    body = base64.urlsafe_b64encode(b'{"type":"acknowledge","action_id":"mail"}').decode()
    setup["provider"].messages = [{"id": "incoming", "threadId": "thread", "payload": {"mimeType": "text/plain", "body": {"data": body}, "headers": [{"name": "From", "value": "customer@example.invalid"}, {"name": "Authentication-Results", "value": "mx.google.com; dmarc=pass header.from=example.invalid"}, {"name": "In-Reply-To", "value": rfc_message_id("mail")}]}}]
    action, reservation = setup["request"]("gmail.read", "read", {})
    result = invoke(setup["gateway"](), "execute", action, reservation)
    assert result["receipt"]["result"]["deliveryUpdates"][0]["actionId"] == "mail"
    headers = setup["provider"].messages[0]["payload"]["headers"]
    headers[1]["value"] = "mx.google.com; dmarc=fail ( dmarc=pass ) header.from=example.invalid"
    assert normalize_message(setup["provider"].messages[0])["sender_verified"] is False


@pytest.mark.parametrize("command,attachment", [
    ({"type": "clarification", "text": "The attachment is missing"}, True),
    ({"type": "opt_out"}, True),
    ({"type": "acknowledge", "action_id": "other"}, True),
    ({"type": "acknowledge"}, True),
    ({"type": "acknowledge", "action_id": "mail", "actionId": "other"}, True),
    ({"type": "acknowledge", "action_id": "mail"}, False),
])
def test_negative_or_unscoped_reply_never_confirms_attachment_delivery(setup, command, attachment):
    arguments = {"to": "customer@example.invalid", "subject": "Private scoped work", "body": "Fixture body"}
    if attachment:
        arguments["attachment"] = {"filename": "fixture.md", "mediaType": "text/markdown", "content": "Explicit fixture content"}
    action, reservation = setup["request"]("gmail.send", "mail", arguments)
    invoke(setup["gateway"](), "execute", action, reservation)
    setup["provider"].messages = [{"id": "incoming", "threadId": "thread", "payload": {"mimeType": "text/plain", "body": {"data": base64.urlsafe_b64encode(json.dumps(command).encode()).decode()}, "headers": [{"name": "From", "value": "customer@example.invalid"}, {"name": "Authentication-Results", "value": "mx.google.com; dmarc=pass header.from=example.invalid"}, {"name": "In-Reply-To", "value": rfc_message_id("mail")}]}}]
    action, reservation = setup["request"]("gmail.read", "read", {})
    result = invoke(setup["gateway"](), "execute", action, reservation)
    assert result["receipt"]["result"]["deliveryUpdates"] == []
