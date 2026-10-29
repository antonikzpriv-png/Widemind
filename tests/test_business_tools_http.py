"""No sockets: exercise the actual shared HTTP admission boundary."""
import io
import socket
from unittest.mock import patch

import pytest
import widemind.http as transport
from widemind.business_tools.http import HTTPS
from widemind.tools.contracts import ToolError


def test_admission_occurs_once_after_pinned_dns_tls_before_origin_bytes():
    events = []
    class Stream:
        def __init__(self, *args):
            events.append("connect")
            self.body = io.BytesIO(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
        def read(self, count):
            return self.body.read(count)
        def write(self, value):
            events.append("write")
        def close(self):
            pass
    def tls(stream, *args):
        events.append("verified-tls")
        return stream
    with patch.object(transport, "getproxies_environment", return_value={}), patch.object(socket, "getaddrinfo", return_value=[(2, 1, 6, "", ("8.8.8.8", 443))]), patch.object(transport, "_RawStream", Stream), patch.object(transport, "_TLSStream", tls):
        result = transport.HTTPTransport().request("POST", "https://public.example/", body=b"private", before_send=lambda: events.append("admission"))
    assert result.body == b"ok"
    assert events[:3] == ["connect", "verified-tls", "admission"]
    assert events.count("admission") == 1
    assert events.count("write") >= 1


def test_denied_late_admission_has_no_request_bytes_and_is_known_unsent():
    writes = []
    class Stream:
        def __init__(self, *args):
            pass
        def write(self, value):
            writes.append(value)
        def close(self):
            pass
    def deny():
        raise ValueError("fixture central gate denied")
    with patch.object(transport, "getproxies_environment", return_value={}), patch.object(socket, "getaddrinfo", return_value=[(2, 1, 6, "", ("8.8.8.8", 443))]), patch.object(transport, "_RawStream", Stream), patch.object(transport, "_TLSStream", side_effect=lambda stream, *args: stream):
        with pytest.raises(ValueError, match="central gate denied"):
            HTTPS().request("POST", "https://public.example/", body=b"private", before_dispatch=deny)
    assert writes == []


def test_invalid_admission_callback_rejected_before_dns():
    with patch.object(socket, "getaddrinfo") as resolve:
        with pytest.raises(ToolError):
            transport.HTTPTransport().request("GET", "https://public.example/", before_send="unsafe")
        resolve.assert_not_called()
