"""Synthetic fixtures matching pinned official openai-python raw API types.

These are controlled contract tests, not recordings or evidence of live inference.
"""
import base64
import copy
import http.client
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import pytest

from widemind.tools.ai import AIAdapter, PLAN_SCHEMA
from widemind.tools.contracts import HTTPResponse, ToolContext, ToolError, Unavailable, Uncertain


def config(**overrides):
    result = {"model": "configured-text-model", "secret_ref": "openai-test", "model_context_tokens": 1000,
              "max_output_tokens": 100, "max_input_bytes": 32_000, "max_output_bytes": 10_000,
              "tariff": {"input_microusd_per_million_tokens": 123_456,
                         "output_microusd_per_million_tokens": 654_321}, "maximum_microusd": 100_000}
    result.update(overrides)
    return result


def text_response(text="Useful content", **overrides):
    result = {"id": "resp_test", "created_at": 0, "object": "response", "model": "configured-text-model",
              "status": "completed", "tools": [], "output": [{"type": "message", "id": "msg_test",
                  "role": "assistant", "status": "completed", "content": [{"type": "output_text", "text": text, "annotations": []}]}],
              "usage": {"input_tokens": 11, "output_tokens": 7, "total_tokens": 18,
                        "input_tokens_details": {"cached_tokens": 0}, "output_tokens_details": {"reasoning_tokens": 0}}}
    result.update(overrides)
    return result


class MemoryState:
    def __init__(self):
        self.data = {}
        self.events = []

    def load(self, action):
        return copy.deepcopy(self.data)

    def update(self, action, patch):
        self.data.update(copy.deepcopy(patch))
        self.events.append(("state", set(patch)))

    def dispatched(self, action, intent):
        self.events.append(("dispatched", copy.deepcopy(intent)))
        self.data["dispatched"] = True


class MemorySecrets:
    def __init__(self, values=None):
        self.values = {"openai-test": "synthetic-test-key"} if values is None else values

    def get(self, name):
        return self.values.get(name)


class MemoryWorkspace:
    root = "/workspace/dedicated-test-only"

    def __init__(self):
        self.files = {}
        self.writes = 0

    def read_bytes(self, path, *, max_bytes=8_000_000):
        if path not in self.files:
            raise FileNotFoundError(path)
        data = self.files[path]
        assert len(data) <= max_bytes
        return data

    def write_bytes(self, path, data, *, overwrite=False):
        if path in self.files and not overwrite:
            raise FileExistsError(path)
        self.files[path] = data
        self.writes += 1
        return {"path": path, "bytes": len(data)}


class FixtureTransport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []
        self.context = None

    def request(self, method, url, **kwargs):
        if kwargs.get("cancel"):
            kwargs["cancel"]()
        self.calls.append((method, url, kwargs))
        if method == "POST":
            assert self.context.state.data.get("dispatched"), "intent must be durable before paid HTTP"
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        if callable(response):
            response = response(self.context)
        if isinstance(response, HTTPResponse):
            return response
        return HTTPResponse(200, {"x-request-id": "req_synthetic"}, json.dumps(response).encode(), url)


def context(provider_config=None, transport=None, *, state=None, secrets=None, workspace=None, cancelled=lambda: False):
    transport = transport or FixtureTransport(text_response())
    ctx = ToolContext("synthetic-action", "synthetic-run", "synthetic-reservation", provider_config or config(), {},
                      workspace or MemoryWorkspace(), secrets or MemorySecrets(), transport, state or MemoryState(),
                      time.monotonic() + 20, lambda: None, cancelled)
    transport.context = ctx
    return ctx


def test_exact_integer_quote_and_actual_usage():
    adapter = AIAdapter()
    quote = adapter.quote({"prompt": "write a brief"}, config())
    assert quote.paid and quote.maximum_microusd == (1000 * 123456 + 100 * 654321 + 999999) // 1000000
    ctx = context()
    result = adapter.execute({"prompt": "write a brief"}, ctx)
    assert result.data["text"] == "Useful content"
    assert result.usage.cost_microusd == (11 * 123456 + 7 * 654321 + 999999) // 1000000
    assert result.usage.provisional and result.trust == "untrusted"
    assert result.usage.provider_request_id == "req_synthetic"
    assert ctx.state.data["ai_response_id"] == "resp_test"
    sent = json.loads(ctx.transport.calls[0][2]["body"])
    assert sent["max_output_tokens"] == 100 and sent["tools"] == [] and sent["store"] is True


