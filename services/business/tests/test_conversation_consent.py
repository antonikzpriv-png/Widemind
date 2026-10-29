"""A private request token cannot revoke consent for another request's email claim."""
import sqlite3
import unittest
from unittest.mock import patch

import test_conversation_portal as portal_fixtures
from widemind.business.contracts import Outcome
from widemind.business.host_server import Host


class ConversationConsentTests(unittest.TestCase):
    def setUp(self):
        self.f = portal_fixtures.ConversationPortalTests(
            'test_natural_request_and_messages_are_request_scoped_and_durable')
        self.f.setUp()
        self.addCleanup(self.f.tearDown)

    def accept(self, created, proposal, token):
        return self.f.host.handle('/v1/accept', {
            'runId':'run-1', 'token':token, 'conversationId':created['conversationId'],
            'quoteId':proposal['quoteId'], 'version':proposal['version'], 'digest':proposal['digest']})

    def test_new_token_with_existing_email_cannot_opt_out_the_original_request(self):
        original = self.f.request('original-customer-request', 'x'*43)
        proposal = self.f.proposal(original['conversationId'])
        claimed = self.f.request('unverified-same-email-claim', 'y'*43,
                                 'A different private request owned by this new token.')
        self.assertNotEqual(original['conversationId'], claimed['conversationId'])
        with self.assertRaises(ValueError):
            self.accept(original, proposal, 'y'*43)
        self.f.host.handle('/v1/opt-out', {'runId':'run-1', 'token':'y'*43})
        self.assertEqual(self.f.customer('y'*43)['conversation']['status'], 'opted_out')
        self.assertEqual(self.f.customer('x'*43)['conversation']['status'], 'quoted')
        self.assertNotIn('different private request', str(self.f.customer('x'*43)))
        business = self.f.host.business('run-1')
        try:
            customer = business.store.one('SELECT customer_id FROM customer_threads WHERE id=?',
                                          (original['conversationId'],))['customer_id']
            self.assertFalse(business._opted_out(customer), 'unverified email text confers no global consent authority')
            original_contact = business.store.one('SELECT contact_allowed FROM customer_threads WHERE id=?',
                                                 (original['conversationId'],))['contact_allowed']
            claimed_contact = business.store.one('SELECT contact_allowed FROM customer_threads WHERE id=?',
                                                (claimed['conversationId'],))['contact_allowed']
            self.assertEqual((original_contact, claimed_contact), (1, 0))
        finally:
            business.store.close()
        accepted = self.accept(original, proposal, 'x'*43)
        self.assertTrue(accepted['orderId'])
        self.assertEqual(self.accept(original, proposal, 'x'*43), accepted)
        self.assertEqual(self.f.providers.creation_calls, 0)
        self.assertEqual(self.f.providers.payments, [])

    def test_stop_message_only_suppresses_the_token_owned_request_while_paused(self):
        original = self.f.request('original-customer-request', 'x'*43)
        self.f.proposal(original['conversationId'])
        own = self.f.request('separate-token-request', 'y'*43)
        with sqlite3.connect(self.f.runtime) as db:
            db.execute("UPDATE runtime_runs SET state='paused',desired='pause'")
        body = {'runId':'run-1','token':'y'*43,'messageId':'immediate-stop','body':'Please stop emailing me.'}
        first = self.f.host.handle('/v1/message', body)
        self.assertEqual(self.f.host.handle('/v1/message', body), first)
        self.assertEqual(self.f.customer('y'*43)['conversation']['status'], 'opted_out')
        self.assertEqual(self.f.customer('x'*43)['conversation']['status'], 'quoted')
        business = self.f.host.business('run-1')
        try:
            self.assertEqual(business.store.one('SELECT opted_out FROM customers')['opted_out'], 0)
            self.assertEqual(business.store.one('SELECT contact_allowed FROM customer_threads WHERE id=?',
                                             (own['conversationId'],))['contact_allowed'], 0)
            self.assertEqual(business.store.all('SELECT * FROM conversation_allocations'), [])
            self.assertEqual(business.store.all('SELECT * FROM effects'), [])
        finally:
            business.store.close()

    def test_verified_gmail_opt_out_retains_global_sender_suppression(self):
        first = self.f.request('customer-request-one', 'x'*43)
        second = self.f.request('customer-request-two', 'y'*43)
        business = self.f.host.business('run-1')
        try:
            business._gmail_message({
                'message_id':'verified-sender-stop', 'thread_id':'verified-sender-thread',
                'from':'controlled-customer@example.test', 'sender_verified':True,
                'body':'Please stop emailing me.', 'rfc_message_id':'<stop@example.test>'})
            self.assertEqual(business.store.one('SELECT opted_out FROM customers')['opted_out'], 1)
            self.assertEqual(business.store.all('SELECT * FROM conversation_allocations'), [])
            self.assertEqual(business.store.all('SELECT * FROM effects'), [])
        finally:
            business.store.close()
        self.assertEqual(self.f.customer('x'*43)['conversation']['status'], 'opted_out')
        self.assertEqual(self.f.customer('y'*43)['conversation']['status'], 'opted_out')
        self.assertNotEqual(first['conversationId'], second['conversationId'])

    def test_local_opt_out_survives_restart_recovery_followup_and_operator_resolution(self):
        own = self.f.request()
        self.f.proposal(own['conversationId'])
        business = self.f.host.business('run-1')
        try:
            message = business.store.one("SELECT id FROM conversation_messages WHERE conversation_id=? AND direction='inbound'",
                                         (own['conversationId'],))
            with business.store.transaction():
                business._conversation_manager()._prepare(
                    business._conversation_manager().thread(own['conversationId']), message['id'],
                    'This controlled response was prepared before contact was withdrawn.')
            self.assertEqual(business.store.all('SELECT * FROM host_notifications'), [])
        finally:
            business.store.close()
        with sqlite3.connect(self.f.runtime) as db:
            db.execute("UPDATE runtime_runs SET state='paused',desired='pause'")
        self.f.host.handle('/v1/opt-out', {'runId':'run-1', 'token':'x'*43})
        self.f.host = Host(self.f.env)
        self.assertEqual(self.f.customer()['conversation']['status'], 'opted_out')
        with sqlite3.connect(self.f.runtime) as db:
            db.execute("UPDATE runtime_runs SET state='active',desired='run'")
        business = self.f.host.business('run-1')
        try:
            manager = business._conversation_manager()
            manager.process(message['id'])
            manager.followup(own['conversationId'])
            manager.recovered({'job_id':own['conversationId'], 'id':'controlled-recovery'})
            business.conversation_resolve(conversation_id=own['conversationId'], evidence_reference='operator-reviewed-support-case')
            for _ in range(20):
                if not business.tick(): break
            self.assertEqual(business.store.all('SELECT * FROM host_notifications'), [])
            self.assertEqual(business.store.all('SELECT * FROM effects'), [])
            self.assertEqual(business.store.one('SELECT contact_allowed FROM customer_threads WHERE id=?',
                                             (own['conversationId'],))['contact_allowed'], 0)
            self.assertEqual(business.conversation_view(own['conversationId'])['status'], 'opted_out')
        finally:
            business.store.close()

    def test_local_consent_is_enforced_before_mail_dispatch_without_blocking_other_request(self):
        own = self.f.request('own-order', 'x'*43)
        proposal = self.f.proposal(own['conversationId'])
        order = self.accept(own, proposal, 'x'*43)['orderId']
        other = self.f.request('other-request', 'y'*43)
        self.f.host.handle('/v1/opt-out', {'runId':'run-1', 'token':'x'*43})
        business = self.f.host.business('run-1')
        try:
            with patch.object(business.tools, 'execute', return_value=Outcome('succeeded', {
                    'message_id':'controlled-send','delivery_state':'accepted'})) as execute:
                for entity in (own['conversationId'], own['opportunityId'], order):
                    with self.subTest(entity=entity):
                        outcome = business._invoke_tool(run_id='run-1', job_id=entity, action_id='controlled-'+entity,
                            action='gmail.send', arguments={'to':'controlled-customer@example.test'},
                            reservation_id='controlled-zero-reservation', maximum_micro_usd=0)
                        self.assertEqual(outcome.state, 'failed')
                execute.assert_not_called()
                allowed = business._invoke_tool(run_id='run-1', job_id=other['conversationId'], action_id='controlled-other',
                    action='gmail.send', arguments={'to':'controlled-customer@example.test'},
                    reservation_id='controlled-other-zero', maximum_micro_usd=0)
                self.assertEqual(allowed.state, 'succeeded')
                execute.assert_called_once()
        finally:
            business.store.close()


if __name__ == '__main__':
    unittest.main()
