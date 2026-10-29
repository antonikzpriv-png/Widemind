"""Actual portal/controller storage with controlled local provider fixtures only."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from widemind.business.conversation import Conversations
from widemind.business.host_server import Host
from widemind.business.store import identity
from test_host_integration import FixtureServices


class ConversationPortalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.providers = FixtureServices()
        self.runtime = self.root/'runtime.sqlite3'
        with sqlite3.connect(self.runtime) as db:
            db.execute('CREATE TABLE runtime_runs(id TEXT PRIMARY KEY,state TEXT,desired TEXT,profile TEXT)')
            db.execute("INSERT INTO runtime_runs VALUES ('run-1','active','run','foundation')")
        self.env = {
            'WIDEMIND_BUSINESS_DB': str(self.root/'business.sqlite3'),
            'WIDEMIND_BUSINESS_TOKEN': 'b'*48, 'WIDEMIND_BUSINESS_MODE': 'test',
            'WIDEMIND_RUNTIME_DATABASE': str(self.runtime),
            'WIDEMIND_TOOLS_URL': self.providers.url, 'WIDEMIND_TOOLS_TOKEN': 't'*48,
            'WIDEMIND_ECONOMY_URL': self.providers.url, 'WIDEMIND_ECONOMY_OPERATOR_TOKEN': 'e'*48,
            'WIDEMIND_PUBLIC_ORIGIN': 'http://127.0.0.1:3000',
        }
        self.host = Host(self.env)

    def tearDown(self):
        self.providers.close()
        self.temp.cleanup()

    def request(self, request_id='natural-1', token='x'*43, message='Could you help me make a graphic for my bakery?'):
        return self.host.handle('/v1/request', {'runId':'run-1', 'requestId':request_id,
            'customerToken':token, 'email':'controlled-customer@example.test', 'message':message})

    def customer(self, token='x'*43):
        return self.host.handle('/v1/customer', {'runId':'run-1', 'token':token})

    def proposal(self, conversation_id, title='Bakery graphic'):
        business = self.host.business('run-1')
        try:
            conversations = Conversations(business)
            with business.store.transaction():
                return conversations._proposal(conversations.thread(conversation_id), 'svg-graphic',
                                               {'title':title, 'description':'A simple bakery sign'}, 0)
        finally:
            business.store.close()

    def test_natural_request_and_messages_are_request_scoped_and_durable(self):
        first = self.request()
        other = self.request('natural-2', 'y'*43, 'private-other-request-canary')
        own = self.customer()
        self.assertEqual(own['conversation']['id'], first['conversationId'])
        self.assertNotEqual(first['conversationId'], other['conversationId'])
        self.assertNotIn('private-other-request-canary', json.dumps(own))
        payload = {'runId':'run-1', 'token':'x'*43, 'messageId':'followup-1',
                   'body':'I need a dark green sign with the bakery name.',
                   'email':'attacker@example.test', 'threadId':other['conversationId'],
                   'conversationId':other['conversationId']}
        for _replay in range(2):
            result = self.host.handle('/v1/message', payload)
            self.assertEqual(result['conversationId'], first['conversationId'])
        restarted = Host(self.env)
        own = restarted.handle('/v1/customer', {'runId':'run-1','token':'x'*43})
        self.assertEqual(len([m for m in own['conversation']['messages'] if m['body']==payload['body']]), 1)
        self.assertNotIn(payload['body'], json.dumps(self.customer('y'*43)))
        self.assertEqual(self.providers.creation_calls, 0)
        self.assertEqual(self.providers.payments, [])

    def test_invalid_or_revoked_token_cannot_append_customer_messages(self):
        self.request()
        for token in ['z'*43, None]:
            with self.assertRaises(ValueError):
                self.host.handle('/v1/message', {'runId':'run-1','token':token,'messageId':'no-auth','body':'My text'})
        self.host.handle('/v1/revoke', {'runId':'run-1','token':'x'*43})
        with self.assertRaises(ValueError):
            self.host.handle('/v1/message', {'runId':'run-1','token':'x'*43,'messageId':'revoked','body':'My text'})

    def test_acceptance_requires_current_exact_terms_and_token_owned_identity(self):
        created = self.request()
        first = self.proposal(created['conversationId'])
        second = self.proposal(created['conversationId'], 'Changed bakery graphic')
        base = {'runId':'run-1','token':'x'*43,'quoteId':second['quoteId'],
                'conversationId':created['conversationId'],'version':second['version'],'digest':second['digest']}
        with self.assertRaises((KeyError, ValueError)):
            self.host.handle('/v1/accept', {key:value for key,value in base.items() if key!='version'})
        with self.assertRaises(ValueError):
            self.host.handle('/v1/accept', {**base,'quoteId':first['quoteId'],'version':first['version'],'digest':first['digest']})
        with self.assertRaises(ValueError):
            self.host.handle('/v1/accept', {**base,'digest':'0'*64})
        self.request('another', 'y'*43)
        with self.assertRaises(ValueError):
            self.host.handle('/v1/accept', {**base,'token':'y'*43})
        accepted = self.host.handle('/v1/accept', {**base,'email':'attacker@example.test'})
        self.assertEqual(self.host.handle('/v1/accept', base), accepted)
        own = self.customer()
        self.assertEqual(len(own['orders']), 1)
        self.assertEqual(own['orders'][0]['orderId'], accepted['orderId'])
        self.assertEqual(self.providers.payments, [], 'acceptance creates no payment receipt or cash')
        self.assertEqual(self.providers.creation_calls, 0)

    def test_opt_out_is_immediate_when_stopped_and_needs_no_model(self):
        created = self.request()
        self.proposal(created['conversationId'])
        with sqlite3.connect(self.runtime) as db:
            db.execute("UPDATE runtime_runs SET state='stopped',desired='stop'")
        result = self.host.handle('/v1/opt-out', {'runId':'run-1','token':'x'*43})
        self.assertTrue(result['optedOut'])
        self.assertEqual(self.customer()['conversation']['status'], 'opted_out')
        self.assertEqual(self.providers.creation_calls, 0)

    def test_gmail_portal_access_is_private_single_issue_and_revocable(self):
        business = self.host.business('run-1')
        token = 'g'*43
        try:
            conversation_id = business.conversation_receive(source='gmail', external_id='gmail-message-1',
                thread_id='gmail-thread-1', customer_email='controlled-gmail@example.test',
                body='Could you make a small SVG logo?', authenticated=True)
            with business.store.transaction():
                link = business.conversation_portal_link(conversation_id, token)
            parsed = urlsplit(link)
            self.assertEqual(parsed.query, '')
            self.assertEqual(parse_qs(parsed.fragment)['customer'], [token])
            self.assertEqual(parse_qs(parsed.fragment)['runId'], ['run-1'])
            with business.store.transaction():
                self.assertEqual(business.conversation_portal_link(conversation_id, token), link)
            with self.assertRaises(ValueError):
                with business.store.transaction():
                    business.conversation_portal_link(conversation_id, 'other-token-'+'o'*43)
            stored = business.store.one('SELECT * FROM host_requests WHERE opportunity_id=?',
                (business.conversation_view(conversation_id)['opportunity_id'],))
            self.assertNotIn(token, json.dumps(stored))
        finally:
            business.store.close()
        own = self.host.handle('/v1/customer', {'runId':'run-1','token':token})
        self.assertEqual(own['conversation']['id'], conversation_id)
        self.assertNotIn('customerToken', json.dumps(own))
        result = self.host.handle('/v1/message', {'runId':'run-1','token':token,
            'messageId':'portal-after-gmail', 'body':'Could you clarify what the quote includes?'})
        self.assertEqual(result['conversationId'], conversation_id)
        business = self.host.business('run-1')
        try:
            inbound = business.store.one("SELECT id FROM conversation_messages WHERE conversation_id=? AND source='customer-portal' AND direction='inbound'", (conversation_id,))
            self.assertIsNotNone(inbound)
            # Missing AI uses the local clarification/intervention path. A portal
            # message joining Gmail history must publish through the portal.
            Conversations(business).process(inbound['id'])
            self.assertTrue(business.store.one('SELECT id FROM host_notifications WHERE entity_id=?',
                (own['opportunityId'],)))
        finally:
            business.store.close()
        self.assertEqual(self.providers.creation_calls, 0)
        self.host.handle('/v1/revoke', {'runId':'run-1','token':token})
        with self.assertRaises(ValueError):
            self.host.handle('/v1/customer', {'runId':'run-1','token':token})

    def test_invalid_public_origin_cannot_mint_gmail_access(self):
        for origin in ['http://example.test', 'https://user:secret@example.test', 'https://example.test/path',
                       'https://example.test?token=bad', 'https://example.test#token=bad']:
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                Host({**self.env,'WIDEMIND_PUBLIC_ORIGIN':origin})


if __name__ == '__main__':
    unittest.main()