def test_plan_is_structured_untrusted_task_data():
    plan = {"objective": "Sell a useful brief", "steps": [{"description": "Research a need", "suggested_tool": "web.search", "success_criterion": "Evidence found"}], "risks": []}
    ctx = context(transport=FixtureTransport(text_response(json.dumps(plan))))
    result = AIAdapter().execute({"prompt": "Plan a business action", "purpose": "plan"}, ctx)
    assert result.data["json"] == plan and result.trust == "untrusted"
    sent = json.loads(ctx.transport.calls[0][2]["body"])
    assert sent["text"]["format"] == {"type": "json_schema", "name": "widemind_output", "schema": PLAN_SCHEMA, "strict": True}


def test_schema_applied_to_response_and_paid_usage_retained():
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"], "additionalProperties": False}
    ctx = context(transport=FixtureTransport(text_response('{"ok":true,"maximum_microusd":999999999}')))
    with pytest.raises(Uncertain) as failure:
        AIAdapter().execute({"prompt": "make JSON", "format": "json", "schema": schema}, ctx)
    assert failure.value.usage.cost_microusd > 0
    assert "ai_response_body" in ctx.state.data and "ai_usage" in ctx.state.data
    assert ctx.config["maximum_microusd"] == 100_000


@pytest.mark.parametrize("arguments", [
    {"prompt": "x", "secret_ref": "attacker"}, {"prompt": "x", "model": "other"},
    {"prompt": "x", "max_output_tokens": True}, {"prompt": "x", "max_output_tokens": 101},
    {"prompt": "x", "max_cost_microusd": 1}, {"prompt": "x", "format": "json", "schema": {"type": "object"}},
    {"prompt": "x", "format": "json", "schema": {"type": "object", "$ref": "https://external/schema"}},
    {"prompt": "x" * 32_001}, {"prompt": "x", "purpose": "unknown"}])
def test_invalid_or_unbounded_inputs_never_dispatch(arguments):
    ctx = context()
    with pytest.raises(ToolError):
        AIAdapter().execute(arguments, ctx)
    assert not ctx.transport.calls and not ctx.state.data.get("dispatched")


@pytest.mark.parametrize("arguments", [{"prompt": "\ud800"}, {"prompt": "x", "purpose": []},
    {"prompt": "x", "format": []}, {"prompt": "x", "format": "json", "schema": {"type": []}},
    {"prompt": "x", "format": "json", "schema": {"type": "object", "properties": {"x": {"type": "boolean"}}, "required": [1], "additionalProperties": False}}])
def test_malformed_types_fail_as_tool_errors_before_dispatch(arguments):
    ctx = context()
    with pytest.raises(ToolError):
        AIAdapter().execute(arguments, ctx)
    assert not ctx.transport.calls


def test_missing_provider_configuration_and_secret_actionable():
    adapter = AIAdapter()
    assert not adapter.availability({}, MemorySecrets())["available"]
    status = adapter.availability(config(), MemorySecrets({}))
    assert not status["available"] and "secret" in status["reason"]
    with pytest.raises(Unavailable):
        adapter.execute({"prompt": "x"}, context(secrets=MemorySecrets({})))


@pytest.mark.parametrize("usage", [None, {"input_tokens": 1, "output_tokens": 2}, {"input_tokens": 1, "output_tokens": 2, "total_tokens": 9}])
def test_missing_or_invalid_usage_is_unknown_liability(usage):
    ctx = context(transport=FixtureTransport(text_response(usage=usage)))
    with pytest.raises(Uncertain) as failure:
        AIAdapter().execute({"prompt": "x"}, ctx)
    assert failure.value.usage.cost_microusd is None
    assert ctx.state.data["ai_usage"]["cost_microusd"] is None
    assert ctx.state.data["ai_response_id"] == "resp_test"


def test_timeout_does_not_repeat_paid_request():
    ctx = context(transport=FixtureTransport(TimeoutError("synthetic timeout")))
    adapter = AIAdapter()
    for _ in range(2):
        with pytest.raises(Uncertain):
            adapter.execute({"prompt": "x"}, ctx)
    assert len(ctx.transport.calls) == 1
    assert adapter.reconcile({"prompt": "x"}, ctx).outcome == "uncertain"


def test_saved_raw_response_recovers_without_http():
    ctx = context()
    adapter = AIAdapter()
    first = adapter.execute({"prompt": "x"}, ctx)
    second = adapter.execute({"prompt": "x"}, ctx)
    assert first == second and len(ctx.transport.calls) == 1


