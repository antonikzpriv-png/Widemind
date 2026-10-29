"""Small bounded HTTPS transport. Inherited proxy and CA configuration are preserved."""
from dataclasses import dataclass, field
import http.client
import io
import ipaddress
import json
import queue
import socket
import threading
import time
import urllib.error
import urllib.request


class TransportError(Exception):
    """No reliable provider outcome; callers must reconcile side effects."""


@dataclass(frozen=True)
class Response:
    status: int
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""

    def json(self):
        value = json.loads(self.body)
        if not isinstance(value, dict):
            raise ValueError("provider response must be an object")
        return value


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_DNS_SLOTS = threading.BoundedSemaphore(4)


def resolve_bounded(resolver, host, port, timeout):
    """Only DNS runs in the bounded daemon pool; it can never send HTTP later."""
    if timeout <= 0 or not _DNS_SLOTS.acquire(blocking=False):
        raise TransportError("provider DNS capacity/deadline exhausted")
    result = queue.Queue(maxsize=1)
    def lookup():
        try:
            result.put((True, resolver(host, port, type=socket.SOCK_STREAM)))
        except Exception:
            result.put((False, None))
        finally:
            _DNS_SLOTS.release()
    try:
        threading.Thread(target=lookup, daemon=True, name="widemind-dns").start()
    except BaseException:
        _DNS_SLOTS.release()
        raise
    try:
        success, addresses = result.get(timeout=timeout)
    except queue.Empty as exc:
        raise TransportError("provider DNS deadline exceeded") from exc
    if not success or not addresses or len(addresses) > 64:
        raise TransportError("provider DNS unavailable")
    return addresses


def _remaining(deadline, clock):
    remaining = deadline - clock()
    if remaining <= 0:
        raise TransportError("provider whole-request deadline exceeded")
    return remaining


class _DeadlineReader(io.RawIOBase):
    """Bound status/header reads too: socket timeouts alone permit slow drips."""
    def __init__(self, raw, sock, deadline, clock):
        self.raw, self.sock, self.deadline, self.clock = raw, sock, deadline, clock

    def readable(self):
        return True

    def readinto(self, buffer):
        self.sock.settimeout(_remaining(self.deadline, self.clock))
        count = self.raw.readinto(buffer)
        _remaining(self.deadline, self.clock)
        return count

    def close(self):
        try:
            self.raw.close()
        finally:
            super().close()


class _DeadlineSocket:
    def __init__(self, sock, deadline, clock):
        self.socket, self.deadline, self.clock = sock, deadline, clock

    def __getattr__(self, name):
        return getattr(self.socket, name)

    def makefile(self, mode, buffering=None):
        if mode != "rb":
            raise ValueError("HTTP response requires a binary reader")
        raw = self.socket.makefile(mode, buffering=0)
        reader = _DeadlineReader(raw, self.socket, self.deadline, self.clock)
        return io.BufferedReader(reader)


