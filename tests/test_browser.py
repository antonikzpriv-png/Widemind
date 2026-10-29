import importlib.util
import os
from pathlib import Path
import shutil
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from widemind.tools.browser import BrowserAdapter, _Session, _worker_env
from widemind.tools.contracts import ToolError, Unavailable
from test_research_support import context, fixture_site


def request(url, *, method="GET", resource_type="document", main=True):
    return {"url": url, "method": method, "resource_type": resource_type, "main": main}


def test_browser_parent_uses_real_bounded_http_for_every_request(tmp_path, fixture_site):
    origin, hits = fixture_site
    ctx, _ = context(tmp_path, origin)
    session = _Session({"url": origin + "/page"}, ctx)
    response = session.response(request(origin + "/page"))
    assert response["status"] == 200 and "body" in response
    assert "form-action 'none'" in response["headers"]["content-security-policy"]
    assert set(response["headers"]) == {"content-type", "content-security-policy"}
    assert session.main_seen and session.main_reference["trust"] == "untrusted"
    assert hits == ["/page"]


@pytest.mark.parametrize("url,method,kind", [
    ("http://169.254.169.254/metadata", "GET", "fetch"),
    ("file:///etc/passwd", "GET", "fetch"),
    ("https://example.org/tracker", "GET", "script"),
    ("http://fixture.invalid/send", "POST", "fetch"),
])
def test_browser_subrequests_are_guarded_before_transport(tmp_path, fixture_site, url, method, kind):
    origin, hits = fixture_site
    ctx, _ = context(tmp_path, origin)
    session = _Session({"url": origin + "/page"}, ctx)
    result = session.response(request(url, method=method, resource_type=kind, main=False))
    assert result["abort"] is True and hits == []


def test_browser_blocks_form_posts_frames_downloads_and_unsolicited_navigation(tmp_path, fixture_site):
    origin, hits = fixture_site
    ctx, _ = context(tmp_path, origin)
    session = _Session({"url": origin + "/page"}, ctx)
    assert session.response(request(origin + "/send", method="POST", resource_type="fetch", main=False))["abort"]
    assert session.response(request(origin + "/iframe", main=False))["abort"]
    assert session.response(request(origin + "/image", resource_type="image", main=False))["abort"]
    with pytest.raises(ToolError) as error:
        session.response(request(origin + "/logout"))
    assert error.value.code == "browser_blocked" and hits == []


def test_browser_follows_only_validated_explicit_main_redirect(tmp_path, fixture_site):
    origin, hits = fixture_site
    ctx, _ = context(tmp_path, origin)
    session = _Session({"url": origin + "/redirect"}, ctx)
    redirect = session.response(request(origin + "/redirect"))
    assert redirect["headers"]["location"] == origin + "/page"
    session.response(request(origin + "/page"))
    assert session.main_url == origin + "/page"
    assert hits == ["/redirect", "/page"]
    with pytest.raises(ToolError):
        session.response(request(origin + "/page"))


def test_browser_limits_count_blocked_request_loops_and_memory(tmp_path, fixture_site, monkeypatch):
    origin, hits = fixture_site
    ctx, _ = context(tmp_path, origin, max_requests=1)
    session = _Session({"url": origin + "/page"}, ctx)
    session.response(request(origin + "/send", method="POST", resource_type="fetch", main=False))
    with pytest.raises(ToolError) as error:
        session.response(request(origin + "/page"))
    assert error.value.code == "resource_limit" and hits == []
    monkeypatch.setattr("widemind.tools.browser._group_rss", lambda pid: 3_000_000_000)
    with pytest.raises(ToolError) as error:
        session.check(type("Process", (), {"pid": 123})())
    assert error.value.code == "resource_limit"


def test_worker_env_has_no_owner_credentials_or_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_OWNER_SECRET", "must-never-reach-browser")
    monkeypatch.setenv("HTTPS_PROXY", "http://user:secret@proxy.invalid")
    monkeypatch.setenv("AWS_PROFILE", "unrelated-account")
    env = _worker_env(str(tmp_path), {"browsers_path": "/installed/playwright"})
    assert env["HOME"] == str(tmp_path)
    assert env["PLAYWRIGHT_BROWSERS_PATH"] == "/installed/playwright"
    assert "must-never-reach-browser" not in str(env)
    assert "HTTPS_PROXY" not in env and "AWS_PROFILE" not in env
    assert "LD_PRELOAD" not in env and "NODE_EXTRA_CA_CERTS" not in env