def test_known_response_id_retrieved_once_without_post():
    state = MemoryState()
    state.data = {"ai_response_id": "resp_known", "ai_intent": {"operation": "responses.create"}}
    ctx = context(state=state)
    result = AIAdapter().reconcile({"prompt": "x"}, ctx)
    assert result.outcome == "completed"
    assert ctx.transport.calls[0][0] == "GET" and ctx.transport.calls[0][1].endswith("/responses/resp_known")


def test_retrieval_can_recover_missing_usage_without_another_paid_post():
    state = MemoryState()
    payload = text_response(usage=None)
    state.data = {"ai_response_id": "resp_test", "ai_response_body": base64.b64encode(json.dumps(payload).encode()).decode()}
    ctx = context(state=state)
    recovery = AIAdapter().reconcile({"prompt": "x"}, ctx)
    assert recovery.outcome == "completed" and recovery.result.usage.cost_microusd > 0
    assert [call[0] for call in ctx.transport.calls] == ["GET"]


def test_reconciliation_keeps_known_charge_for_invalid_output():
    ctx = context(transport=FixtureTransport(text_response("too big")))
    adapter = AIAdapter()
    with pytest.raises(Uncertain):
        adapter.execute({"prompt": "x", "max_output_bytes": 1}, ctx)
    recovery = adapter.reconcile({"prompt": "x", "max_output_bytes": 1}, ctx)
    assert recovery.outcome == "uncertain" and recovery.result.usage.cost_microusd > 0
    assert len(ctx.transport.calls) == 1


def test_incomplete_saved_response_is_retrieved():
    payload = text_response(status="in_progress")
    state = MemoryState()
    state.data = {"ai_response_id": "resp_test", "ai_response_body": base64.b64encode(json.dumps(payload).encode()).decode()}
    ctx = context(state=state)
    assert AIAdapter().reconcile({"prompt": "x"}, ctx).outcome == "completed"
    assert [call[0] for call in ctx.transport.calls] == ["GET"]


def test_refusal_and_output_limit_preserve_usage_and_receipt():
    for payload in [text_response("too much output"), text_response(output=[{"type": "message", "content": [{"type": "refusal", "refusal": "no"}]}])]:
        ctx = context(transport=FixtureTransport(payload))
        with pytest.raises(Uncertain) as failure:
            AIAdapter().execute({"prompt": "x", "max_output_bytes": 3}, ctx)
        assert failure.value.usage.cost_microusd > 0 and ctx.state.data["ai_response_body"]


def test_cancel_before_dispatch_and_after_response():
    ctx = context(cancelled=lambda: True)
    with pytest.raises(ToolError, match="cancelled"):
        AIAdapter().execute({"prompt": "x"}, ctx)
    assert not ctx.transport.calls
    cancelled = [False]

    def response(ctx):
        cancelled[0] = True
        return text_response()

    ctx = context(transport=FixtureTransport(response), cancelled=lambda: cancelled[0])
    with pytest.raises(Uncertain) as failure:
        AIAdapter().execute({"prompt": "x"}, ctx)
    assert failure.value.usage.cost_microusd > 0
    assert ctx.state.data["ai_response_body"]


def test_http_error_receipt_and_invalid_json_retained():
    for payload in [HTTPResponse(429, {"x-request-id": "req_rate"}, b'{"error":{"message":"rate limit"}}', "x"),
                    HTTPResponse(200, {"x-request-id": "req_bad"}, b'not json', "x")]:
        ctx = context(transport=FixtureTransport(payload))
        with pytest.raises(Uncertain):
            AIAdapter().execute({"prompt": "x"}, ctx)
        assert ctx.state.data["ai_response_body"] and ctx.state.data["ai_request_id"]
        assert len(ctx.transport.calls) == 1


def test_owner_cache_write_rate_is_used_conservatively():
    cfg = config(tariff={"input_microusd_per_million_tokens": 1, "output_microusd_per_million_tokens": 1,
                         "cache_write_microusd_per_million_tokens": 2_000_000})
    result = AIAdapter().execute({"prompt": "x"}, context(cfg))
    assert result.usage.cost_microusd == 23  # 11*2 USD micro units plus tiny output rounded up


