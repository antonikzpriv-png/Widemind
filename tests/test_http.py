"""Actual controlled loopback fixtures; no external HTTP or credentials used."""
import gzip
import io
import json
import os
import select
import shutil
import socket
import socketserver
import ssl
import subprocess
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import widemind.http as transport
from widemind.http import HTTPTransport
from widemind.tools.contracts import ToolError


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_):
        pass

    def do_GET(self):
        self.server.requests.append((self.command, self.path, dict(self.headers), b""))
        try:
            if self.path.startswith("/early"):
                self.wfile.write(b"HTTP/1.1 103 Early Hints\r\nLink: </style.css>; rel=preload\r\n\r\n")
            if self.path.startswith("/notmodified"):
                self.send_response(304)
                self.send_header("Content-Length", "1000000")
                self.end_headers()
                return
            if self.path.startswith("/tlsabort"):
                self.send_response(200)
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(b"partial close-delimited content")
                self.wfile.flush()
                self.connection.shutdown(socket.SHUT_RDWR)
                self.close_connection = True
                return
            if self.path.startswith("/slowheaders"):
                time.sleep(.25)
            if self.path.startswith("/drop"):
                self.close_connection = True
                return
            if self.path.startswith("/redirect"):
                self.send_response(302)
                self.send_header("Location", "http://127.0.0.1:1/private")
                data = b"redirect"
            elif self.path.startswith("/chunked"):
                self.send_response(200)
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                self.wfile.write(b"3\r\nabc\r\n3\r\ndef\r\n0\r\n\r\n")
                self.wfile.flush()
                return
            elif self.path.startswith("/gzip"):
                self.send_response(200)
                self.send_header("Content-Encoding", "gzip")
                data = gzip.compress(b"decoded fixture data")
            elif self.path.startswith("/headers"):
                self.send_response(200)
                self.send_header("X-Large", "x" * 70_000)
                data = b"body"
            else:
                self.send_response(200)
                data = b"0123456789" if self.path.startswith(("/slowbody", "/oversize", "/truncated")) else b"fixture-response"
            self.send_header("Content-Length", str(len(data)))
            self.send_header("X-MiXeD-Case", "lowercase-output")
            self.send_header("Connection", "close")
            self.end_headers()
            if self.path.startswith("/truncated"):
                self.wfile.write(data[:2])
                self.wfile.flush()
                self.close_connection = True
            elif self.path.startswith("/slowbody"):
                for byte in data:
                    self.wfile.write(bytes([byte]))
                    self.wfile.flush()
                    time.sleep(.04)
            else:
                self.wfile.write(data)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ssl.SSLError):
            pass

    def do_POST(self):
        data = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.server.requests.append((self.command, self.path, dict(self.headers), data))
        if self.path.startswith("/drop"):
            self.close_connection = True
            return
        self.send_response(201)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")


@contextmanager
def server(tls=None):
    instance = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    instance.daemon_threads, instance.requests = True, []
    if tls is not None:
        instance.socket = tls.wrap_socket(instance.socket, server_side=True)
    thread = threading.Thread(target=instance.serve_forever, kwargs={"poll_interval": .02}, daemon=True)
    thread.start()
    try:
        yield instance, instance.server_address[1]
    finally:
        instance.shutdown()
        instance.server_close()
        thread.join(timeout=1)


class ProxyHandler(socketserver.StreamRequestHandler):
    def handle(self):
        try:
            line = self.rfile.readline(65536).decode("ascii").strip()
            headers = {}
            while True:
                header = self.rfile.readline(65536)
                if header in (b"\r\n", b"\n", b""):
                    break
                key, value = header.decode("latin-1").split(":", 1)
                headers[key.lower()] = value.strip()
            self.server.requests.append((line, headers))
            if not line.startswith("CONNECT "):
                self.wfile.write(b"HTTP/1.1 200 OK\r\nContent-Length: 13\r\nConnection: close\r\n\r\nproxy-fixture")
                self.wfile.flush()
                return
            if not self.server.forward_port:
                self.wfile.write(b"HTTP/1.1 403 Rejected\r\nContent-Length: 0\r\n\r\n")
                self.wfile.flush()
                return
            # The fixture only forwards to its explicitly supplied loopback
            # TLS server; never resolve or dial the CONNECT target.
            with socket.create_connection(("127.0.0.1", self.server.forward_port), timeout=2) as remote:
                self.wfile.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
                self.wfile.flush()
                self.request.settimeout(2)
                end = time.monotonic() + 3
                while time.monotonic() < end:
                    waiting = [self.request, remote]
                    ready = select.select(waiting, [], [], .05)[0]
                    if isinstance(self.request, ssl.SSLSocket) and self.request.pending():
                        ready = list(set(ready + [self.request]))
                    for source in ready:
                        data = source.recv(65536)
                        if not data:
                            return
                        (remote if source is self.request else self.request).sendall(data)
        except (OSError, ValueError):
            return


