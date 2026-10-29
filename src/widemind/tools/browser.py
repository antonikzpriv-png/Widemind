"""Read-only rendered research in a disposable, network-isolated Chromium worker.

All HTTP is relayed to the authorized parent's public-only transport. The worker
has no credentials, existing profile, browser network route, or owner controls.
"""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Mapping

from .contracts import Quote, Reconciliation, ToolContext, ToolError, ToolResult, ToolSpec, Unavailable
from .research import (
    URLPolicy, _integer, _origin, read_limits, research_usage, source_reference,
)

_SLOT = threading.BoundedSemaphore(1)
_MAX_IPC = 12_000_000
_RESOURCE_TYPES = {"document", "stylesheet", "script", "image", "xhr", "fetch"}


def _browser_limits(config: Mapping[str, Any]) -> dict[str, int]:
    return {
        "requests": _integer(config.get("max_requests"), "max_requests", 32, 1, 100),
        "total_bytes": _integer(config.get("max_total_bytes"), "max_total_bytes", 8_000_000, 1024, 16_000_000),
        "memory_mb": _integer(config.get("max_memory_mb"), "max_memory_mb", 768, 256, 2048),
        "render_ms": _integer(config.get("render_ms"), "render_ms", 250, 0, 2000),
        "runtime_seconds": _integer(config.get("max_runtime_seconds"), "max_runtime_seconds", 30, 1, 60),
        "screenshot_bytes": _integer(config.get("max_screenshot_bytes"), "max_screenshot_bytes", 750_000, 1024, 2_000_000),
    }


def _runtime(config: Mapping[str, Any]) -> dict[str, str]:
    executable = config.get("browser_executable")
    if executable is not None and (not isinstance(executable, str) or not Path(executable).is_absolute()
                                   or not Path(executable).is_file()):
        raise Unavailable("browser_executable must be an installed absolute Chromium executable selected by the owner.")
    cache = config.get("browsers_path", os.environ.get("PLAYWRIGHT_BROWSERS_PATH"))
    if cache is None:
        cache = str(Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))) / "ms-playwright")
    if not isinstance(cache, str) or not Path(cache).is_absolute():
        raise Unavailable("browsers_path must be an absolute owner-configured Playwright runtime directory.")
    return {"browser_executable": executable or "", "browsers_path": cache}


def _worker_env(directory: str, runtime: Mapping[str, str]) -> dict[str, str]:
    spec = importlib.util.find_spec("playwright")
    package_root = str(Path(spec.origin).parent.parent) if spec and spec.origin else ""
    return {
        "PATH": os.defpath, "HOME": directory, "TMPDIR": directory,
        "XDG_CONFIG_HOME": directory, "XDG_CACHE_HOME": directory, "XDG_DATA_HOME": directory,
        "LANG": "C.UTF-8", "PYTHONUNBUFFERED": "1", "PYTHONNOUSERSITE": "1",
        "PYTHONPATH": os.pathsep.join(filter(None, [str(Path(__file__).resolve().parents[2]), package_root])),
        "PLAYWRIGHT_BROWSERS_PATH": runtime["browsers_path"], "NODE_OPTIONS": "--max-old-space-size=96",
    }


def _group_rss(pid: int) -> int:
    """Sum RSS for the isolated process group (shared pages count conservatively)."""
    total = 0
    try:
        for path in Path("/proc").glob("[0-9]*/stat"):
            try:
                contents = path.read_text()
                fields = contents[contents.rfind(")") + 2:].split()
                if int(fields[2]) == pid:
                    total += int(fields[21]) * os.sysconf("SC_PAGE_SIZE")
            except (OSError, ValueError, IndexError):
                continue
    except OSError:
        raise Unavailable("Browser process memory accounting requires Linux /proc.") from None
    return total


def _kill_group(process: subprocess.Popen) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass


