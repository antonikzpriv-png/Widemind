"""Bounded, single-attempt HTTP with explicit public-network safeguards.

Public DNS validation is not enough behind a forwarding proxy: public_only
requires an owner attestation that the proxy enforces the same destination
boundary at connect time. Direct connections pin one validated numeric address.
"""
from __future__ import annotations

import base64
import errno
import http.client
import io
import ipaddress
import math
import os
import queue
import re
import selectors
import socket
import ssl
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping
from urllib.parse import quote, unquote, urlsplit, urlunsplit
from urllib.request import getproxies_environment, proxy_bypass_environment

from .tools.contracts import HTTPResponse, ToolError

_MAX_BODY = 64 * 1024 * 1024
_MAX_HEADERS = 64 * 1024
_DNS_SLOTS = threading.BoundedSemaphore(32)
_TOKEN = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_DNS_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_NUMERIC_LABEL = re.compile(r"^(?:0x[0-9a-f]+|[0-9]+)$", re.I)
_FORBIDDEN_HEADERS = frozenset({"host", "content-length", "transfer-encoding", "connection", "proxy-authorization",
                                "proxy-connection", "te", "trailer", "upgrade", "expect"})
_V6_TRANSITION = tuple(ipaddress.ip_network(s) for s in
                       ("::/96", "::ffff:0:0/96", "::ffff:0:0:0/96", "64:ff9b::/96", "64:ff9b:1::/48", "2001::/23", "2002::/16", "3fff::/20"))


def _error(code: str, message: str) -> ToolError:
    return ToolError(code, message)


def _safe_text(value: Any, maximum: int, *, ascii_only: bool = False) -> bool:
    try:
        return (isinstance(value, str) and 0 < len(value.encode("ascii" if ascii_only else "utf-8")) <= maximum
                and not any(ord(c) < 33 or ord(c) == 127 for c in value))
    except UnicodeError:
        return False


@dataclass(frozen=True)
class _URL:
    original: str
    scheme: str
    host: str
    port: int
    authority: str
    origin: str
    path: str
    absolute: str


def _url(value: str) -> _URL:
    if not _safe_text(value, 16_384) or "\\" in value:
        raise _error("invalid_url", "HTTP requires a bounded absolute URL without control characters.")
    try:
        p = urlsplit(value)
        if (p.scheme not in {"http", "https"} or not p.netloc or p.username is not None
                or p.password is not None or p.fragment or not p.hostname or "%" in p.hostname):
            raise ValueError
        port = p.port if p.port is not None else (443 if p.scheme == "https" else 80)
        if not 1 <= port <= 65535 or p.netloc.endswith(":"):
            raise ValueError
        host = p.hostname.encode("idna").decode("ascii").lower()
        if not host or len(host) > 253 or host.endswith("."):
            raise ValueError
        if ":" in host:
            if not p.netloc.startswith("[") or not isinstance(ipaddress.ip_address(host), ipaddress.IPv6Address):
                raise ValueError
        else:
            if not all(_DNS_LABEL.fullmatch(label) for label in host.split(".")):
                raise ValueError
            try:
                ipaddress.ip_address(host)
                if host != p.hostname.lower():
                    raise ValueError
            except ValueError:
                # Reject alternate decimal/octal/hex IPv4 spellings rather than
                # delegating their interpretation to libc or a proxy parser.
                if all(_NUMERIC_LABEL.fullmatch(label) for label in host.split(".")):
                    raise ValueError
        host_part = "[" + host + "]" if ":" in host else host
        explicit_port = p.netloc.endswith(":" + str(port))
        authority = host_part + (":" + str(port) if explicit_port or port != (443 if p.scheme == "https" else 80) else "")
        path = quote(p.path or "/", safe="/%:@!$&'()*+,;=-._~")
        if p.query:
            path += "?" + quote(p.query, safe="/%?:@!$&'()*+,;=-._~[]")
        origin = p.scheme + "://" + host_part + ":" + str(port)
        return _URL(value, p.scheme, host, port, authority, origin, path, p.scheme + "://" + authority + path)
    except (ValueError, UnicodeError):
        raise _error("invalid_url", "HTTP URL must have a valid HTTP(S) origin without credentials, fragments, or ambiguous host syntax.") from None


def _public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if (not ip.is_global or ip.is_multicast or ip.is_reserved or ip.is_unspecified
            or ip.is_loopback or ip.is_link_local):
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        if (ip not in ipaddress.ip_network("2000::/3") or any(ip in n for n in _V6_TRANSITION)
                or (int(ip) >> 32) & 0xFFFFFFFF in {0x00005EFE, 0x02005EFE}):
            return False
    elif ip in ipaddress.ip_network("192.0.0.0/24"):
        return False
    return True


