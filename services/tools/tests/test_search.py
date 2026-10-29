"""Synthetic provider payloads over actual bounded HTTP and a durable SQLite journal."""
import copy
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import socket
import threading
import time
from urllib.parse import parse_qs, urlsplit

import pytest

from widemind.http import HTTPTransport
from widemind.integrated_service import Journal
from widemind.secrets import SecretStore
from widemind.tools.contracts import ToolContext, ToolError, Unavailable, Uncertain
from widemind.tools.search import SearchAdapter


@pytest.fixture
def search_site(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    state = {"requests": [], "inspect": lambda: {}, "payload": {"results": [
        {"url": "file:///private/owner", "title": "Forbidden"},
        {"url": "http://169.254.169.254/metadata", "title": "Private"},
        {"url": "https://user:secret@example.org/", "title": "Credentials"},
        {"url": "https://evidence.example/article#fragment", "title": "<b>Public evidence</b>",
         "content": "<p>Bounded <em>snippet</em><script>steal()</script></p>", "publishedDate": "2026-10-25T12:00:00Z"},
        {"url": "https://evidence.example/article", "title": "Duplicate"},
        {"url": "https://other.example/source", "title": "Second source", "content": "More evidence", "publishedDate": "yesterday"},
    ]}}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            parsed = urlsplit(self.path)
            state["requests"].append({"path": parsed.path, "params": parse_qs(parsed.query),
                                      "key": self.headers.get("X-Subscription-Token"), "state": state["inspect"]()})
            if parsed.path == "/drop":
                self.close_connection = True
                self.connection.shutdown(socket.SHUT_RDWR)
                return
            status = 302 if parsed.path == "/redirect" else 503 if parsed.path == "/error" else 200
            body = b"not valid JSON" if parsed.path == "/badjson" else b"x" * 2000 if parsed.path == "/oversized" else json.dumps(state["payload"]).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Request-ID", "req_synthetic_search")
            if parsed.path == "/redirect":
                self.send_header("Location", "/searx")
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def settings(origin, *, provider="searxng", path="/searx", **overrides):
    result = {"provider": provider, "endpoint": origin + path, "allow_local_http": True,
              "trusted_fixture_origins": [origin], "max_results": 10,
              "request_microusd": 75 if provider == "brave" else 0,
              "allocation": "Synthetic fixed request tariff" if provider == "brave" else "Owner-subsidized search hosting, compute and upstream traffic",
              "secret_ref": "brave-fixture" if provider == "brave" else None}
    result.update(overrides)
    return result


def context(tmp_path, origin, config=None, *, existing=False):
    config = config or settings(origin)
    journal = Journal(tmp_path / "control")
    if not existing:
        journal.prepare({"actionId": "search-once"}, {"reservationId": "reserved"}, "owner-config-fingerprint")
    secrets = SecretStore(tmp_path / "control")
    if not existing:
        secrets.set("brave-fixture", "synthetic-private-brave-key")
    transport = HTTPTransport({"network": {"trusted_fixture_origins": [origin]}})
    checks = []
    return ToolContext("search-once", "run", "reserved", config, {}, object(), secrets, transport, journal,
                       time.monotonic() + 10, lambda: checks.append(True)), checks


def test_real_searxng_http_returns_bounded_clean_results_and_evidence(tmp_path, search_site):
    origin, state = search_site
    ctx, checks = context(tmp_path, origin)
    state["inspect"] = ctx.load_state
    result = SearchAdapter().execute({"query": "current market evidence", "max_results": 2}, ctx)
    assert result.trust == "untrusted"
    assert len(result.data["results"]) == 2
    assert result.data["results"][0] == {
        "url": "https://evidence.example/article", "title": "Public evidence", "snippet": "Bounded snippet",
        "published_date": "2026-10-25T12:00:00+00:00", "date_basis": "searxng:publishedDate"}
    assert "published_date" not in result.data["results"][1]
    assert result.references[0]["kind"] == "search_response"
    assert result.references[1]["kind"] == "search_result"
    assert result.references[1]["derived_from"] == result.references[0]["source_id"]
    assert result.usage.cost_microusd == 0 and result.usage.details["cost_basis"] == "owner_subsidy"
    assert result.usage.units["search_requests"] == 1 and len(checks) > 4
    assert len(state["requests"]) == 1, "Results are references, never automatic page fetches"
    request = state["requests"][0]
    assert request["params"] == {"q": ["current market evidence"], "format": ["json"], "categories": ["general"], "pageno": ["1"]}
    assert request["key"] is None
    assert request["state"]["external_intent"]["operation"] == "web_search"
    assert ctx.load_state()["search_response"]["body_base64"]


def test_brave_paid_quote_credentials_receipt_and_restart_never_repeat_request(tmp_path, search_site):
    origin, state = search_site
    config = settings(origin, provider="brave", path="/brave")
    state["payload"] = {"web": {"results": [
        {"url": "https://evidence.example/one", "title": "Evidence", "description": "Useful finding", "page_age": "2026-10-22"},
        {"url": "https://evidence.example/two", "title": "Fetched timestamp", "description": "No publication evidence", "page_fetched": "2026-10-28T11:00:00Z"},
    ]}}
    args = {"query": "independent evidence", "max_results": 2}
    quote = SearchAdapter().quote(args, config)
    assert quote.paid and quote.maximum_microusd == 75 and quote.limits["max_requests"] == 1
    ctx, _ = context(tmp_path, origin, config)
    state["inspect"] = ctx.load_state
    result = SearchAdapter().execute(args, ctx)
    assert result.usage.cost_microusd == 75 and result.usage.provisional
    assert result.usage.details["invoice_verified"] is False
    assert result.usage.provider_request_id == "req_synthetic_search"
    assert result.data["results"][0]["date_basis"] == "brave:page_age_unverified"
    assert "published_date" not in result.data["results"][1]
    assert state["requests"][0]["key"] == "synthetic-private-brave-key"
    assert state["requests"][0]["params"]["count"] == ["2"]
    assert state["requests"][0]["state"]["search_usage"]["cost_microusd"] is None
    assert "synthetic-private-brave-key" not in json.dumps(ctx.load_state())
    restarted, _ = context(tmp_path, origin, config, existing=True)
    assert SearchAdapter().execute(args, restarted) == result
    assert SearchAdapter().reconcile(args, restarted).outcome == "completed"
    assert len(state["requests"]) == 1


def test_result_loopback_is_allowed_only_by_exact_owner_fixture_opt_in(tmp_path, search_site):
    origin, state = search_site
    state["payload"] = {"results": [{"url": origin + "/source", "title": "Controlled source"},
                                    {"url": "http://127.0.0.2/source", "title": "Unapproved host"}]}
    ctx, _ = context(tmp_path, origin)
    result = SearchAdapter().execute({"query": "local fixture"}, ctx)
    assert result.data["results"] == [{"url": origin + "/source", "title": "Controlled source", "snippet": ""}]
    assert len(state["requests"]) == 1


@pytest.mark.parametrize("path", ["/badjson", "/error", "/redirect"])
def test_failed_paid_responses_preserve_known_allocation_without_retry(tmp_path, search_site, path):
    origin, state = search_site
    config = settings(origin, provider="brave", path=path)
    ctx, _ = context(tmp_path, origin, config)
    args = {"query": "bounded failure"}
    with pytest.raises(Uncertain) as error:
        SearchAdapter().execute(args, ctx)
    assert error.value.usage.cost_microusd == 75
    assert ctx.load_state()["search_usage"]["provider_request_id"] == "req_synthetic_search"
    restarted, _ = context(tmp_path, origin, config, existing=True)
    reconciled = SearchAdapter().reconcile(args, restarted)
    assert reconciled.outcome == "uncertain" and reconciled.result.usage.cost_microusd == 75
    with pytest.raises(Uncertain):
        SearchAdapter().execute(args, restarted)
    assert len(state["requests"]) == 1


@pytest.mark.parametrize("path,overrides", [("/drop", {}), ("/oversized", {"max_response_bytes": 1024})])
def test_lost_or_unbounded_response_keeps_paid_usage_unknown_and_never_retries(tmp_path, search_site, path, overrides):
    origin, state = search_site
    config = settings(origin, provider="brave", path=path, **overrides)
    ctx, _ = context(tmp_path, origin, config)
    args = {"query": "uncertain search"}
    with pytest.raises(Uncertain) as error:
        SearchAdapter().execute(args, ctx)
    assert error.value.usage.cost_microusd is None
    restarted, _ = context(tmp_path, origin, config, existing=True)
    reconciled = SearchAdapter().reconcile(args, restarted)
    assert reconciled.outcome == "uncertain" and reconciled.result.usage.cost_microusd is None
    with pytest.raises(Uncertain):
        SearchAdapter().execute(args, restarted)
    assert len(state["requests"]) == 1


def test_known_charge_survives_raw_receipt_persistence_failure(tmp_path, search_site, monkeypatch):
    origin, state = search_site
    config = settings(origin, provider="brave")
    ctx, _ = context(tmp_path, origin, config)
    update = ctx.state.update

    def reject_raw(action_id, patch):
        if "search_response" in patch:
            raise ToolError("storage", "Injected bounded raw receipt storage failure")
        return update(action_id, patch)

    monkeypatch.setattr(ctx.state, "update", reject_raw)
    with pytest.raises(Uncertain) as error:
        SearchAdapter().execute({"query": "save measured charge"}, ctx)
    assert error.value.usage.cost_microusd == 75
    restarted, _ = context(tmp_path, origin, config, existing=True)
    saved = restarted.load_state()
    assert saved["search_usage"]["cost_microusd"] == 75
    assert saved["search_response_meta"]["request_id"] == "req_synthetic_search"
    assert "search_response" not in saved
    assert SearchAdapter().reconcile({"query": "save measured charge"}, restarted).result.usage.cost_microusd == 75
    assert len(state["requests"]) == 1


@pytest.mark.parametrize("arguments", [
    {"query": "x", "endpoint": "https://attacker.example"}, {"query": "x", "secret_ref": "other"},
    {"query": "x", "request_microusd": 0}, {"query": "x", "max_results": 11},
    {"query": "x", "max_results": True}, {"query": ""}, {"query": "x" * 401},
    {"query": "header\ninjection"}, {"query": "\ud800"}, {"query": []},
])
def test_argument_overrides_and_invalid_bounds_never_dispatch(tmp_path, search_site, arguments):
    origin, state = search_site
    ctx, _ = context(tmp_path, origin)
    with pytest.raises(ToolError):
        SearchAdapter().execute(arguments, ctx)
    assert state["requests"] == [] and "search_intent" not in ctx.load_state()


def test_missing_configuration_or_key_is_actionable_and_cancellation_is_enforced(tmp_path, search_site):
    origin, state = search_site
    ctx, _ = context(tmp_path, origin)
    assert SearchAdapter().availability({}, ctx.secrets)["available"] is False
    config = settings(origin, provider="brave", secret_ref="missing-dedicated-key")
    assert SearchAdapter().availability(config, ctx.secrets)["available"] is False
    with pytest.raises(Unavailable):
        SearchAdapter().execute({"query": "no credential"}, replace(ctx, config=config))
    with pytest.raises(ToolError) as error:
        SearchAdapter().execute({"query": "cancelled"}, replace(ctx, cancelled=lambda: True))
    assert error.value.code == "cancelled" and state["requests"] == []


def test_output_bound_preserves_paid_usage_and_changed_replay_never_dispatches(tmp_path, search_site):
    origin, state = search_site
    state["payload"] = {"web": {"results": [{"url": "https://evidence.example/source", "title": "Large result", "description": "x" * 3000}]}}
    config = settings(origin, provider="brave", max_output_bytes=1024)
    ctx, _ = context(tmp_path, origin, config)
    with pytest.raises(Uncertain) as error:
        SearchAdapter().execute({"query": "original request"}, ctx)
    assert error.value.usage.cost_microusd == 75
    with pytest.raises(Uncertain):
        SearchAdapter().execute({"query": "changed request"}, ctx)
    assert len(state["requests"]) == 1


@pytest.mark.parametrize("overrides", [
    {"request_microusd": -1}, {"request_microusd": True}, {"allocation": ""},
    {"endpoint": "http://127.0.0.1:80/search", "allow_local_http": False},
    {"endpoint": "https://user:key@example.org/search"}, {"endpoint": "https://example.org/search?q=secret"},
])
def test_unavailable_owner_configuration_is_never_a_successful_stub(search_site, overrides):
    origin, _ = search_site
    with pytest.raises(ToolError):
        SearchAdapter().quote({"query": "fixture"}, settings(origin, **overrides))