class _BoundedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, *args, deadline, clock, before_dispatch=None, public_only=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.deadline, self.clock = deadline, clock
        self.before_dispatch, self.public_only = before_dispatch, public_only
        self._connecting, self._admitted = False, False
        self._create_connection = self._connect_address

    def _connect_address(self, target, timeout, source_address=None):
        addresses = resolve_bounded(socket.getaddrinfo, target[0], target[1], _remaining(self.deadline, self.clock))
        if self.public_only and any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
            raise TransportError("source connection must use public addresses")
        failure = None
        for family, kind, protocol, _, destination in addresses:
            remaining = _remaining(self.deadline, self.clock)
            sock = socket.socket(family, kind, protocol)
            try:
                sock.settimeout(remaining)
                if source_address:
                    sock.bind(source_address)
                sock.connect(destination)
                _remaining(self.deadline, self.clock)
                return _DeadlineSocket(sock, self.deadline, self.clock)
            except OSError as exc:
                failure = exc
                sock.close()
            except BaseException:
                sock.close()
                raise
        raise TransportError("provider connection unavailable") from failure

    def connect(self):
        self._connecting = True
        try:
            # Keep stdlib CONNECT/proxy behavior, then its configured TLS context
            # and hostname verification. DNS is resolved once per socket attempt.
            http.client.HTTPConnection.connect(self)
            self.sock.settimeout(_remaining(self.deadline, self.clock))
            hostname = self._tunnel_host or self.host
            secured = self._context.wrap_socket(self.sock.socket, server_hostname=hostname)
            self.sock = _DeadlineSocket(secured, self.deadline, self.clock)
            _remaining(self.deadline, self.clock)
        finally:
            self._connecting = False

    def send(self, data):
        if self.sock is None:
            self.connect()
        self.sock.settimeout(_remaining(self.deadline, self.clock))
        if not self._connecting and not self._admitted:
            # DNS/TLS may take time. Recheck controls/funding before the first
            # provider HTTP byte, with no background worker able to dispatch later.
            if self.before_dispatch:
                self.before_dispatch()
            self.sock.settimeout(_remaining(self.deadline, self.clock))
            self._admitted = True
        super().send(data)


class _BoundedHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, clock):
        super().__init__()
        self.clock = clock

    def https_open(self, request):
        def connection(host, **kwargs):
            return _BoundedHTTPSConnection(host, deadline=request.widemind_deadline, clock=self.clock,
                before_dispatch=request.widemind_before_dispatch,
                # A CONNECT proxy resolves the origin itself; its destination
                # policy remains the configured host/proxy boundary.
                public_only=request.widemind_public_only and not request._tunnel_host, **kwargs)
        return self.do_open(connection, request, context=self._context)


class HTTPS:
    def __init__(self, *, clock=time.monotonic):
        # Do not disable proxies or TLS verification. urllib honors the host's
        # proxy/CA trust configuration; credentials are per-request headers only.
        self.opener = urllib.request.build_opener(_NoRedirect(), _BoundedHTTPSHandler(clock))
        self.clock = clock

    def request(self, method, url, *, headers=None, body=None, timeout=20, maximum_bytes=262144,
                before_dispatch=None, public_only=False):
        if not url.startswith("https://"):
            raise ValueError("HTTPS required")
        deadline = self.clock() + timeout
        request = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
        request.widemind_deadline = deadline
        request.widemind_before_dispatch = before_dispatch
        request.widemind_public_only = public_only
        try:
            try:
                response = self.opener.open(request, timeout=timeout)
            except urllib.error.HTTPError as exc:
                response = exc
            with response:
                chunks, size = [], 0
                reader = getattr(response, "read1", response.read)
                while True:
                    remaining = deadline - self.clock()
                    if remaining <= 0:
                        raise TransportError("provider whole-request deadline exceeded")
                    # urllib's socket timeout is otherwise per read; a slow drip
                    # can keep read(N) alive forever. read1 and a shrinking socket
                    # timeout enforce the monotonic whole-response deadline.
                    obj = response
                    for attribute in ("fp", "raw", "_sock"):
                        obj = getattr(obj, attribute, None)
                        if obj is None:
                            break
                        setter = getattr(obj, "settimeout", None)
                        if callable(setter):
                            setter(remaining)
                    piece = reader(min(16384, maximum_bytes + 1 - size))
                    if self.clock() > deadline:
                        raise TransportError("provider whole-request deadline exceeded")
                    if not piece:
                        break
                    size += len(piece)
                    if size > maximum_bytes:
                        raise TransportError("provider response exceeds limit")
                    chunks.append(piece)
                return Response(response.status, {k.lower(): v for k, v in response.headers.items()}, b"".join(chunks))
        except (OSError, urllib.error.URLError, TimeoutError, http.client.HTTPException) as exc:
            raise TransportError("provider transport unavailable") from exc