class _Deadline:
    def __init__(self, seconds: float, cancel: Callable[[], None] | None):
        self.end, self.cancel = time.monotonic() + seconds, cancel

    def check(self) -> None:
        if time.monotonic() >= self.end:
            raise _error("timeout", "HTTP request exceeded its overall deadline.")
        if self.cancel is not None:
            try:
                self.cancel()
            except ToolError as exc:
                code = exc.code if isinstance(exc.code, str) and re.fullmatch(r"[a-z_]{1,64}", exc.code) else "cancelled"
                raise _error(code, "HTTP request stopped by gateway authorization or cancellation.") from None
            except Exception:
                raise _error("cancelled", "HTTP request stopped by gateway authorization or cancellation.") from None
        if time.monotonic() >= self.end:
            raise _error("timeout", "HTTP request exceeded its overall deadline.")

    def wait(self, sock: socket.socket, event: int) -> None:
        with selectors.DefaultSelector() as selector:
            selector.register(sock, event)
            while True:
                self.check()
                if selector.select(max(0, min(.05, self.end - time.monotonic()))):
                    return


def _resolve(host: str, port: int, deadline: _Deadline) -> list[tuple]:
    deadline.check()
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        return [(socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (str(literal), port, 0, 0))] if literal.version == 6 else [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (str(literal), port))]
    slots = _DNS_SLOTS
    if not slots.acquire(blocking=False):
        raise _error("dns_busy", "HTTP DNS resolver capacity is busy; retry after outstanding lookups finish.")
    result: queue.Queue = queue.Queue(maxsize=1)

    def run() -> None:
        try:
            result.put((True, socket.getaddrinfo(host, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)))
        except Exception:
            result.put((False, None))
        finally:
            slots.release()

    try:
        threading.Thread(target=run, name="widemind-dns", daemon=True).start()
    except Exception:
        slots.release()
        raise _error("dns_failure", "HTTP DNS resolution could not start.") from None
    while True:
        deadline.check()
        try:
            ok, addresses = result.get(timeout=max(.001, min(.05, deadline.end - time.monotonic())))
            if not ok or not addresses or len(addresses) > 256:
                raise _error("dns_failure", "HTTP DNS resolution did not return usable addresses.")
            deadline.check()
            return addresses
        except queue.Empty:
            continue


def _validated_addresses(addresses: list[tuple], *, public: bool, fixture: bool) -> list[tuple]:
    result = []
    try:
        for address in addresses:
            family, kind, proto, _, sockaddr = address
            ip = ipaddress.ip_address(sockaddr[0])
            if (family not in {socket.AF_INET, socket.AF_INET6} or kind != socket.SOCK_STREAM
                    or proto not in {0, socket.IPPROTO_TCP} or "%" in sockaddr[0]
                    or (family == socket.AF_INET) != (ip.version == 4)):
                raise ValueError
            if public and not (ip.is_loopback if fixture else _public(ip)):
                raise ValueError
            if family == socket.AF_INET6 and len(sockaddr) >= 4 and sockaddr[3] != 0:
                raise ValueError
            result.append((family, kind, proto, "", sockaddr))
    except (ValueError, TypeError, IndexError):
        raise _error("private_destination", "HTTP destination includes a non-public, ambiguous, or prohibited address.") from None
    if not result:
        raise _error("dns_failure", "HTTP destination has no usable addresses.")
    return result


class _RawStream:
    def __init__(self, address: tuple, deadline: _Deadline, wire_limit: int):
        self.deadline, self.received, self.wire_limit = deadline, 0, wire_limit
        self.sock = socket.socket(address[0], socket.SOCK_STREAM, address[2])
        self.sock.setblocking(False)
        try:
            deadline.check()
            code = self.sock.connect_ex(address[4])
            if code not in {0, errno.EISCONN}:
                if code not in {errno.EINPROGRESS, errno.EWOULDBLOCK, errno.EALREADY, errno.EINTR}:
                    raise OSError
                deadline.wait(self.sock, selectors.EVENT_WRITE)
                if self.sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR):
                    raise OSError
            deadline.check()
        except BaseException:
            self.sock.close()
            raise

    def read(self, maximum: int) -> bytes:
        while True:
            self.deadline.check()
            try:
                data = self.sock.recv(min(maximum, 65536))
                self.received += len(data)
                if self.received > self.wire_limit:
                    raise _error("output_limit", "HTTP response exceeded its bounded wire allowance.")
                return data
            except (BlockingIOError, InterruptedError):
                self.deadline.wait(self.sock, selectors.EVENT_READ)

    def write(self, data: bytes) -> None:
        view = memoryview(data)
        while view:
            self.deadline.check()
            try:
                sent = self.sock.send(view)
                if not sent:
                    raise OSError
                view = view[sent:]
            except (BlockingIOError, InterruptedError):
                self.deadline.wait(self.sock, selectors.EVENT_WRITE)

    def close(self) -> None:
        self.sock.close()


