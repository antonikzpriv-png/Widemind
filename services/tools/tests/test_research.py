import hashlib
import time

import pytest

from widemind.tools.contracts import HTTPResponse, ToolError
from widemind.tools.research import ResearchAdapter, URLPolicy
from test_research_support import PAGE, context, fixture_site


def test_real_http_extracts_bounded_untrusted_evidence(tmp_path, fixture_site):
    origin, hits = fixture_site
    ctx, checks = context(tmp_path, origin)
    result = ResearchAdapter().execute({"url": origin + "/page"}, ctx)
    assert result.trust == "untrusted"
    assert result.data["title"] == "Fixture & evidence"
    assert "Research fixture" in result.data["text"]
    assert "document.querySelector" not in result.data["text"]
    assert "display:none" not in result.data["text"]
    assert result.data["links"] == [{"url": origin + "/next", "text": "Next evidence"}]
    assert result.references[0]["sha256"] == hashlib.sha256(PAGE).hexdigest()
    assert result.references[0]["retrieved_at"].endswith("+00:00")
    assert result.references[0]["trust"] == "untrusted"
    assert result.usage.cost_microusd == 0
    assert result.usage.details["cost_basis"] == "owner_subsidy"
    assert result.usage.units["response_bytes"] == len(PAGE)
    assert hits == ["/page"] and len(checks) >= 5


def test_same_origin_redirect_is_explicit_and_bounded(tmp_path, fixture_site):
    origin, hits = fixture_site
    ctx, _ = context(tmp_path, origin)
    result = ResearchAdapter().execute({"url": origin + "/redirect"}, ctx)
    assert result.data["url"] == origin + "/page"
    assert result.data["redirects"] == [origin + "/redirect"]
    assert result.usage.units["http_requests"] == 2
    assert hits == ["/redirect", "/page"]


@pytest.mark.parametrize("route, code", [
    ("redirect-private", "unsafe_url"), ("redirect-cross", "unsafe_redirect"),
    ("redirect-loop", "redirect_limit"), ("binary", "unsupported_content"),
])
def test_rejects_unsafe_redirects_and_downloads_before_following(tmp_path, fixture_site, route, code):
    origin, hits = fixture_site
    ctx, _ = context(tmp_path, origin)
    with pytest.raises(ToolError) as error:
        ResearchAdapter().execute({"url": origin + "/" + route}, ctx)
    assert error.value.code == code
    assert hits == ["/" + route]


@pytest.mark.parametrize("url", [
    "file:///etc/passwd", "data:text/html,private", "about:blank", "ftp://example.com/a",
    "http://127.0.0.1/", "http://127.1/", "http://2130706433/", "http://localhost/",
    "http://example.local/", "http://10.0.0.1/", "http://172.16.1.1/", "http://192.168.1.1/",
    "http://169.254.169.254/", "http://0.0.0.0/", "http://[::1]/", "http://[::ffff:127.0.0.1]/",
    "http://[fe80::1]/", "http://224.0.0.1/", "http://user:password@example.com/",
    "http://[2002:7f00:1::]/", "http://[64:ff9b::7f00:1]/",
    "https://example.com/?access_token=secret", "https://example.com/?X-Amz-Signature=secret",
    "https://example.com/?api_key=secret", "https://example.com/?session=secret",
    "https://example.com:22/", "https://example.com\\@127.0.0.1/", "https://example.com/\nheader",
    "https://%31%32%37.0.0.1/",
])
def test_url_boundary_denies_private_schemes_credentials_and_ports(url):
    with pytest.raises(ToolError) as error:
        ResearchAdapter().quote({"url": url}, {})
    assert error.value.code == "unsafe_url"


def test_task_cannot_enable_local_fixture_or_raise_owner_limit(tmp_path, fixture_site):
    origin, _ = fixture_site
    for arguments in ({"url": origin, "trusted_fixture_origins": [origin]},
                      {"url": "https://example.com", "max_text_chars": 1001}):
        with pytest.raises(ToolError) as error:
            ResearchAdapter().quote(arguments, {"max_text_chars": 1000})
        assert error.value.code == "invalid_arguments"
    # An owner opt-in is exact to one origin and cannot authorize another port or address.
    policy = URLPolicy({"trusted_fixture_origins": [origin]})
    assert policy.validate(origin + "/page").startswith(origin)
    with pytest.raises(ToolError):
        policy.validate("http://127.0.0.2:8080/page")


def test_response_and_output_limits(tmp_path, fixture_site):
    origin, _ = fixture_site
    ctx, _ = context(tmp_path, origin)
    result = ResearchAdapter().execute({"url": origin + "/page", "max_text_chars": 12, "max_links": 0}, ctx)
    assert len(result.data["text"]) <= 12
    assert result.data["truncated"] and result.data["links"] == []
    ctx.config = {**ctx.config, "max_response_bytes": 1024}
    original_request = ctx.transport.request

    def oversized(*args, **kwargs):
        response = original_request(*args, **kwargs)
        return HTTPResponse(response.status, response.headers, b"x" * 1025, response.url)

    ctx.transport.request = oversized
    with pytest.raises(ToolError) as error:
        ResearchAdapter().execute({"url": origin + "/page"}, ctx)
    assert error.value.code == "output_limit"


def test_implicit_transport_redirect_is_rejected(tmp_path, fixture_site):
    origin, _ = fixture_site
    ctx, _ = context(tmp_path, origin)
    ctx.transport.request = lambda *a, **kw: HTTPResponse(200, {"content-type": "text/plain"}, b"private", "http://10.0.0.1/")
    with pytest.raises(ToolError):
        ResearchAdapter().execute({"url": origin + "/page"}, ctx)


@pytest.mark.parametrize("stopped", ["cancelled", "timeout", "revoked"])
def test_cancel_timeout_and_revocation_prevent_network(tmp_path, fixture_site, stopped):
    origin, hits = fixture_site
    ctx, _ = context(tmp_path, origin)
    if stopped == "cancelled":
        ctx.cancelled = lambda: True
    elif stopped == "timeout":
        ctx.deadline = time.monotonic() - 1
    else:
        def revoke():
            raise ToolError("revoked", "Authorization revoked")
        ctx.authorize = revoke
    with pytest.raises(ToolError) as error:
        ResearchAdapter().execute({"url": origin + "/page"}, ctx)
    assert error.value.code == stopped and hits == []


def test_redirect_policy_never_downgrades_https():
    policy = URLPolicy({"allowed_redirect_origins": ["http://example.com"]})
    with pytest.raises(ToolError) as error:
        policy.redirect("https://example.com/start", "http://example.com/end")
    assert error.value.code == "unsafe_redirect"


def test_interrupted_read_never_claims_proof_of_nonexecution(tmp_path, fixture_site):
    origin, _ = fixture_site
    ctx, _ = context(tmp_path, origin)
    assert ResearchAdapter().reconcile({"url": origin + "/page"}, ctx).outcome == "uncertain"
