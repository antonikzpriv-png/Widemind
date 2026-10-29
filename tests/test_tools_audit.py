"""Offline regressions for trust, recovery pricing, and transport admission."""
from dataclasses import replace
import json
import io
import socket
import threading
import time
import unittest
from unittest.mock import patch

import test_tools as fixtures
from widemind.tools import GmailClient, LiveTools, OpenAIResearch
from widemind.tools.gmail import normalize_message
from widemind.tools.gmail import rfc_message_id
from widemind.tools.http import HTTPS, TransportError, _BoundedHTTPSConnection


class ToolsAuditTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ToolsTests()
        self.fixture.setUp()

    def tearDown(self):
        self.fixture.tearDown()

    def test_authentication_results_ignores_comments_and_quoted_properties(self):
        message = fixtures.inbox_message()
        for header in (
            'mx.google.com; dmarc=fail ( dmarc=pass ) header.from=example.invalid',
            'mx.google.com; spf=pass smtp.mailfrom=" dmarc=pass header.from=example.invalid "@attacker.invalid; dmarc=fail header.from=example.invalid',
            'mx.google.com; dmarc=pass header.from=attacker.invalid; spf=pass header.from=example.invalid',
            'mx.google.com; dmarc=pass header.from=example.invalid; dmarc=fail header.from=example.invalid',
        ):
            with self.subTest(header=header):
                message['payload']['headers'][1]['value'] = header
                self.assertFalse(normalize_message(message)['sender_verified'])
        message['payload']['headers'][1]['value'] = 'mx.google.com; dmarc=pass (p=NONE sp=NONE dis=NONE) header.from=example.invalid; spf=pass smtp.mailfrom=sender@example.invalid'
        self.assertTrue(normalize_message(message)['sender_verified'])

    def test_false_result_in_a_comment_cannot_confirm_delivery(self):
        fixture = self.fixture
        self.assertEqual(fixture.mail().data['delivery_state'], 'accepted')
        message = fixtures.inbox_message(reply_to=rfc_message_id('TEST_MAIL'))
        message['payload']['headers'][1]['value'] = 'mx.google.com; dmarc=fail ( dmarc=pass ) header.from=example.invalid'
        fixture.transport.inbox = [message]
        read = fixture.execute('gmail.read', 'READ', {})
        self.assertEqual(read.state, 'succeeded')
        self.assertEqual(read.data['delivery_updates'], [])
        self.assertEqual(fixture.tools.reconcile(action_id='TEST_MAIL').data['delivery_state'], 'accepted')

    def test_lost_response_uses_original_prices_after_configuration_changes(self):
        fixture = self.fixture
        fixture.transport.openai_uncertain = True
        fixture.transport.cached_tokens = 80
        self.assertEqual(fixture.execute().state, 'unknown')
        fixture.tools.close()
        config = replace(fixture.research.config, input_micro_usd_per_million_tokens=1,
                         output_micro_usd_per_million_tokens=1, cached_input_micro_usd_per_million_tokens=0)
        research = OpenAIResearch(config, transport=fixture.transport, resolver=fixtures.public_dns)
        fixture.tools = LiveTools(fixture.path, economy=fixture.economy, runtime=fixture.runtime,
                                  research=research, mode='test')
        self.assertEqual(fixture.tools.reconcile(action_id='TEST_ACTION').state, 'succeeded')
        self.assertEqual(fixture.economy.settlements[-1]['actual_micro_usd'], 80)
        self.assertEqual(fixture.model_calls(), 1)

    def test_legacy_unresolved_context_cannot_be_repriced_as_final(self):
        fixture = self.fixture
        fixture.transport.openai_uncertain = True
        self.assertEqual(fixture.execute().state, 'unknown')
        context = json.loads(fixture.tools._row('TEST_ACTION')['context'])
        context.pop('pricing', None)
        fixture.tools.db.execute('UPDATE tool_actions SET context=? WHERE action_id=?',
                                 (json.dumps(context), 'TEST_ACTION'))
        self.assertEqual(fixture.tools.reconcile(action_id='TEST_ACTION').state, 'unknown')
        self.assertEqual(fixture.economy.settlements, [])

    def test_inbox_revocation_during_oauth_blocks_profile_and_inbox_reads(self):
        fixture = self.fixture
        original = fixture.transport.request
        def revoke(method, url, **kwargs):
            result = original(method, url, **kwargs)
            if url == GmailClient.TOKEN:
                fixture.runtime.disabled.add('gmail.read')
                fixture.runtime.generation += 1
            return result
        fixture.transport.request = revoke
        self.assertNotEqual(fixture.execute('gmail.read', 'READ', {}).state, 'succeeded')
        self.assertEqual([url for _, url, _ in fixture.transport.requests], [GmailClient.TOKEN])

    def test_inbox_revocation_between_messages_blocks_remaining_fetches(self):
        fixture = self.fixture
        fixture.transport.inbox = [fixtures.inbox_message('one'), fixtures.inbox_message('two')]
        original = fixture.transport.request
        def revoke(method, url, **kwargs):
            result = original(method, url, **kwargs)
            if '/messages/one?' in url:
                fixture.runtime.current = 'stopped'
                fixture.runtime.generation += 1
            return result
        fixture.transport.request = revoke
        self.assertNotEqual(fixture.execute('gmail.read', 'READ', {}).state, 'succeeded')
        self.assertFalse(any('/messages/two?' in url for _, url, _ in fixture.transport.requests))

    def test_timed_out_opening_retains_research_liability_and_never_dispatches_later(self):
        fixture = self.fixture
        release, finished = threading.Event(), threading.Event()
        def resolver(*args, **kwargs):
            try:
                release.wait(2)
                return fixtures.public_dns('example.com', 443)
            finally:
                finished.set()
        original = fixture.transport.request
        def transport(method, url, **kwargs):
            if url == OpenAIResearch.API:
                kwargs['timeout'] = .03
                return HTTPS().request(method, url, **kwargs)
            return original(method, url, **kwargs)
        fixture.transport.request = transport
        with patch('socket.getaddrinfo', resolver), patch('socket.socket') as socket_factory:
            try:
                self.assertEqual(fixture.execute().state, 'unknown')
                self.assertEqual(fixture.economy.settlements, [])
                self.assertEqual(fixture.economy.releases, [])
                release.set()
                self.assertTrue(finished.wait(1))
                socket_factory.assert_not_called()
            finally:
                release.set()