class _Session:
    def __init__(self, arguments: Mapping[str, Any], ctx: ToolContext):
        self.ctx = ctx
        self.policy = URLPolicy(ctx.config)
        self.limits = read_limits(arguments, ctx.config, browser=True)
        self.browser_limits = _browser_limits(ctx.config)
        self.initial_url = self.policy.validate(arguments["url"])
        self.screenshot = arguments.get("screenshot", False)
        if not isinstance(self.screenshot, bool):
            raise ToolError("invalid_arguments", "screenshot must be true or false.")
        self.requests = self.transferred = self.blocked = 0
        self.redirects: list[str] = []
        self.expected_main = {self.initial_url}
        self.main_url = self.initial_url
        self.main_reference: dict | None = None
        self.main_seen = False
        self.peak_rss = 0
        self.end = min(ctx.deadline, time.monotonic() + self.browser_limits["runtime_seconds"])

    def check(self, process: subprocess.Popen | None = None) -> None:
        self.ctx.check()
        if time.monotonic() >= self.end:
            raise ToolError("timeout", "Browser exceeded its bounded rendering time.")
        if process is not None:
            self.peak_rss = max(self.peak_rss, _group_rss(process.pid))
            if self.peak_rss > self.browser_limits["memory_mb"] * 1_048_576:
                raise ToolError("resource_limit", "Browser process group exceeded its memory allowance.")

    def response(self, request: Mapping[str, Any]) -> dict:
        self.check()
        if self.requests >= self.browser_limits["requests"]:
            raise ToolError("resource_limit", "Browser exceeded its network request allowance.")
        # Counting rejected requests also bounds hostile loops producing blocked requests.
        self.requests += 1
        try:
            url = self.policy.validate(request.get("url"))
            kind = request.get("resource_type")
            main = request.get("main") is True
            if request.get("method") != "GET" or kind not in _RESOURCE_TYPES:
                raise ToolError("browser_blocked", "Only bounded read-only HTTP page resources are permitted.")
            if kind == "document":
                if not main or url not in self.expected_main:
                    raise ToolError("browser_blocked", "Frames, popups and unsolicited navigations are disabled.")
                self.expected_main.remove(url)
            elif _origin(url) not in {_origin(self.main_url), *self.policy.resource_origins}:
                raise ToolError("browser_blocked", "Cross-origin resources require owner configuration.")
            if kind == "image" and not self.screenshot:
                raise ToolError("browser_blocked", "Image transfers are disabled when no screenshot is requested.")
        except ToolError as exc:
            self.blocked += 1
            if request.get("main") is True:
                raise
            return {"abort": True, "reason": exc.code}
        available = self.browser_limits["total_bytes"] - self.transferred
        if available <= 0:
            raise ToolError("resource_limit", "Browser exceeded its total transfer allowance.")
        response = self.ctx.transport.request(
            "GET", url, headers={"user-agent": "WideMind-Research/1.0", "accept-encoding": "identity"},
            timeout=min(self.ctx.remaining(10), max(.001, self.end - time.monotonic())),
            max_bytes=min(self.limits.response_bytes, available), cancel=self.ctx.check, public_only=True,
        )
        self.check()
        self.transferred += len(response.body)
        if len(response.body) > min(self.limits.response_bytes, available):
            raise ToolError("output_limit", "Browser resource exceeded its configured byte limit.")
        if self.policy.validate(response.url) != url:
            raise ToolError("unsafe_redirect", "Transport followed an unvalidated browser redirect.")
        content_type = response.headers.get("content-type", "").partition(";")[0].lower().strip()
        headers = {"content-type": response.headers.get("content-type", "application/octet-stream")}
        if response.status in {301, 302, 303, 307, 308}:
            target = self.policy.redirect(url, response.headers.get("location", ""))
            if not response.headers.get("location"):
                raise ToolError("unsafe_redirect", "Browser redirect did not supply a location.")
            headers["location"] = target
            if main:
                if len(self.redirects) >= self.limits.redirects or target in self.redirects:
                    raise ToolError("redirect_limit", "Browser page exceeded its redirect allowance.")
                self.redirects.append(url)
                self.expected_main.add(target)
                self.main_url = target
        elif main:
            if not 200 <= response.status < 300:
                raise ToolError("http_error", f"Browser page returned HTTP {response.status}.")
            if content_type not in {"text/html", "application/xhtml+xml", "text/plain"}:
                raise ToolError("unsupported_content", "Browser reads HTML and text; file downloads are disabled.")
            if "attachment" in response.headers.get("content-disposition", "").lower():
                raise ToolError("unsupported_content", "Browser file downloads are disabled.")
            self.main_seen = True
            self.main_url = url
            self.main_reference = source_reference(url, hashlib.sha256(response.body).hexdigest())
        # Do not relay cookies, authentication, reporting endpoints, refresh, or
        # headers that can create unmediated effects. Every resource is fetched
        # independently without browser-provided credentials/referrers.
        if kind == "document" and response.status not in {301, 302, 303, 307, 308}:
            headers["content-security-policy"] = (
                "sandbox allow-scripts allow-same-origin; object-src 'none'; frame-src 'none'; "
                "worker-src 'none'; form-action 'none'; base-uri 'self'"
            )
        return {"status": response.status, "headers": headers,
                "body": base64.b64encode(response.body).decode("ascii")}

    def _send(self, process: subprocess.Popen, message: dict) -> None:
        payload = memoryview((json.dumps(message, separators=(",", ":")) + "\n").encode())
        if len(payload) > _MAX_IPC:
            raise ToolError("output_limit", "Browser relay exceeded its message limit.")
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdin, selectors.EVENT_WRITE)
            while payload:
                self.check(process)
                if selector.select(.05):
                    try:
                        written = os.write(process.stdin.fileno(), payload[:65_536])
                    except BrokenPipeError:
                        raise ToolError("browser_failed", "Browser worker stopped during its network relay.") from None
                    payload = payload[written:]

    def run(self, runtime: Mapping[str, str]) -> dict:
        with tempfile.TemporaryDirectory(prefix="widemind-browser-") as directory:
            process = subprocess.Popen(
                [sys.executable, "-m", "widemind.tools.browser", "--worker"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                cwd=directory, env=_worker_env(directory, runtime), start_new_session=True,
                bufsize=0,
            )
            try:
                os.set_blocking(process.stdin.fileno(), False)
                os.set_blocking(process.stdout.fileno(), False)
                self._send(process, {"url": self.initial_url, "text_chars": self.limits.text_chars,
                           "links": self.limits.links, "screenshot": self.screenshot,
                           "render_ms": self.browser_limits["render_ms"],
                           "screenshot_bytes": self.browser_limits["screenshot_bytes"],
                           "runtime_seconds": self.browser_limits["runtime_seconds"], **runtime})
                buffer = b""
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdout, selectors.EVENT_READ)
                    while True:
                        self.check(process)
                        if not selector.select(.05):
                            if process.poll() is not None:
                                raise Unavailable("Browser worker exited. Install the browser extra and a working sandboxed Chromium runtime.")
                            continue
                        chunk = os.read(process.stdout.fileno(), 65_536)
                        if not chunk:
                            raise Unavailable("Browser worker stopped before returning a page. Check the sandboxed Chromium installation.")
                        buffer += chunk
                        if len(buffer) > _MAX_IPC:
                            raise ToolError("output_limit", "Browser worker exceeded its output allowance.")
                        while b"\n" in buffer:
                            line, buffer = buffer.split(b"\n", 1)
                            try:
                                message = json.loads(line)
                            except (ValueError, UnicodeError):
                                raise ToolError("browser_failed", "Browser worker returned invalid data.") from None
                            if message.get("type") == "request":
                                self._send(process, self.response(message))
                            elif message.get("type") == "result":
                                return message
                            elif message.get("type") == "error":
                                if message.get("code") == "unavailable":
                                    raise Unavailable(message["message"])
                                raise ToolError("browser_failed", "Browser could not render this page within its bounded environment.")
                            else:
                                raise ToolError("browser_failed", "Browser worker returned an unknown message.")
            finally:
                _kill_group(process)
                process.stdin.close()
                process.stdout.close()

    def result(self, message: Mapping[str, Any]) -> ToolResult:
        self.check()
        if not self.main_seen or not self.main_reference:
            raise ToolError("browser_failed", "Browser did not retrieve its authorized main document.")
        if self.policy.validate(message.get("url")) != self.main_url:
            raise ToolError("unsafe_redirect", "The rendered document left its authorized page URL.")
        links: list[dict] = []
        seen: set[str] = set()
        for link in message.get("links", [])[:self.limits.links]:
            self.check()
            try:
                url = self.policy.validate(link.get("url"))
                if url not in seen:
                    links.append({"url": url, "text": str(link.get("text", ""))[:200]})
                    seen.add(url)
            except ToolError:
                continue
        extracted = {"title": str(message.get("title", ""))[:512],
                     "text": str(message.get("text", ""))[:self.limits.text_chars], "links": links}
        digest = hashlib.sha256(json.dumps(extracted, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        rendered = source_reference(self.main_url, digest, kind="rendered_text")
        rendered["derived_from"] = self.main_reference["source_id"]
        artifacts = []
        if self.screenshot and message.get("screenshot"):
            try:
                screenshot = base64.b64decode(message["screenshot"], validate=True)
            except (ValueError, TypeError):
                raise ToolError("browser_failed", "Browser returned an invalid screenshot.") from None
            if len(screenshot) > self.browser_limits["screenshot_bytes"]:
                raise ToolError("output_limit", "Screenshot exceeded its configured byte limit.")
            self.check()
            key = hashlib.sha256(self.ctx.action_id.encode()).hexdigest()[:24]
            artifact = self.ctx.workspace.write_bytes(f"browser/{key}.jpg", screenshot)
            artifacts.append({**artifact, "media_type": "image/jpeg", "trust": "untrusted",
                              "source_id": rendered["source_id"],
                              "sha256": hashlib.sha256(screenshot).hexdigest()})
        return ToolResult(
            data={"url": self.main_url, **extracted, "truncated": bool(message.get("truncated")),
                  "source_id": rendered["source_id"], "redirects": self.redirects,
                  "blocked_requests": self.blocked, "rendered": True},
            references=[self.main_reference, rendered], artifacts=artifacts,
            usage=research_usage(self.requests - self.blocked, self.transferred,
                                 browser_peak_rss_bytes=self.peak_rss),
        )


class BrowserAdapter:
    spec = ToolSpec("browser.read", "Render a public page in an isolated browser; return untrusted text, links and an optional bounded screenshot.")

    def quote(self, arguments: Mapping[str, Any], config: Mapping[str, Any]) -> Quote:
        limits = read_limits(arguments, config, browser=True)
        browser = _browser_limits(config)
        _runtime(config)
        if not isinstance(arguments.get("screenshot", False), bool):
            raise ToolError("invalid_arguments", "screenshot must be true or false.")
        return Quote(provider="local_chromium", limits={**browser, "max_response_bytes": limits.response_bytes,
                     "max_text_chars": limits.text_chars, "max_links": limits.links,
                     "cost_basis": "owner_subsidy"})

    def availability(self, config: Mapping[str, Any], secrets: Any) -> dict:
        URLPolicy(config)
        _browser_limits(config)
        runtime = _runtime(config)
        if not importlib.util.find_spec("playwright"):
            return {"available": False, "detail": "Install widemind[browser] and run python -m playwright install chromium in a supported sandbox."}
        if not runtime["browser_executable"] and not Path(runtime["browsers_path"]).is_dir():
            return {"available": False, "detail": "Install Playwright Chromium and configure browsers_path, or select an installed sandboxed Chromium with browser_executable."}
        return {"available": True, "detail": "Playwright installed; execution additionally checks Chromium's OS sandbox, public-only transport and resource limits. Compute/network is subsidized."}

    def execute(self, arguments: Mapping[str, Any], ctx: ToolContext) -> ToolResult:
        ctx.check()
        self.quote(arguments, ctx.config)
        if not importlib.util.find_spec("playwright"):
            raise Unavailable("Install widemind[browser] and python -m playwright install chromium.")
        if sys.platform != "linux" or not Path("/proc/self/stat").is_file():
            raise Unavailable("The bounded browser worker currently requires Linux /proc memory accounting.")
        if not _SLOT.acquire(blocking=False):
            raise ToolError("busy", "The browser's single execution slot is in use.", retryable=True)
        try:
            session = _Session(arguments, ctx)
            return session.result(session.run(_runtime(ctx.config)))
        finally:
            _SLOT.release()

    def reconcile(self, arguments: Mapping[str, Any], ctx: ToolContext) -> Reconciliation:
        ctx.check()
        return Reconciliation("uncertain", detail="An interrupted render has no provider operation status; the gateway must authorize a fresh read separately.")


_EXTRACT = r"""({textChars, maxLinks}) => {
  const text = [], links = [], seen = new Set();
  let count = 0, length = 0, truncated = false;
  const root = document.body || document.documentElement;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
  let node;
  while ((node = walker.nextNode())) {
    if (++count > 50000) { truncated = true; break; }
    if (node.nodeType === Node.ELEMENT_NODE) {
      if (node.tagName === 'A' && links.length < maxLinks && node.href && node.href.length <= 4096 && !seen.has(node.href)) {
        seen.add(node.href); links.push({url: node.href, text: (node.textContent || '').slice(0, 200)});
      }
      continue;
    }
    const parent = node.parentElement;
    if (!parent || parent.closest('script,style,noscript,template,svg,canvas,iframe,object') || !parent.getClientRects().length) continue;
    const style = getComputedStyle(parent);
    if (style.visibility === 'hidden' || style.visibility === 'collapse') continue;
    const value = (node.textContent || '').replace(/\s+/g, ' ');
    const room = textChars - length;
    if (value.length > room) truncated = true;
    if (room > 0) { text.push(value.slice(0, room)); length += Math.min(room, value.length); }
  }
  return {title: document.title.slice(0, 512), text: text.join('').trim(), links, truncated};
}"""


def _worker_main() -> None:
    """Private bounded line protocol. No task-controlled code, headers or launch flags."""
    import resource

    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (8_000_000, 8_000_000))
    resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))
    resource.setrlimit(resource.RLIMIT_CPU, (65, 65))

    def send(data: dict) -> None:
        encoded = json.dumps(data, separators=(",", ":"))
        if len(encoded) > _MAX_IPC:
            raise RuntimeError("worker output limit")
        sys.stdout.write(encoded + "\n")
        sys.stdout.flush()

    try:
        line = sys.stdin.buffer.readline(_MAX_IPC + 1)
        if len(line) > _MAX_IPC:
            raise RuntimeError("worker input limit")
        config = json.loads(line)
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            send({"type": "error", "code": "unavailable", "message": "Install widemind[browser] and a sandboxed Playwright Chromium runtime."})
            return
        with sync_playwright() as driver:
            try:
                browser = driver.chromium.launch(
                    headless=True, chromium_sandbox=True,
                    executable_path=config.get("browser_executable") or None,
                    timeout=min(10_000, config["runtime_seconds"] * 1000),
                    env={k: os.environ[k] for k in ("PATH", "HOME", "TMPDIR", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "LANG")},
                    # Any request missed by routing must fail locally. No proxy
                    # credentials or inherited HTTP_PROXY enter the browser.
                    proxy={"server": "http://widemind-browser-network.invalid:9", "bypass": "<-loopback>"},
                    args=["--disable-background-networking", "--disable-extensions", "--disable-sync",
                          "--disable-component-update", "--disable-quic", "--no-pings",
                          "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
                          "--host-resolver-rules=MAP * ~NOTFOUND", "--renderer-process-limit=2",
                          "--js-flags=--max-old-space-size=96"],
                )
            except Exception:
                send({"type": "error", "code": "unavailable", "message": "Sandboxed Chromium could not start. Install a supported Chromium runtime and Linux sandbox prerequisites, or select browser_executable in owner config. Host execution without the OS sandbox is disabled."})
                return
            try:
                context = browser.new_context(
                    accept_downloads=False, service_workers="block", permissions=[],
                    viewport={"width": 1280, "height": 720}, device_scale_factor=1,
                    ignore_https_errors=False,
                )
                context.route_web_socket("**/*", lambda socket: socket.close())
                page = None

                def handle(route: Any) -> None:
                    request = route.request
                    main = request.is_navigation_request() and request.frame == page.main_frame
                    send({"type": "request", "url": request.url, "method": request.method,
                          "resource_type": request.resource_type, "main": main})
                    line = sys.stdin.buffer.readline(_MAX_IPC + 1)
                    if len(line) > _MAX_IPC:
                        raise RuntimeError("network reply limit")
                    result = json.loads(line)
                    if result.get("abort"):
                        route.abort("blockedbyclient")
                    else:
                        route.fulfill(status=result["status"], headers=result["headers"],
                                      body=base64.b64decode(result["body"], validate=True))

                context.route("**/*", handle)
                page = context.new_page()
                context.on("page", lambda opened: opened.close() if opened != page else None)
                page.on("dialog", lambda dialog: dialog.dismiss())
                page.on("download", lambda download: download.cancel())
                page.set_default_timeout(5000)
                page.goto(config["url"], wait_until="domcontentloaded", timeout=config["runtime_seconds"] * 1000)
                page.wait_for_timeout(config["render_ms"])
                extracted = page.evaluate(_EXTRACT, {"textChars": config["text_chars"], "maxLinks": config["links"]})
                if config["screenshot"]:
                    screenshot = page.screenshot(type="jpeg", quality=65, full_page=False,
                                                 animations="disabled", timeout=5000)
                    if len(screenshot) > config["screenshot_bytes"]:
                        raise RuntimeError("screenshot limit")
                    extracted["screenshot"] = base64.b64encode(screenshot).decode("ascii")
                send({"type": "result", "url": page.url, **extracted})
                context.close()
            finally:
                browser.close()
    except Exception:
        send({"type": "error", "code": "browser_failed", "message": "Bounded browser rendering failed."})


if __name__ == "__main__" and sys.argv[1:] == ["--worker"]:
    _worker_main()
