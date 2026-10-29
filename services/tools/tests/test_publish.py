"""Controlled HTTP publication tests: no external publishing or live accounts."""
import copy
import hashlib
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from widemind.tools.contracts import HTTPResponse, ToolError, Unavailable, Uncertain
from widemind.tools.publish import PublishAdapter

from test_ai import FixtureTransport, LocalHTTPTransport, MemorySecrets, context


CONTENT = b"<p>Controlled publishing fixture</p>"


def config(**overrides):
    result = {"base_url": "https://publishing.example/dedicated", "public_base_url": "https://public.example/artifacts",
              "sandbox": True, "paid": False, "publication_microusd": 0,
              "hosting_allocation": "Owner-prepaid fixture hosting; separate subsidy, no variable per-request charges",
              "secret_ref": "openai-test", "suffix": ".html", "content_type": "text/html; charset=utf-8",
              "max_artifact_bytes": 1024}
    result.update(overrides)
    return result


def setup_ctx(cfg=None, transport=None):
    ctx = context(cfg or config(), transport or FixtureTransport(
        HTTPResponse(201, {"etag": '"fixture"'}, b"", "x"),
        HTTPResponse(200, {"x-request-id": "read_fixture"}, CONTENT, "x")))
    ctx.policy = {"profile": "sandbox", "allow_external_effects": True}
    ctx.workspace.files["site/page.html"] = CONTENT
    return ctx


def test_conditional_create_then_verified_digest_and_owner_public_mapping():
    ctx = setup_ctx()
    result = PublishAdapter().execute({"artifact_path": "site/page.html"}, ctx)
    assert result.data["verified"] and result.data["sha256"] == hashlib.sha256(CONTENT).hexdigest()
    assert result.data["url"].startswith("https://public.example/artifacts/")
    assert result.data["visibility"] == "sandbox" and result.trust == "untrusted"
    assert result.usage.cost_microusd == 0
    assert [call[0] for call in ctx.transport.calls] == ["PUT", "GET"]
    put = ctx.transport.calls[0]
    assert put[2]["headers"]["If-None-Match"] == "*" and put[2]["body"] == CONTENT
    assert ctx.state.data["publish_intent"]["sha256"] == result.data["sha256"]
    assert ctx.state.data["publish_put_receipt"]["status"] == 201


def test_explicit_fixed_paid_allocation_and_quote():
    cfg = config(paid=True, publication_microusd=57)
    adapter = PublishAdapter()
    quote = adapter.quote({"artifact_path": "site/page.html"}, cfg)
    assert quote.paid and quote.maximum_microusd == 57
    result = adapter.execute({"artifact_path": "site/page.html"}, setup_ctx(cfg))
    assert result.usage.cost_microusd == 57 and result.usage.provisional


@pytest.mark.parametrize("cfg", [config(paid=True, publication_microusd=0), config(publication_microusd=1),
    config(hosting_allocation=""), config(base_url="https://user:secret@example.test/a"),
    config(base_url="https://example.test/a?token=x"), config(base_url="https://example.test/%2e%2e/a"),
    config(base_url="http://external.example/test"), config(content_type="text/html\r\nAuthorization: other")])
def test_invalid_configuration_unavailable_independently(cfg):
    adapter = PublishAdapter()
    assert not adapter.availability(cfg, MemorySecrets())["available"]
    with pytest.raises(Unavailable):
        adapter.quote({"artifact_path": "site/page.html"}, cfg)


@pytest.mark.parametrize("arguments", [{"artifact_path": "../secret"}, {"artifact_path": "/host"},
    {"artifact_path": "site/page.html", "url": "https://attacker.example"},
    {"artifact_path": "site/page.html", "secret_ref": "other"}, {"artifact_path": "a//b"}])
def test_only_workspace_path_no_task_configuration(arguments):
    ctx = setup_ctx()
    with pytest.raises(ToolError):
        PublishAdapter().execute(arguments, ctx)
    assert not ctx.transport.calls


@pytest.mark.parametrize("policy,cfg", [({"profile": "sandbox"}, config()),
    ({"profile": "production", "allow_external_effects": False}, config(sandbox=False)),
    ({"profile": "development", "allow_external_effects": True}, config(sandbox=False)),
    ({"profile": "sandbox", "allow_external_effects": True}, config(sandbox=False))])
def test_owner_policy_required_before_external_effects(policy, cfg):
    ctx = setup_ctx(cfg)
    ctx.policy = policy
    with pytest.raises(ToolError, match="requires|require"):
        PublishAdapter().execute({"artifact_path": "site/page.html"}, ctx)
    assert not ctx.transport.calls and not ctx.state.data.get("dispatched")