class LocalHTTPTransport:
    """Controlled localhost transport only; production gateway owns real transport."""
    def __init__(self):
        self.context = None
        self.calls = []

    def request(self, method, url, *, headers=None, body=None, timeout=30, max_bytes=2_000_000, cancel=None):
        parts = urlsplit(url)
        assert parts.hostname == "127.0.0.1" and parts.scheme == "http"
        if cancel:
            cancel()
        self.calls.append((method, url))
        connection = http.client.HTTPConnection(parts.hostname, parts.port, timeout=timeout)
        try:
            connection.request(method, parts.path, body, headers or {})
            response = connection.getresponse()
            payload = response.read(max_bytes + 1)
            if len(payload) > max_bytes:
                raise ValueError("bounded HTTP response exceeded")
            return HTTPResponse(response.status, {k.lower(): v for k, v in response.getheaders()}, payload, url)
        finally:
            connection.close()


def test_controlled_http_raw_responses_contract():
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
            payload = json.dumps(text_response()).encode()
            self.send_response(200)
            self.send_header("x-request-id", "req_local")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        cfg = config(endpoint=f"http://127.0.0.1:{server.server_port}/v1", allow_local_http=True)
        result = AIAdapter().execute({"prompt": "controlled fixture"}, context(cfg, LocalHTTPTransport()))
        assert result.usage.provider_request_id == "req_local" and received[0][0] == "/v1/responses"
        assert received[0][1]["input"] == "controlled fixture"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def journal_context(tmp_path, cfg, payload, arguments, tool):
    """Use the integrated SQLite journal, never a lookalike state-size fake."""
    control = pytest.importorskip("widemind.control", reason="SQLite JournalState is supplied by the integrated gateway")
    store = control.ControlStore(tmp_path / "control")
    store.create_run("synthetic-run")
    adapter_quote = tool.quote(arguments, cfg)
    store.prepare(action_id="synthetic-action", run_id="synthetic-run", job_id=None,
                  tool=tool.spec.name, version=1, arguments=arguments, config=cfg, policy={}, quote=adapter_quote)
    claim = store.claim("synthetic-action", 120)
    journal = control.JournalState(store, claim, "synthetic-action")
    # This valid preexisting state makes a bounded large raw receipt exceed the
    # real 32,000,000-byte serialized journal limit after base64 expansion.
    journal.update("synthetic-action", {"existing_state": "x" * 12_100_000})
    receipt = json.dumps(payload).encode()
    assert len(receipt) < 16_000_000

    class JournalReceiptTransport:
        context = None

        def request(self, method, url, **kwargs):
            assert store.get("synthetic-action")["dispatched"] == 1
            return HTTPResponse(200, {"x-request-id": "req_bounded_journal"}, receipt, url)

    ctx = context(cfg, JournalReceiptTransport())
    ctx.state = journal
    ctx.deadline = time.monotonic() + 120
    ctx.authorize = lambda: store.authorize("synthetic-action", claim)
    return ctx, store


def test_actual_32mb_journal_keeps_identity_and_charge_before_raw_overflow(tmp_path):
    arguments = {"prompt": "x"}
    cfg = config(max_response_bytes=16_000_000)
    payload = text_response(extra_provider_data="x" * 15_000_000)
    ctx, store = journal_context(tmp_path, cfg, payload, arguments, AIAdapter())
    with pytest.raises(Uncertain) as failure:
        AIAdapter().execute(arguments, ctx)
    assert failure.value.usage.cost_microusd > 0
    saved = ctx.load_state()
    assert saved["ai_response_id"] == "resp_test" and saved["ai_request_id"] == "req_bounded_journal"
    assert saved["ai_usage"]["cost_microusd"] == failure.value.usage.cost_microusd
    assert "ai_response_body" not in saved
    # Affirm this is the actual journal's 32 MB bound, with transactional failure.
    with pytest.raises(ToolError, match="size limit"):
        ctx.save_state({"overflow": "x" * 20_000_000})
    assert store.get("synthetic-action")["dispatched"] == 1


def test_receipt_limit_and_compact_usage_prevent_state_amplification():
    with pytest.raises(Unavailable):
        AIAdapter().quote({"prompt": "x"}, config(max_response_bytes=16_000_001))
    payload = text_response()
    payload["usage"]["unexpected_provider_field"] = "x" * 100_000
    ctx = context(transport=FixtureTransport(payload))
    result = AIAdapter().execute({"prompt": "x"}, ctx)
    assert len(json.dumps(result.usage.details)) < 2000
    assert "unexpected_provider_field" not in result.usage.details["provider_usage"]
    assert ctx.state.data["ai_response_body"]
