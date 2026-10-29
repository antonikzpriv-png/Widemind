"""Business adapter for the existing DNS-pinned, proxy-aware SDK transport.

This imports no SDK wallet or gateway. Only the HTTP transport and its error type
are shared; the central economy remains the sole execution authority.
"""
from dataclasses import dataclass, field
import json
import queue
import socket
import threading

from widemind.http import HTTPTransport
from widemind.tools.contracts import ToolError

class TransportError(Exception):
    """Provider outcome uncertain; retain the central reservation."""

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



class HTTPS:
    def __init__(self, *, public_proxy_guarded=False):
        self.transport = HTTPTransport({"network": {"public_proxy_guarded": public_proxy_guarded}})

    def request(self, method, url, *, headers=None, body=None, timeout=20,
                maximum_bytes=262144, before_dispatch=None, public_only=False, checkpoint=None):
        if not url.startswith("https://"):
            raise ValueError("HTTPS required")
        denied = []
        def admission():
            if before_dispatch:
                try:
                    before_dispatch()
                except Exception as exc:
                    denied.append(exc)
                    raise
        try:
            result = self.transport.request(method, url, headers=headers, body=body,
                timeout=timeout, max_bytes=maximum_bytes, public_only=public_only,
                before_send=admission, cancel=checkpoint)
            return Response(result.status, dict(result.headers), result.body)
        except ToolError as exc:
            # This callback runs before request bytes, so a definite local denial
            # can remain a known unsent failure. Transport/cancellation failures
            # after admission retain uncertainty and any possible bill.
            if denied:
                raise denied[0]
            raise TransportError("provider transport outcome unavailable") from exc