def test_unknown_request_no_second_put_and_404_never_proves_absence():
    ctx = setup_ctx(transport=FixtureTransport(TimeoutError("after dispatch"),
        HTTPResponse(404, {}, b"", "x"), HTTPResponse(404, {}, b"", "x")))
    adapter = PublishAdapter()
    with pytest.raises(Uncertain):
        adapter.execute({"artifact_path": "site/page.html"}, ctx)
    with pytest.raises(Uncertain):
        adapter.execute({"artifact_path": "site/page.html"}, ctx)
    assert adapter.reconcile({"artifact_path": "site/page.html"}, ctx).outcome == "uncertain"
    assert [call[0] for call in ctx.transport.calls] == ["PUT", "GET", "GET"]


def test_config_and_content_changes_reject_immutable_intent():
    ctx = setup_ctx(transport=FixtureTransport(TimeoutError("after dispatch")))
    adapter = PublishAdapter()
    with pytest.raises(Uncertain):
        adapter.execute({"artifact_path": "site/page.html"}, ctx)
    initial = copy.deepcopy(ctx.config)
    ctx.config = config(base_url="https://different.example/base")
    with pytest.raises(ToolError, match="changed"):
        adapter.execute({"artifact_path": "site/page.html"}, ctx)
    assert adapter.reconcile({"artifact_path": "site/page.html"}, ctx).outcome == "uncertain"
    ctx.config = initial
    ctx.workspace.files["site/page.html"] = b"Changed"
    with pytest.raises(ToolError, match="changed"):
        adapter.execute({"artifact_path": "site/page.html"}, ctx)
    assert len(ctx.transport.calls) == 1


def test_conflicting_get_never_overwritten_and_private_receipt_no_unconfigured_url():
    ctx = setup_ctx(config(public_base_url=None), FixtureTransport(
        HTTPResponse(412, {}, b"", "x"), HTTPResponse(200, {}, b"unrelated existing object", "x")))
    with pytest.raises(Uncertain):
        PublishAdapter().execute({"artifact_path": "site/page.html"}, ctx)
    assert [call[0] for call in ctx.transport.calls] == ["PUT", "GET"]
    ctx = setup_ctx(config(public_base_url=None))
    result = PublishAdapter().execute({"artifact_path": "site/page.html"}, ctx)
    assert "url" not in result.data and result.data["visibility"] == "sandbox"


def test_cancel_and_bounds_before_dispatch():
    ctx = setup_ctx(config(max_artifact_bytes=1))
    with pytest.raises((ToolError, AssertionError)):
        PublishAdapter().execute({"artifact_path": "site/page.html"}, ctx)
    assert not ctx.transport.calls
    ctx = setup_ctx()
    ctx.cancelled = lambda: True
    with pytest.raises(ToolError):
        PublishAdapter().execute({"artifact_path": "site/page.html"}, ctx)
    assert not ctx.transport.calls


def test_local_http_commit_then_drop_response_recovery_has_zero_duplicate_put():
    objects = {}
    methods = []
    ctx_holder = []

    class Handler(BaseHTTPRequestHandler):
        def do_PUT(self):
            methods.append("PUT")
            assert ctx_holder[0].state.data.get("dispatched")
            assert ctx_holder[0].state.data.get("publish_intent")
            assert self.headers.get("If-None-Match") == "*"
            assert self.path not in objects
            objects[self.path] = self.rfile.read(int(self.headers["Content-Length"]))
            self.connection.shutdown(socket.SHUT_RDWR)
            self.connection.close()

        def do_GET(self):
            methods.append("GET")
            data = objects.get(self.path)
            self.send_response(200 if data is not None else 404)
            self.send_header("Content-Length", str(len(data or b"")))
            self.end_headers()
            self.wfile.write(data or b"")

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        cfg = config(base_url=f"http://127.0.0.1:{server.server_port}/dedicated", public_base_url=None, allow_local_http=True)
        ctx = setup_ctx(cfg, LocalHTTPTransport())
        ctx_holder.append(ctx)
        adapter = PublishAdapter()
        with pytest.raises(Uncertain):
            adapter.execute({"artifact_path": "site/page.html"}, ctx)
        assert methods == ["PUT"] and len(objects) == 1
        # A fresh adapter/context with only persisted state simulates restart.
        restarted = setup_ctx(cfg, LocalHTTPTransport())
        restarted.state = ctx.state
        result = PublishAdapter().reconcile({"artifact_path": "site/page.html"}, restarted)
        assert result.outcome == "completed" and result.result.data["verified"]
        assert methods == ["PUT", "GET"] and len(objects) == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