class _TLSStream:
    """MemoryBIO TLS permits verified TLS inside an HTTPS proxy TLS tunnel."""
    def __init__(self, stream: Any, context: ssl.SSLContext, hostname: str, deadline: _Deadline):
        self.stream, self.deadline = stream, deadline
        self.incoming, self.outgoing, self.eof = ssl.MemoryBIO(), ssl.MemoryBIO(), False
        self.tls = context.wrap_bio(self.incoming, self.outgoing, server_side=False, server_hostname=hostname)
        self._run(self.tls.do_handshake)

    def _flush(self) -> None:
        while self.outgoing.pending:
            self.stream.write(self.outgoing.read(65536))

    def _run(self, operation: Callable[[], Any]) -> Any:
        while True:
            self.deadline.check()
            try:
                result = operation()
                self._flush()
                return result
            except ssl.SSLWantWriteError:
                self._flush()
            except ssl.SSLWantReadError:
                self._flush()
                if self.eof:
                    raise ssl.SSLEOFError
                data = self.stream.read(65536)
                if data:
                    self.incoming.write(data)
                else:
                    self.incoming.write_eof()
                    self.eof = True

    def read(self, maximum: int) -> bytes:
        try:
            return self._run(lambda: self.tls.read(maximum))
        except ssl.SSLZeroReturnError:
            return b""

    def write(self, data: bytes) -> None:
        view = memoryview(data)
        while view:
            written = self._run(lambda: self.tls.write(view[:65536]))
            if not written:
                raise OSError
            view = view[written:]

    def close(self) -> None:
        self.stream.close()


class _RawReader(io.RawIOBase):
    def __init__(self, stream: Any):
        super().__init__()
        self.stream = stream

    def readable(self) -> bool:
        return True

    def readinto(self, target: Any) -> int:
        data = self.stream.read(len(target))
        target[:len(data)] = data
        return len(data)


class _ResponseFile:
    def __init__(self, stream: Any):
        self.buffer = io.BufferedReader(_RawReader(stream), buffer_size=8192)
        self.header_bytes = 0

    def readline(self, maximum: int = -1) -> bytes:
        remaining = _MAX_HEADERS - self.header_bytes
        data = self.buffer.readline(min(maximum if maximum >= 0 else remaining + 1, remaining + 1))
        self.header_bytes += len(data)
        if self.header_bytes > _MAX_HEADERS:
            raise _error("output_limit", "HTTP response header or chunk framing exceeded its size bound.")
        return data

    def read(self, size: int = -1) -> bytes:
        return self.buffer.read(size)

    def readinto(self, target: Any) -> int:
        return self.buffer.readinto(target)

    def close(self) -> None:
        self.buffer.close()

    def flush(self) -> None:
        # Read-only HTTPResponse file; closing never writes or performs I/O.
        pass


class _Socket:
    def __init__(self, stream: Any):
        self.stream = stream

    def sendall(self, data: bytes) -> None:
        self.stream.write(data)

    def makefile(self, mode: str) -> _ResponseFile:
        if mode != "rb":
            raise ValueError
        return _ResponseFile(self.stream)

    def close(self) -> None:
        self.stream.close()


class _HTTPResponse(http.client.HTTPResponse):
    def _read_status(self) -> tuple:
        # HTTPResponse.begin only skips 100; public servers can also send 103
        # Early Hints. Consume bounded informational messages on the SAME file
        # so buffered final-response bytes are never dropped or re-requested.
        for _ in range(16):
            version, status, reason = super()._read_status()
            if not 100 <= status < 200:
                return version, status, reason
            if status == 101:
                raise _error("http_response", "HTTP protocol upgrades are not supported.")
            http.client.parse_headers(self.fp)
        raise _error("http_response", "HTTP returned too many informational responses.")


