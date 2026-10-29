"""Small bounded HTTPS transport. Inherited proxy and CA configuration are preserved."""
from dataclasses import dataclass, field
import json
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


class HTTPS:
    def __init__(self, *, clock=time.monotonic):
        # Do not disable proxies or TLS verification. urllib honors the host's
        # proxy/CA trust configuration; credentials are per-request headers only.
        self.opener = urllib.request.build_opener(_NoRedirect())
        self.clock = clock

    def request(self, method, url, *, headers=None, body=None, timeout=20, maximum_bytes=262144):
        if not url.startswith("https://"):
            raise ValueError("HTTPS required")
        deadline = self.clock() + timeout
        request = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
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
        except (OSError, urllib.error.URLError, TimeoutError) as exc:
            raise TransportError("provider transport unavailable") from exc