class FixtureSocket:
    """An in-memory socket boundary; no real connections or TLS traffic."""
    def __init__(self, *, response=b'HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n', now=None):
        self.sent, self.timeouts, self.destinations = [], [], []
        self.response, self.now = response, now

    def settimeout(self, timeout):
        self.timeouts.append(timeout)

    def setsockopt(self, *args):
        pass

    def connect(self, destination):
        self.destinations.append(destination)

    def sendall(self, data):
        self.sent.append(data)

    def makefile(self, *args, **kwargs):
        if self.now is None:
            return io.BytesIO(self.response)
        source, now = io.BytesIO(self.response), self.now
        class Drip(io.RawIOBase):
            def readable(self):
                return True
            def readinto(self, buffer):
                now[0] += .4
                piece = source.read(1)
                buffer[:len(piece)] = piece
                return len(piece)
        return Drip()

    def close(self):
        pass


class FixtureTLSContext:
    def __init__(self, *, after_handshake=None):
        self.hostnames, self.after_handshake = [], after_handshake

    def wrap_socket(self, sock, *, server_hostname):
        self.hostnames.append(server_hostname)
        if self.after_handshake:
            self.after_handshake()
        return sock


class HTTPAuditTests(unittest.TestCase):
    def test_dispatch_guard_rechecks_after_dns_and_tls_before_any_provider_byte(self):
        sock = FixtureSocket()
        allowed = [True]
        context = FixtureTLSContext(after_handshake=lambda: allowed.__setitem__(0, False))
        def guard():
            if not allowed[0]:
                raise ValueError('offline control-plane revocation')
        connection = _BoundedHTTPSConnection('example.com', context=context,
            deadline=time.monotonic() + 1, clock=time.monotonic, before_dispatch=guard)
        with patch('socket.getaddrinfo', fixtures.public_dns), patch('socket.socket', return_value=sock):
            with self.assertRaisesRegex(ValueError, 'revocation'):
                connection.request('POST', '/', body=b'private')
        self.assertEqual(sock.sent, [])
        self.assertEqual(context.hostnames, ['example.com'])
        connection.close()

    def test_proxy_tunnel_and_target_tls_verification_are_preserved(self):
        sock, context = FixtureSocket(), FixtureTLSContext()
        connection = _BoundedHTTPSConnection('proxy.example', 8080, context=context,
            deadline=time.monotonic() + 1, clock=time.monotonic)
        connection.set_tunnel('target.example', 443, {'Proxy-Authorization': 'TEST_PROXY_ONLY'})
        with patch('socket.getaddrinfo', fixtures.public_dns), patch('socket.socket', return_value=sock):
            connection.request('GET', '/')
        self.assertIn(b'CONNECT target.example:443', sock.sent[0])
        self.assertIn(b'Proxy-Authorization: TEST_PROXY_ONLY', sock.sent[0])
        self.assertNotIn(b'TEST_PROXY_ONLY', b''.join(sock.sent[1:]))
        self.assertEqual(context.hostnames, ['target.example'])
        self.assertEqual(sock.destinations, [('93.184.216.34', 8080)])
        connection.close()

    def test_slow_drip_during_status_headers_hits_the_opening_deadline(self):
        now = [0.0]
        sock, context = FixtureSocket(now=now), FixtureTLSContext()
        connection = _BoundedHTTPSConnection('example.com', context=context, deadline=1, clock=lambda: now[0])
        with patch('socket.getaddrinfo', fixtures.public_dns), patch('socket.socket', return_value=sock):
            connection.request('GET', '/')
            with self.assertRaises(TransportError):
                connection.getresponse()
        self.assertLessEqual(now[0], 1.3)
        self.assertLess(sock.timeouts[-1], 1)
        connection.close()

    def test_direct_source_connect_rechecks_public_addresses_after_dns_preflight(self):
        connection = _BoundedHTTPSConnection('example.com', context=FixtureTLSContext(),
            deadline=time.monotonic() + 1, clock=time.monotonic, public_only=True)
        def rebound(host, port, **kwargs):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', port))]
        with patch('socket.getaddrinfo', rebound), patch('socket.socket') as socket_factory:
            with self.assertRaises(TransportError):
                connection.request('GET', '/')
            socket_factory.assert_not_called()
        connection.close()

    def test_opening_dns_is_bounded_and_cannot_send_after_timeout(self):
        release = threading.Event()
        finished = threading.Event()
        def resolver(*args, **kwargs):
            try:
                release.wait(2)
                return fixtures.public_dns('example.com', 443)
            finally:
                finished.set()
        started = time.monotonic()
        with patch('socket.getaddrinfo', resolver), patch('socket.socket') as socket_factory:
            try:
                with self.assertRaises(TransportError):
                    HTTPS().request('POST', 'https://example.com/', body=b'private', timeout=.03)
                self.assertLess(time.monotonic() - started, .5)
                release.set()
                self.assertTrue(finished.wait(1))
                socket_factory.assert_not_called()
            finally:
                release.set()

    def test_stalled_dns_has_a_fixed_worker_bound(self):
        release = threading.Event()
        entered = []
        done = []
        def resolver(*args, **kwargs):
            entered.append(threading.current_thread())
            release.wait(2)
            done.append(True)
            raise socket.gaierror('offline fixture')
        with patch('socket.getaddrinfo', resolver):
            try:
                for _ in range(8):
                    with self.assertRaises(TransportError):
                        HTTPS().request('GET', 'https://example.com/', timeout=.02)
                self.assertLessEqual(len(entered), 4)
            finally:
                release.set()
                for worker in entered:
                    worker.join(1)
        self.assertEqual(len(done), len(entered))