def _tls_context() -> ssl.SSLContext:
    try:
        # stdlib honors SSL_CERT_FILE/DIR. Preserve commonly injected bundle
        # selectors as well when a runtime was configured for requests/curl.
        bundle = os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE") or os.environ.get("CURL_CA_BUNDLE")
        return ssl.create_default_context(cafile=bundle, capath=os.environ.get("SSL_CERT_DIR"))
    except Exception:
        raise _error("tls_configuration", "HTTP TLS trust configuration is unavailable or invalid.") from None


def _headers(headers: Mapping[str, str] | None, body: bytes | None) -> dict[str, str]:
    if headers is not None and (not isinstance(headers, Mapping) or len(headers) > 100):
        raise _error("invalid_headers", "HTTP headers must be a bounded string mapping.")
    result, size = {}, 0
    for name, value in (headers or {}).items():
        if not isinstance(name, str) or not _TOKEN.fullmatch(name) or len(name) > 256:
            raise _error("invalid_headers", "HTTP header names must use valid bounded ASCII tokens.")
        key = name.lower()
        if key in result or key in _FORBIDDEN_HEADERS:
            raise _error("invalid_headers", "HTTP Host, framing, and hop-by-hop headers are controlled by the transport.")
        try:
            if (not isinstance(value, str) or any(ord(c) < 32 or ord(c) == 127 for c in value)
                    or len(value.encode("latin-1")) > _MAX_HEADERS):
                raise ValueError
        except (ValueError, UnicodeError):
            raise _error("invalid_headers", "HTTP header values must be bounded strings without control characters.") from None
        result[key] = value
        size += len(name) + len(value) + 4
    if size > _MAX_HEADERS:
        raise _error("invalid_headers", "HTTP request headers exceed their combined size limit.")
    result.setdefault("accept-encoding", "identity")
    result["connection"] = "close"
    if body is not None:
        result["content-length"] = str(len(body))
    return result


def _proxy(target: _URL) -> tuple[_URL, str | None] | None:
    proxies = getproxies_environment()
    if proxy_bypass_environment(target.authority, proxies):
        return None
    value = proxies.get(target.scheme) or proxies.get("all")
    if not value:
        return None
    try:
        if not _safe_text(value, 8192):
            raise ValueError
        if "://" not in value:
            value = "http://" + value
        parts = urlsplit(value)
        if parts.scheme not in {"http", "https"} or parts.query or parts.fragment or parts.path not in {"", "/"}:
            raise ValueError
        auth = None
        if parts.username is not None:
            username, password = unquote(parts.username), unquote(parts.password or "")
            auth = "Basic " + base64.b64encode((username + ":" + password).encode("utf-8")).decode("ascii")
        netloc = parts.netloc.rsplit("@", 1)[-1]
        return _url(urlunsplit((parts.scheme, netloc, "", "", ""))), auth
    except Exception:
        raise _error("proxy_configuration", "The inherited HTTP proxy configuration is invalid or unsupported.") from None


