"""Bounded public-web retrieval. Page content and links are untrusted task data."""
from __future__ import annotations

import hashlib
import ipaddress
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any, Mapping
from urllib.parse import parse_qsl, urljoin, urlsplit, urlunsplit

from .contracts import (
    HTTPResponse, Quote, Reconciliation, ToolContext, ToolError, ToolResult,
    ToolSpec, Usage,
)

_REDIRECTS = {301, 302, 303, 307, 308}
_CREDENTIAL_KEY = re.compile(
    r"(?:^|[_-])(?:access[_-]?token|refresh[_-]?token|token|password|passwd|"
    r"secret|api[_-]?key|authorization|credential|signature|sig)(?:$|[_-])", re.I
)
_MAX_URL = 4096
_DEFAULT_MAX_BYTES = 2_000_000
_TEXT_TYPES = {"text/html", "application/xhtml+xml", "text/plain", "text/csv", "application/json"}


def _integer(value: Any, name: str, default: int, low: int, high: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ToolError("invalid_arguments", f"{name} must be an integer from {low} to {high}.")
    return value


def _origin(url: str) -> str:
    parts = urlsplit(url)
    port = parts.port
    host = parts.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    default = 443 if parts.scheme == "https" else 80
    return f"{parts.scheme}://{host}" + (f":{port}" if port and port != default else "")


class URLPolicy:
    """Syntactic guard; the transport additionally validates and pins the actual peer.

    Explicit local fixtures are owner configuration, never request arguments.
    DNS/public-address checks in the transport must happen at connection time.
    """

    def __init__(self, config: Mapping[str, Any]):
        self.allowed_hosts = config.get("allowed_hosts")
        if self.allowed_hosts is not None and (
            not isinstance(self.allowed_hosts, (tuple, list))
            or not all(isinstance(x, str) for x in self.allowed_hosts)
        ):
            raise ToolError("invalid_config", "allowed_hosts must be an owner-configured list of exact hosts.")
        self.fixture_origins: set[str] = set()
        for raw in self._origins(config, "trusted_fixture_origins"):
            try:
                parsed = urlsplit(raw)
                address = ipaddress.ip_address(parsed.hostname or "")
            except ValueError:
                raise ToolError("invalid_config", "Fixture origins must name literal loopback addresses.") from None
            if not address.is_loopback or parsed.scheme != "http":
                raise ToolError("invalid_config", "Fixture origins must be exact HTTP loopback origins.")
            self.fixture_origins.add(raw)
        self.redirect_origins = set(self._origins(config, "allowed_redirect_origins"))
        self.resource_origins = set(self._origins(config, "resource_origins"))
        ports = config.get("public_ports", [80, 443])
        if not isinstance(ports, (list, tuple)) or not all(
            isinstance(p, int) and not isinstance(p, bool) and 1 <= p <= 65535 for p in ports
        ):
            raise ToolError("invalid_config", "public_ports must contain valid port integers.")
        self.public_ports = set(ports)

    @staticmethod
    def _origins(config: Mapping[str, Any], key: str) -> list[str]:
        values = config.get(key, [])
        if not isinstance(values, (list, tuple)):
            raise ToolError("invalid_config", f"{key} must be an owner-configured list of exact origins.")
        result = []
        for value in values:
            try:
                p = urlsplit(value)
                if (not isinstance(value, str) or p.scheme not in {"http", "https"}
                        or not p.hostname or p.username is not None or p.password is not None
                        or p.path not in {"", "/"} or p.query or p.fragment
                        or any(ord(c) <= 32 or c == "\\" for c in value)):
                    raise ValueError
                result.append(_origin(value))
            except (ValueError, TypeError, AttributeError):
                raise ToolError("invalid_config", f"{key} must contain exact HTTP(S) origins.") from None
        return result

    def validate(self, raw: Any) -> str:
        if (not isinstance(raw, str) or not raw or len(raw) > _MAX_URL
                or any(ord(c) <= 32 or ord(c) == 127 or c == "\\" for c in raw)):
            raise ToolError("unsafe_url", "Supply a bounded HTTP(S) URL without control characters.")
        try:
            p = urlsplit(raw)
            if p.scheme not in {"http", "https"} or not p.hostname:
                raise ValueError
            if p.username is not None or p.password is not None or "%" in p.netloc:
                raise ToolError("unsafe_url", "Credential-bearing URLs and encoded host names are forbidden.")
            host = p.hostname.encode("idna").decode("ascii").lower().rstrip(".")
            if not host or len(host) > 253:
                raise ValueError
            port = p.port or (443 if p.scheme == "https" else 80)
            if not 1 <= port <= 65535:
                raise ValueError
            if any(_CREDENTIAL_KEY.search(k) or k.lower() in {"auth", "key", "jwt", "session", "sessionid"}
                   or k.lower().startswith("x-amz-")
                   for k, _ in parse_qsl(p.query, keep_blank_values=True, max_num_fields=128)):
                raise ToolError("unsafe_url", "URLs containing credential or signed-access parameters are forbidden.")
            netloc = f"[{host}]" if ":" in host else host
            if port != (443 if p.scheme == "https" else 80):
                netloc += f":{port}"
            clean = urlunsplit((p.scheme, netloc, p.path or "/", p.query, ""))
            fixture = _origin(clean) in self.fixture_origins
            try:
                address = ipaddress.ip_address(host)
            except ValueError:
                address = None
                if (host == "localhost" or "." not in host or host.endswith((".localhost", ".local", ".internal"))
                        or re.fullmatch(r"(?:0x[0-9a-f]+|[0-9]+)(?:\.(?:0x[0-9a-f]+|[0-9]+))*", host)):
                    raise ToolError("unsafe_url", "Local and internal host names are forbidden.")
            transition = isinstance(address, ipaddress.IPv6Address) and (
                address.sixtofour is not None or address.teredo is not None
                or address in ipaddress.ip_network("64:ff9b::/96")
                or address in ipaddress.ip_network("64:ff9b:1::/48")
            )
            if address and (not address.is_global or address.is_multicast or address.is_reserved or transition) and not fixture:
                raise ToolError("unsafe_url", "Private, loopback, reserved and link-local addresses are forbidden.")
            if not fixture and port not in self.public_ports:
                raise ToolError("unsafe_url", "This port is not enabled by the owner's research policy.")
            if not fixture and self.allowed_hosts is not None and host not in self.allowed_hosts:
                raise ToolError("unsafe_url", "This host is not enabled by the owner's research policy.")
            return clean
        except (ValueError, UnicodeError):
            raise ToolError("unsafe_url", "Supply a valid public HTTP(S) URL.") from None

    def redirect(self, source: str, location: str) -> str:
        target = self.validate(urljoin(source, location))
        if urlsplit(source).scheme == "https" and urlsplit(target).scheme != "https":
            raise ToolError("unsafe_redirect", "HTTPS redirects cannot downgrade to HTTP.")
        if _origin(source) != _origin(target) and _origin(target) not in self.redirect_origins:
            raise ToolError("unsafe_redirect", "Cross-origin redirect requires owner configuration.")
        return target


@dataclass(frozen=True)
class ReadLimits:
    text_chars: int
    links: int
    response_bytes: int
    redirects: int


def read_limits(arguments: Mapping[str, Any], config: Mapping[str, Any], *, browser: bool = False) -> ReadLimits:
    accepted = {"url", "max_text_chars", "max_links"}
    if browser:
        accepted.add("screenshot")
    if set(arguments) - accepted:
        raise ToolError("invalid_arguments", "Unknown research arguments; permissions and limits come from owner configuration.")
    policy = URLPolicy(config)
    policy.validate(arguments.get("url"))
    max_text = _integer(config.get("max_text_chars"), "configured max_text_chars", 60_000, 1, 200_000)
    max_links = _integer(config.get("max_links"), "configured max_links", 50, 0, 200)
    text = _integer(arguments.get("max_text_chars"), "max_text_chars", max_text, 1, max_text)
    links = _integer(arguments.get("max_links"), "max_links", max_links, 0, max_links)
    return ReadLimits(
        text, links,
        _integer(config.get("max_response_bytes"), "max_response_bytes", _DEFAULT_MAX_BYTES, 1024, 8_000_000),
        _integer(config.get("max_redirects"), "max_redirects", 3, 0, 5),
    )


def fetch_response(url: str, ctx: ToolContext, policy: URLPolicy, limits: ReadLimits,
                   *, max_bytes: int | None = None) -> tuple[HTTPResponse, list[str], int]:
    """Fetch with explicit redirects and the gateway's DNS-pinned public transport."""
    current = policy.validate(url)
    chain: list[str] = []
    transferred = 0
    for hop in range(limits.redirects + 1):
        ctx.check()
        response = ctx.transport.request(
            "GET", current,
            headers={"accept": "text/html,application/xhtml+xml,text/plain,application/json;q=0.8,*/*;q=0.1",
                     "user-agent": "WideMind-Research/1.0", "accept-encoding": "identity"},
            timeout=ctx.remaining(15), max_bytes=min(max_bytes or limits.response_bytes, limits.response_bytes),
            cancel=ctx.check, public_only=True,
        )
        ctx.check()
        if len(response.body) > min(max_bytes or limits.response_bytes, limits.response_bytes):
            raise ToolError("output_limit", "Response exceeded the configured byte limit.")
        transferred += len(response.body)
        if policy.validate(response.url) != current:
            raise ToolError("unsafe_redirect", "Transport followed a redirect without adapter validation.")
        if response.status not in _REDIRECTS:
            if not 200 <= response.status < 300:
                raise ToolError("http_error", f"Public page returned HTTP {response.status}.")
            return response, chain, transferred
        if hop >= limits.redirects:
            raise ToolError("redirect_limit", "Page exceeded the configured redirect limit.")
        location = response.headers.get("location")
        if not location:
            raise ToolError("unsafe_redirect", "Redirect did not supply a location.")
        next_url = policy.redirect(current, location)
        if next_url in chain or next_url == current:
            raise ToolError("redirect_limit", "Page contains a redirect loop.")
        chain.append(current)
        current = next_url
    raise AssertionError("unreachable")


def _content_type(response: HTTPResponse) -> tuple[str, str]:
    header = response.headers.get("content-type", "")
    media = header.partition(";")[0].strip().lower()
    charset = "utf-8"
    match = re.search(r"charset\s*=\s*[\"']?([a-zA-Z0-9_-]+)", header, re.I)
    if match:
        charset = match.group(1)
    return media, charset


class _TextParser(HTMLParser):
    _SKIP = {"script", "style", "noscript", "template", "svg", "canvas", "iframe", "object"}
    _BREAK = {"p", "br", "div", "li", "tr", "section", "article", "h1", "h2", "h3", "h4", "pre"}

    def __init__(self, url: str, policy: URLPolicy, limits: ReadLimits):
        super().__init__(convert_charrefs=True)
        self.url, self.policy, self.limits = url, policy, limits
        self.skip: list[str] = []
        self.in_title = False
        self.title = ""
        self.parts: list[str] = []
        self.length = 0
        self.links: list[dict] = []
        self.seen: set[str] = set()
        self.link: dict | None = None
        self.truncated = False

    def _append(self, text: str) -> None:
        clean = re.sub(r"\s+", " ", text)
        room = self.limits.text_chars - self.length
        if len(clean) > room:
            self.truncated = True
        if room > 0:
            self.parts.append(clean[:room])
            self.length += len(clean[:room])

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._SKIP:
            self.skip.append(tag)
        if self.skip:
            return
        if tag == "title":
            self.in_title = True
        if tag in self._BREAK:
            self._append("\n")
        if tag == "a" and len(self.links) < self.limits.links:
            self.link = None
            raw = dict(attrs).get("href")
            try:
                href = self.policy.validate(urljoin(self.url, raw or ""))
                if raw and href not in self.seen:
                    self.link = {"url": href, "text": ""}
                    self.links.append(self.link)
                    self.seen.add(href)
            except ToolError:
                pass

    def handle_endtag(self, tag: str) -> None:
        if self.skip:
            if tag == self.skip[-1]:
                self.skip.pop()
            return
        if tag == "title":
            self.in_title = False
        if tag == "a":
            self.link = None
        if tag in self._BREAK:
            self._append("\n")

    def handle_data(self, data: str) -> None:
        if self.skip:
            return
        if self.in_title:
            self.title = (self.title + data)[:512]
            return
        self._append(data)
        if self.link is not None:
            self.link["text"] = (self.link["text"] + data)[:200]


def extract_response(response: HTTPResponse, policy: URLPolicy, limits: ReadLimits,
                     ctx: ToolContext) -> dict:
    media, charset = _content_type(response)
    if media not in _TEXT_TYPES:
        raise ToolError("unsupported_content", "Research supports HTML, plain text, CSV and JSON; binary downloads are disabled.")
    try:
        text = response.body.decode(charset, errors="replace")
    except (LookupError, ValueError):
        text = response.body.decode("utf-8", errors="replace")
    ctx.check()
    if media in {"text/html", "application/xhtml+xml"}:
        parser = _TextParser(response.url, policy, limits)
        for start in range(0, len(text), 16_384):
            ctx.check()
            parser.feed(text[start:start + 16_384])
        parser.close()
        return {"title": re.sub(r"\s+", " ", parser.title).strip(),
                "text": "".join(parser.parts).strip(), "links": parser.links,
                "truncated": parser.truncated, "content_type": media}
    return {"title": "", "text": text[:limits.text_chars], "links": [],
            "truncated": len(text) > limits.text_chars, "content_type": media}


def source_reference(url: str, digest: str, *, kind: str = "http") -> dict:
    return {"source_id": f"sha256:{digest}", "url": url, "sha256": digest,
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "kind": kind, "trust": "untrusted"}


def research_usage(requests: int, transferred: int, **extra: int) -> Usage:
    return Usage(cost_microusd=0, units={"http_requests": requests, "response_bytes": transferred, **extra},
                 details={"cost_basis": "owner_subsidy", "subsidy": "local_compute_and_network",
                          "provider_charge_microusd": 0})


class ResearchAdapter:
    spec = ToolSpec("research.fetch", "Read a bounded public webpage and extract untrusted text, links and source evidence.")

    def quote(self, arguments: Mapping[str, Any], config: Mapping[str, Any]) -> Quote:
        limits = read_limits(arguments, config)
        return Quote(provider="public_http", limits={"max_response_bytes": limits.response_bytes,
                     "max_requests": limits.redirects + 1, "max_text_chars": limits.text_chars,
                     "max_links": limits.links, "cost_basis": "owner_subsidy"})

    def availability(self, config: Mapping[str, Any], secrets: Any) -> dict:
        URLPolicy(config)
        return {"available": True, "provider": "public_http",
                "detail": "Requires gateway public-only DNS-pinned transport, or an owner-configured SSRF-enforcing egress proxy; local compute/network is subsidized."}

    def execute(self, arguments: Mapping[str, Any], ctx: ToolContext) -> ToolResult:
        ctx.check()
        limits = read_limits(arguments, ctx.config)
        policy = URLPolicy(ctx.config)
        response, chain, transferred = fetch_response(arguments["url"], ctx, policy, limits)
        extracted = extract_response(response, policy, limits, ctx)
        reference = source_reference(response.url, hashlib.sha256(response.body).hexdigest())
        ctx.check()
        return ToolResult(data={"url": response.url, "status": response.status, **extracted,
                               "source_id": reference["source_id"], "redirects": chain},
                          references=[reference], usage=research_usage(len(chain) + 1, transferred))

    def reconcile(self, arguments: Mapping[str, Any], ctx: ToolContext) -> Reconciliation:
        ctx.check()
        return Reconciliation("uncertain", detail="An interrupted read has no provider operation status; its result cannot be reconstructed. A fresh read requires separate gateway authorization.")