@contextmanager
def proxy_server(*, tls=None, forward_port=None):
    class Proxy(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True
    instance = Proxy(("127.0.0.1", 0), ProxyHandler)
    instance.requests, instance.forward_port = [], forward_port
    if tls:
        instance.socket = tls.wrap_socket(instance.socket, server_side=True)
    thread = threading.Thread(target=instance.serve_forever, kwargs={"poll_interval": .02}, daemon=True)
    thread.start()
    try:
        yield instance, instance.server_address[1]
    finally:
        instance.shutdown()
        instance.server_close()
        thread.join(timeout=1)


def address(ip, port):
    return [(socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, port, 0, 0))] if ":" in ip else [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, port))]


class HTTPTests(unittest.TestCase):
    def setUp(self):
        # These fixtures simulate a direct runtime without modifying inherited
        # environment variables or connecting to any public destination.
        self.proxy_environment = patch.object(transport, "getproxies_environment", return_value={})
        self.proxy_environment.start()
        self.addCleanup(self.proxy_environment.stop)

    def fixture(self, origin):
        return HTTPTransport({"network": {"trusted_fixture_origins": [origin]}})

    def test_actual_get_lowercase_headers_and_no_redirect_following(self):
        with server() as (instance, port):
            origin = f"http://127.0.0.1:{port}"
            client = self.fixture(origin)
            response = client.request("GET", origin + "/redirect?private=fixture-query", public_only=True)
            self.assertEqual(response.status, 302)
            self.assertEqual(response.body, b"redirect")
            self.assertEqual(response.headers["x-mixed-case"], "lowercase-output")
            self.assertEqual(len(instance.requests), 1)
            self.assertEqual(instance.requests[0][2]["host"], f"127.0.0.1:{port}")
            cached = client.request("GET", origin + "/notmodified", max_bytes=1, public_only=True)
            self.assertEqual((cached.status, cached.body), (304, b""))

    def test_early_hints_are_consumed_without_dropping_final_response_or_retrying(self):
        with server() as (instance, port):
            origin = f"http://127.0.0.1:{port}"
            response = self.fixture(origin).request("GET", origin + "/early", public_only=True)
            self.assertEqual(response.status, 200)
            self.assertEqual(response.body, b"fixture-response")
            self.assertEqual(len(instance.requests), 1)

    def test_actual_post_failure_is_never_retried_and_errors_hide_secrets(self):
        with server() as (instance, port):
            origin = f"http://127.0.0.1:{port}"
            with self.assertRaises(ToolError) as failure:
                self.fixture(origin).request("POST", origin + "/drop?token=fixture-query-secret",
                                             headers={"Authorization": "Bearer fixture-header-secret"}, body=b"fixture-body-secret", public_only=True)
            self.assertEqual(len(instance.requests), 1)
            for secret in ("fixture-query-secret", "fixture-header-secret", "fixture-body-secret", origin):
                self.assertNotIn(secret, str(failure.exception) + repr(failure.exception))

    def test_actual_body_bounds_chunked_truncation_headers_and_no_decompression(self):
        with server() as (_, port):
            origin = f"http://127.0.0.1:{port}"
            client = self.fixture(origin)
            self.assertEqual(client.request("GET", origin + "/chunked", public_only=True).body, b"abcdef")
            for path, limit in (("/oversize", 5), ("/chunked", 5), ("/headers", 100)):
                with self.subTest(path=path), self.assertRaises(ToolError) as failure:
                    client.request("GET", origin + path, max_bytes=limit, public_only=True)
                self.assertEqual(failure.exception.code, "output_limit")
            with self.assertRaises(ToolError) as failure:
                client.request("GET", origin + "/truncated", public_only=True)
            self.assertEqual(failure.exception.code, "http_response")
            compressed = client.request("GET", origin + "/gzip", public_only=True)
            self.assertEqual(gzip.decompress(compressed.body), b"decoded fixture data")
            self.assertNotEqual(compressed.body, b"decoded fixture data")

    def test_absolute_deadline_covers_slow_headers_and_trickling_body(self):
        with server() as (_, port):
            origin = f"http://127.0.0.1:{port}"
            for path in ("/slowheaders", "/slowbody"):
                started = time.monotonic()
                with self.subTest(path=path), self.assertRaises(ToolError) as failure:
                    self.fixture(origin).request("GET", origin + path, timeout=.09, public_only=True)
                self.assertEqual(failure.exception.code, "timeout")
                self.assertLess(time.monotonic() - started, .25)

    def test_cancel_before_request_and_while_waiting_for_headers(self):
        with server() as (instance, port):
            origin = f"http://127.0.0.1:{port}"
            stopped = threading.Event()
            def cancel():
                if stopped.is_set():
                    raise ToolError("cancelled", "fixture-private-cancel-message")
            stopped.set()
            with self.assertRaises(ToolError):
                self.fixture(origin).request("GET", origin, cancel=cancel, public_only=True)
            self.assertFalse(instance.requests)
            stopped.clear()
            timer = threading.Timer(.04, stopped.set)
            timer.start()
            self.addCleanup(timer.cancel)
            started = time.monotonic()
            with self.assertRaises(ToolError) as failure:
                self.fixture(origin).request("GET", origin + "/slowheaders", timeout=1, cancel=cancel, public_only=True)
            self.assertEqual(failure.exception.code, "cancelled")
            self.assertNotIn("fixture-private-cancel-message", str(failure.exception))
            self.assertLess(time.monotonic() - started, .20)

    def test_private_ambiguous_transition_and_invalid_urls_rejected_without_dial(self):
        urls = ["http://127.0.0.1", "http://10.0.0.1", "http://172.16.0.1", "http://192.168.1.1",
                "http://169.254.169.254", "http://100.64.0.1", "http://198.18.0.1", "http://224.0.0.1",
                "http://255.255.255.255", "http://192.0.2.1", "http://127.1", "http://0177.0.0.1",
                "http://0x7f000001", "http://2130706433", "http://[::]", "http://[::1]", "http://[fc00::1]",
                "http://[fe80::1]", "http://[ff02::1]", "http://[::ffff:8.8.8.8]", "http://[64:ff9b::808:808]",
                "http://[2002:0808:0808::1]", "http://[2001:0000:4136:e378:8000:63bf:3fff:fdd2]",
                "http://[2001:db8::1]", "http://[fe80::1%25eth0]", "http://example.com\\@127.0.0.1",
                "http://user:fixture-password@example.com", "http://example.com/#fragment", "http://example.com:0/",
                "http://example.com\r\n/", "file:///etc/passwd"]
        with patch.object(transport, "_RawStream") as dial:
            for url in urls:
                with self.subTest(url=url), self.assertRaises(ToolError):
                    HTTPTransport().request("GET", url, public_only=True)
            dial.assert_not_called()

    def test_dns_mixed_answers_fail_and_direct_connection_pins_once_preserving_host(self):
        mixed = address("8.8.8.8", 80) + address("127.0.0.1", 80)
        with patch.object(socket, "getaddrinfo", return_value=mixed) as dns, patch.object(transport, "_RawStream") as dial:
            with self.assertRaises(ToolError) as failure:
                HTTPTransport().request("GET", "http://public.example/", public_only=True)
            self.assertEqual(failure.exception.code, "private_destination")
            dial.assert_not_called()
            self.assertEqual(dns.call_count, 1)

        seen = []
        class PinnedFixtureStream:
            def __init__(self, endpoint, deadline, limit):
                seen.append(endpoint)
                self.input = io.BytesIO(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
                self.output = bytearray()
            def read(self, count):
                return self.input.read(count)
            def write(self, data):
                self.output.extend(data)
                seen.append(bytes(data))
            def close(self):
                pass
        with patch.object(socket, "getaddrinfo", return_value=address("8.8.8.8", 8080)) as dns, patch.object(transport, "_RawStream", PinnedFixtureStream):
            self.assertEqual(HTTPTransport().request("GET", "http://public.example:8080/path", public_only=True).body, b"ok")
            self.assertEqual(dns.call_count, 1)
        self.assertEqual(seen[0][4], ("8.8.8.8", 8080))
        self.assertIn(b"host: public.example:8080\r\n", b"".join(x for x in seen if isinstance(x, bytes)))

    def test_proxy_requires_exact_boolean_guard_and_preserves_proxy_route_and_authority(self):
        with proxy_server() as (instance, port):
            proxy = {"http": f"http://fixture-user:fixture-proxy-secret@127.0.0.1:{port}"}
            with patch.object(transport, "getproxies_environment", return_value=proxy), patch.object(socket, "getaddrinfo", return_value=address("8.8.8.8", 80)):
                for flag in (None, False, "true", 1):
                    with self.subTest(flag=flag), self.assertRaises(ToolError) as failure:
                        HTTPTransport({"network": {"public_proxy_guarded": flag}}).request("GET", "http://public.example/path?query=private", public_only=True)
                    self.assertEqual(failure.exception.code, "proxy_guard_required")
                self.assertFalse(instance.requests)
                response = HTTPTransport({"network": {"public_proxy_guarded": True}}).request("GET", "http://public.example/path?query=private", public_only=True)
            self.assertEqual(response.body, b"proxy-fixture")
            self.assertEqual(instance.requests[0][0], "GET http://public.example/path?query=private HTTP/1.1")
            self.assertEqual(instance.requests[0][1]["host"], "public.example")
            self.assertTrue(instance.requests[0][1]["proxy-authorization"].startswith("Basic "))

    def test_no_proxy_is_honored_and_exact_fixture_does_not_allow_other_origins(self):
        with server() as (instance, port):
            origin = f"http://127.0.0.1:{port}"
            proxy = {"http": "http://127.0.0.1:1", "no": "127.0.0.1"}
            with patch.object(transport, "getproxies_environment", return_value=proxy):
                self.assertEqual(self.fixture(origin).request("GET", origin, public_only=True).status, 200)
                with self.assertRaises(ToolError):
                    self.fixture(origin).request("GET", f"http://127.0.0.1:{port + 1}", public_only=True)
            self.assertEqual(len(instance.requests), 1)

    def test_exact_loopback_fixture_can_use_proxy_without_disabling_it(self):
        with proxy_server() as (instance, port):
            origin = "http://127.0.0.1:49123"
            with patch.object(transport, "getproxies_environment", return_value={"http": f"http://127.0.0.1:{port}"}):
                self.assertEqual(self.fixture(origin).request("GET", origin + "/fixture", public_only=True).body, b"proxy-fixture")
            self.assertEqual(instance.requests[0][0], origin.replace("http://", "GET http://") + "/fixture HTTP/1.1")

    def test_dns_timeout_and_cancel_return_without_waiting_for_stuck_resolver(self):
        release = threading.Event()
        def stalled(*args, **kwargs):
            release.wait(1)
            return address("8.8.8.8", 80)
        try:
            with patch.object(socket, "getaddrinfo", side_effect=stalled), patch.object(transport, "_RawStream") as dial:
                started = time.monotonic()
                with self.assertRaises(ToolError) as failure:
                    HTTPTransport().request("GET", "http://public.example", public_only=True, timeout=.06)
                self.assertEqual(failure.exception.code, "timeout")
                self.assertLess(time.monotonic() - started, .20)
                dial.assert_not_called()
        finally:
            release.set()

    def test_dns_worker_capacity_is_bounded_and_errors_are_sanitized(self):
        semaphore = threading.BoundedSemaphore(1)
        semaphore.acquire()
        with patch.object(transport, "_DNS_SLOTS", semaphore):
            with self.assertRaises(ToolError) as failure:
                HTTPTransport().request("GET", "http://public.example", public_only=True)
            self.assertEqual(failure.exception.code, "dns_busy")
        with patch.object(socket, "getaddrinfo", side_effect=OSError("fixture-dns-secret")):
            with self.assertRaises(ToolError) as failure:
                HTTPTransport().request("GET", "http://public.example/?token=fixture-query-secret", public_only=True)
            self.assertNotIn("fixture-dns-secret", str(failure.exception))
            self.assertNotIn("fixture-query-secret", str(failure.exception))

    def test_header_framing_and_invalid_request_arguments_rejected_before_dial(self):
        invalid = [{"Host": "127.0.0.1"}, {"Content-Length": "0"}, {"Transfer-Encoding": "chunked"}, {"Connection": "upgrade"},
                   {"Authorization": "x", "authorization": "y"}, {"X-Header": "value\r\nInjected: yes"}, {"bad name": "x"}]
        with patch.object(transport, "_RawStream") as dial:
            for headers in invalid:
                with self.subTest(headers=headers), self.assertRaises(ToolError):
                    HTTPTransport().request("GET", "http://8.8.8.8", headers=headers, public_only=True)
            for kwargs in ({"timeout": float("inf")}, {"timeout": 0}, {"timeout": True}, {"max_bytes": 0}, {"max_bytes": True}, {"body": "text"}, {"public_only": "yes"}):
                with self.subTest(kwargs=kwargs), self.assertRaises(ToolError):
                    HTTPTransport().request("GET", "http://8.8.8.8", **kwargs)
            dial.assert_not_called()


@unittest.skipUnless(shutil.which("openssl"), "OpenSSL command is needed only to generate a temporary fixture certificate.")
class TLSHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.cert, cls.key = Path(cls.temp.name) / "fixture.crt", Path(cls.temp.name) / "fixture.key"
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(cls.key), "-out", str(cls.cert), "-days", "1", "-subj", "/CN=fixture.example", "-addext", "subjectAltName=DNS:fixture.example,DNS:localhost,IP:127.0.0.1"], check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.sni = []
        self.tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.tls.load_cert_chain(self.cert, self.key)
        self.tls.set_servername_callback(lambda sock, name, context: self.sni.append(name))
        self.env = patch.dict(os.environ, {"SSL_CERT_FILE": str(self.cert)})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.proxies = patch.object(transport, "getproxies_environment", return_value={})
        self.proxies.start()
        self.addCleanup(self.proxies.stop)
        self.dns = patch.object(socket, "getaddrinfo", side_effect=lambda host, port, *args, **kwargs: address("127.0.0.1", port))
        self.mock_dns = self.dns.start()
        self.addCleanup(self.dns.stop)

    def test_actual_tls_pins_address_but_verifies_original_sni_host(self):
        with server(self.tls) as (instance, port):
            origin = f"https://fixture.example:{port}"
            client = HTTPTransport({"network": {"trusted_fixture_origins": [origin]}})
            self.assertEqual(client.request("GET", origin + "/tls", public_only=True).body, b"fixture-response")
            self.assertEqual(self.mock_dns.call_count, 1)
            self.assertEqual(self.sni, ["fixture.example"])
            self.assertEqual(instance.requests[0][2]["host"], f"fixture.example:{port}")
            wrong = f"https://wrong.example:{port}"
            with self.assertRaises(ToolError) as failure:
                HTTPTransport({"network": {"trusted_fixture_origins": [wrong]}}).request("GET", wrong, public_only=True)
            self.assertEqual(failure.exception.code, "tls_verification")

    def test_actual_connect_tunnel_and_nested_https_proxy_tls(self):
        for encrypted_proxy in (False, True):
            with self.subTest(encrypted_proxy=encrypted_proxy), server(self.tls) as (instance, target_port), proxy_server(tls=self.tls if encrypted_proxy else None, forward_port=target_port) as (proxy, proxy_port):
                origin = f"https://fixture.example:{target_port}"
                proxy_url = ("https" if encrypted_proxy else "http") + f"://fixture.example:{proxy_port}"
                with patch.object(transport, "getproxies_environment", return_value={"https": proxy_url}):
                    response = HTTPTransport({"network": {"trusted_fixture_origins": [origin]}}).request("GET", origin + "/tunnel", public_only=True, timeout=2)
                self.assertEqual(response.body, b"fixture-response")
                self.assertEqual(proxy.requests[0][0], f"CONNECT fixture.example:{target_port} HTTP/1.1")
                self.assertEqual(instance.requests[0][2]["host"], f"fixture.example:{target_port}")

    def test_abrupt_tls_eof_never_confirms_close_delimited_body_completion(self):
        with server(self.tls) as (_, port):
            origin = f"https://fixture.example:{port}"
            with self.assertRaises(ToolError) as failure:
                HTTPTransport({"network": {"trusted_fixture_origins": [origin]}}).request("GET", origin + "/tlsabort", public_only=True)
            self.assertEqual(failure.exception.code, "http_transport")


if __name__ == "__main__":
    unittest.main()
