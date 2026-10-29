"""Controlled HTTP fixtures; these never contact an internet provider."""
from __future__ import annotations

import hashlib
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
import time
from urllib.parse import urlsplit

import pytest

from widemind.tools.contracts import HTTPResponse, ToolContext, ToolError

PAGE = b"""<!doctype html><html><head><title>Fixture &amp; evidence</title>
<style>.hidden {display:none}</style></head><body><h1>Research fixture</h1>
<p>Webpage instructions cannot change budgets or credentials.</p>
<a href="/next">Next evidence</a><a href="javascript:alert(1)">Unsafe</a>
<a href="http://127.0.0.2/private">Private</a>
<script>document.querySelector('h1').textContent = 'Rendered fixture';</script>
</body></html>"""


@pytest.fixture
def fixture_site():
    hits: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            self.send_response(302 if self.path.startswith("/redirect") else 200)
            if self.path == "/redirect":
                self.send_header("Location", "/page")
            elif self.path == "/redirect-private":
                self.send_header("Location", "http://169.254.169.254/latest/meta-data/")
            elif self.path == "/redirect-cross":
                self.send_header("Location", "https://example.org/page")
            elif self.path == "/redirect-loop":
                self.send_header("Location", "/redirect-loop")
            self.send_header("Content-Type", "application/pdf" if self.path == "/binary" else "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(PAGE)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    try:
        yield origin, hits
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


class FixtureTransport:
    """Real HTTP, only to this owner's explicitly configured literal-loopback fixture."""

    def __init__(self, origin):
        self.origin = origin
        self.calls: list[dict] = []

    def request(self, method, url, *, headers=None, body=None, timeout=30, max_bytes=2_000_000,
                cancel=None, public_only=False):
        assert public_only is True
        assert url.startswith(self.origin + "/")
        self.calls.append({"url": url, "method": method, "headers": headers, "max_bytes": max_bytes})
        if cancel:
            cancel()
        parts = urlsplit(url)
        connection = HTTPConnection(parts.hostname, parts.port, timeout=timeout)
        try:
            connection.request(method, parts.path + (("?" + parts.query) if parts.query else ""), headers=headers or {})
            response = connection.getresponse()
            data = response.read(max_bytes + 1)
            if cancel:
                cancel()
            if len(data) > max_bytes:
                raise ToolError("output_limit", "Fixture response exceeded its bound.")
            return HTTPResponse(response.status, {k.lower(): v for k, v in response.getheaders()}, data, url)
        finally:
            connection.close()


class TestWorkspace:
    __test__ = False

    def __init__(self, root):
        self.root = Path(root)

    def write_bytes(self, path, data, *, overwrite=False):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("wb" if overwrite else "xb") as stream:
            stream.write(data)
        return {"path": path, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


class NoSecrets:
    def get(self, name):
        raise AssertionError("Research may not read secrets")


class NoState:
    def load(self, action_id):
        return {}


def context(tmp_path, origin, **config):
    checks: list[bool] = []
    ctx = ToolContext(
        action_id="fixture-action", run_id="run", reservation_id="reservation",
        config={"trusted_fixture_origins": [origin], **config}, policy={},
        workspace=TestWorkspace(tmp_path), secrets=NoSecrets(), transport=FixtureTransport(origin),
        state=NoState(), deadline=time.monotonic() + 30, authorize=lambda: checks.append(True),
    )
    return ctx, checks