class HTTPTransport:
    def __init__(self, policy: Mapping | None = None):
        if policy is not None and not isinstance(policy, Mapping):
            raise _error("network_configuration", "HTTP transport requires owner-loaded network policy.")
        network = (policy or {}).get("network", {})
        if not isinstance(network, Mapping):
            raise _error("network_configuration", "Owner network policy must be a mapping.")
        fixtures = network.get("trusted_fixture_origins", [])
        if not isinstance(fixtures, list) or len(fixtures) > 32 or not all(isinstance(v, str) for v in fixtures):
            raise _error("network_configuration", "Owner fixture origins must be a bounded list of exact origins.")
        self.fixture_origins = set()
        for value in fixtures:
            parsed = _url(value)
            if parsed.path != "/":
                raise _error("network_configuration", "Owner fixture entries must be exact origins without paths or queries.")
            self.fixture_origins.add(parsed.origin)
        self.proxy_guarded = network.get("public_proxy_guarded") is True

    def request(self, method: str, url: str, *, headers: Mapping[str, str] | None = None,
                body: bytes | None = None, timeout: float = 30, max_bytes: int = 2_000_000,
                cancel: Callable[[], None] | None = None, public_only: bool = False) -> HTTPResponse:
        if (not isinstance(method, str) or method.upper() not in {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"}
                or type(max_bytes) is not int or not 1 <= max_bytes <= _MAX_BODY
                or isinstance(timeout, bool) or not isinstance(timeout, (int, float))
                or not 0 < timeout <= 600 or not math.isfinite(timeout)
                or body is not None and (not isinstance(body, bytes) or len(body) > _MAX_BODY)
                or type(public_only) is not bool or cancel is not None and not callable(cancel)):
            raise _error("invalid_http_request", "HTTP method, body, timeout, cancellation, or output limit is invalid.")
        deadline = _Deadline(float(timeout), cancel)
        deadline.check()
        target, request_headers = _url(url), _headers(headers, body)
        proxy = _proxy(target)
        fixture = target.origin in self.fixture_origins
        if public_only and proxy and not (self.proxy_guarded or fixture):
            raise _error("proxy_guard_required", "Public HTTP through an inherited proxy requires an owner-confirmed destination guard.")
        stream = connection = response = None
        try:
            destination = _resolve(target.host, target.port, deadline)
            destination = _validated_addresses(destination, public=public_only, fixture=fixture)
            connect = destination
            if proxy:
                connect = _validated_addresses(_resolve(proxy[0].host, proxy[0].port, deadline), public=False, fixture=False)
            # A single validated numeric connection: no second DNS lookup and
            # no address fallback that could silently repeat an HTTP request.
            stream = _RawStream(connect[0], deadline, max_bytes * 4 + 1_048_576)
            context = _tls_context() if target.scheme == "https" or proxy and proxy[0].scheme == "https" else None
            if proxy and proxy[0].scheme == "https":
                stream = _TLSStream(stream, context, proxy[0].host, deadline)
            if proxy and target.scheme == "https":
                authority = ("[" + target.host + "]" if ":" in target.host else target.host) + ":" + str(target.port)
                tunnel = "CONNECT " + authority + " HTTP/1.1\r\nHost: " + authority + "\r\n"
                if proxy[1]:
                    tunnel += "Proxy-Authorization: " + proxy[1] + "\r\n"
                stream.write((tunnel + "\r\n").encode("ascii"))
                response = _HTTPResponse(_Socket(stream), method="CONNECT")
                response.begin()
                if response.status != 200:
                    raise _error("proxy_rejected", "The inherited proxy rejected the HTTP tunnel.")
                response.close()
                response = None
            if target.scheme == "https":
                stream = _TLSStream(stream, context, target.host, deadline)
            request_headers["host"] = target.authority
            request_target = target.path
            if proxy and target.scheme == "http":
                request_target = target.absolute
                if proxy[1]:
                    request_headers["proxy-authorization"] = proxy[1]
            connection = http.client.HTTPConnection(target.host, target.port)
            connection.sock = _Socket(stream)
            connection.request(method.upper(), request_target, body=body, headers=request_headers)
            # Parse explicitly rather than HTTPConnection.getresponse(): that
            # method may close its socket immediately for Connection: close.
            # Our deadline stream intentionally owns one actual socket instead
            # of socket.makefile's separate descriptor-reference lifecycle.
            response = _HTTPResponse(_Socket(stream), method=method.upper())
            response.begin()
            length = response.getheader("content-length")
            transfer = response.getheader("transfer-encoding")
            if transfer is not None and (transfer.casefold() != "chunked" or length is not None):
                raise _error("http_response", "HTTP response has unsupported or ambiguous transfer framing.")
            if method.upper() != "HEAD" and response.status not in {204, 304} and length is not None:
                if not re.fullmatch(r"[0-9]+", length) or len(length) > 20:
                    raise _error("http_response", "HTTP response has invalid length framing.")
                if int(length) > max_bytes:
                    raise _error("output_limit", "HTTP response exceeds the configured output limit.")
            content = bytearray()
            while True:
                deadline.check()
                chunk = response.read(min(65536, max_bytes + 1 - len(content)))
                if not chunk:
                    break
                content.extend(chunk)
                if len(content) > max_bytes:
                    raise _error("output_limit", "HTTP response exceeds the configured output limit.")
            deadline.check()
            if (length is not None and method.upper() != "HEAD" and response.status not in {204, 304}
                    and not 100 <= response.status < 200 and len(content) != int(length)):
                raise _error("http_response", "HTTP response ended before its declared content was complete.")
            result_headers = {}
            for name, value in response.getheaders():
                name = name.lower()
                result_headers[name] = result_headers[name] + ", " + value if name in result_headers else value
            return HTTPResponse(response.status, result_headers, bytes(content), url)
        except ToolError:
            raise
        except ssl.SSLCertVerificationError:
            raise _error("tls_verification", "HTTP TLS certificate verification failed.") from None
        except Exception:
            raise _error("http_transport", "HTTP request failed without a complete usable response.") from None
        finally:
            for resource in (response, connection, stream):
                if resource is not None:
                    try:
                        resource.close()
                    except Exception:
                        pass  # Never mask a sanitized error or skip socket cleanup.