def test_missing_optional_playwright_is_actionable_and_http_still_works(tmp_path, fixture_site, monkeypatch):
    from widemind.tools.research import ResearchAdapter

    origin, _ = fixture_site
    ctx, _ = context(tmp_path, origin)
    real_find = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None if name == "playwright" else real_find(name))
    assert BrowserAdapter().availability(ctx.config, ctx.secrets)["available"] is False
    with pytest.raises(Unavailable, match="Install widemind"):
        BrowserAdapter().execute({"url": origin + "/page"}, ctx)
    assert ResearchAdapter().execute({"url": origin + "/page"}, ctx).data["title"]


def test_browser_result_sources_and_private_artifact(tmp_path, fixture_site):
    import base64

    origin, _ = fixture_site
    ctx, _ = context(tmp_path, origin)
    session = _Session({"url": origin + "/page", "screenshot": True}, ctx)
    session.response(request(origin + "/page"))
    result = session.result({"url": origin + "/page", "title": "Rendered", "text": "Rendered result",
                             "links": [{"url": "file:///etc/passwd"}, {"url": origin + "/next"}],
                             "screenshot": base64.b64encode(b"bounded screenshot fixture").decode()})
    assert result.trust == "untrusted" and result.data["rendered"]
    assert result.data["links"] == [{"url": origin + "/next", "text": ""}]
    assert result.references[1]["derived_from"] == result.references[0]["source_id"]
    assert result.artifacts[0]["trust"] == "untrusted"
    assert (tmp_path / result.artifacts[0]["path"]).is_file()


def test_real_sandboxed_playwright_render_when_runtime_available(tmp_path, fixture_site):
    """Actual browser, actual fixture HTTP; skips with the runtime's exact dependency failure."""
    origin, hits = fixture_site
    executable = os.environ.get("WIDEMIND_TEST_CHROMIUM") or shutil.which("chromium")
    config = {"browser_executable": executable} if executable else {}
    ctx, _ = context(tmp_path, origin, **config)
    try:
        result = BrowserAdapter().execute({"url": origin + "/page", "screenshot": True}, ctx)
    except Unavailable as exc:
        pytest.skip(exc.message)
    assert "Rendered fixture" in result.data["text"]
    assert result.artifacts and result.data["rendered"]
    assert hits and result.references[0]["url"] == origin + "/page"


def test_browser_arguments_cannot_select_profile_executable_or_permit_private_network():
    for extra in [{"browser_executable": "/usr/bin/chromium"}, {"storage_state": "host/profile"},
                  {"trusted_fixture_origins": ["http://127.0.0.1"]}, {"javascript": "fetch('/send')"}]:
        with pytest.raises(ToolError) as error:
            BrowserAdapter().quote({"url": "https://example.com", **extra}, {})
        assert error.value.code == "invalid_arguments"


def test_cancel_kills_real_worker_process_and_releases_browser_slot(tmp_path, fixture_site, monkeypatch):
    """Exercise supervision against a real hung child, without requiring Chromium."""
    import widemind.tools.browser as module

    origin, hits = fixture_site
    ctx, _ = context(tmp_path, origin)
    stop_at = time.monotonic() + .2
    ctx.cancelled = lambda: time.monotonic() >= stop_at
    processes = []
    real_popen = module.subprocess.Popen
    real_find = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: SimpleNamespace(origin=__file__) if name == "playwright" else real_find(name))

    def hung_worker(command, **kwargs):
        process = real_popen([sys.executable, "-c", "import time; time.sleep(30)"], **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(module.subprocess, "Popen", hung_worker)
    with pytest.raises(ToolError) as error:
        BrowserAdapter().execute({"url": origin + "/page"}, ctx)
    assert error.value.code == "cancelled"
    assert processes and processes[0].poll() is not None and hits == []
    assert module._SLOT.acquire(blocking=False)
    module._SLOT.release()


def test_independent_memory_supervisor_kills_child_without_parent_polling(tmp_path, fixture_site, monkeypatch):
    import widemind.tools.browser as module

    origin, _ = fixture_site
    ctx, _ = context(tmp_path, origin)
    session = _Session({"url": origin + "/page"}, ctx)
    process = module.subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True)
    stop = threading.Event()
    monkeypatch.setattr(module, "_group_rss", lambda pid: 3_000_000_000)
    supervisor = threading.Thread(target=session._supervise, args=(process, stop))
    try:
        supervisor.start()
        process.wait(timeout=2)
        assert session._watch_error.code == "resource_limit"
    finally:
        stop.set()
        module._kill_group(process)
        supervisor.join(timeout=1)
