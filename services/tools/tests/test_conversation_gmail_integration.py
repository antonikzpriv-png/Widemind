"""Actual Gmail MIME/OAuth adapter through the integrated gateway; synthetic HTTP only."""
import base64
import json
from email import policy
from email.parser import BytesParser
from pathlib import Path
import tempfile
import unittest

import test_gmail as gmail_fixtures
import test_integrated_service as integrated_fixtures
from widemind.integrated_service import IntegratedTools


class GmailConversationIntegrationTests(unittest.TestCase):
    def test_authorized_gmail_batch_and_threaded_send_replay_through_real_adapter(self):
        with tempfile.TemporaryDirectory() as root:
            root=Path(root)
            config={'control_dir':str(root/'control'),'workspace_dir':str(root/'workspace'),
                    'policy':{'profile':'sandbox','allow_external_effects':True,'allowed_email_recipients':['controlled@example.test']},
                    'tools':{'gmail':{'enabled':True,'client_id':'fixture-client','redirect_uri':'http://127.0.0.1:8123/callback',
                                     'expected_account':'dedicated@example.test','expected_subject':'fixture-subject',
                                     'client_secret_ref':'gmail.client','token_ref':'gmail.token','oauth_state_ref':'gmail.state',
                                     'conversation_trusted_authserv_id':'mx.google.com'}}}
            economy=integrated_fixtures.Economy()
            tools=IntegratedTools(config,economy,secrets=gmail_fixtures.Secrets())
            transport=gmail_fixtures.GmailFixture(tools.journal,'read-action')
            transport.read_payload['payload']['headers']=[
                {'name':'From','value':'controlled@example.test'}, {'name':'Subject','value':'An owl design'},
                {'name':'Message-ID','value':'<customer-fixture@example.test>'},
                {'name':'Authentication-Results','value':'mx.google.com; dmarc=pass header.from=example.test'}]
            def page(method,parsed,_kwargs):
                if method=='GET' and parsed.path.endswith('/messages'):
                    return 200,{'messages':[{'id':'read1','threadId':'thread1'}]}
                if method=='POST' and parsed.path.endswith('/messages/send'):
                    saved=tools.journal.load('reply-action')
                    self.assertTrue(saved['gmail']['attempted'])
                    self.assertEqual(saved['external_intent']['mime_sha256'],saved['gmail']['mime_sha256'])
                    body=json.loads(_kwargs['body'])
                    transport.sent['sent1']={'id':'sent1','threadId':body['threadId'],'raw':body['raw']}
                    return 200,{'id':'sent1','threadId':body['threadId']}
                return None
            transport.override=page
            tools.transport=transport
            action=integrated_fixtures.action('gmail.read',{'cursor':None},id='read-action')
            action['limit']['maxOutputBytes']=65536
            reservation=economy.add(action)
            economy.rows['reservation-1']['limits']['max_storage_bytes']=65536
            result=tools.execute({'action':action,'reservation':reservation})
            self.assertEqual(result['kind'],'receipt',result)
            batch=result['receipt']['result']['data']
            self.assertEqual(len(batch['messages']),1)
            incoming=batch['messages'][0]
            self.assertTrue(incoming['sender_verified'])
            self.assertEqual(incoming['body'],'hello customer')
            self.assertEqual(incoming['reply']['thread_id'],'thread1')
            count=len(transport.calls)
            self.assertEqual(tools.execute({'action':action,'reservation':reservation}),result)
            self.assertEqual(len(transport.calls),count)
            economy.settle('reservation-1',result['receipt'])
            reply=integrated_fixtures.action('gmail.send',{'to':incoming['from'],'body':'What dimensions would you like?',**incoming['reply']},id='reply-action')
            reply_reservation=economy.add(reply,'reply-reservation')
            sent=tools.execute({'action':reply,'reservation':reply_reservation})
            self.assertEqual(sent['kind'],'receipt',sent)
            data=sent['receipt']['result']['data']
            self.assertTrue(data['provider_accepted'])
            self.assertFalse(data['delivery_confirmed'])
            self.assertEqual(len(transport.sent),1)
            mime=BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(transport.sent['sent1']['raw']))
            self.assertEqual(mime['In-Reply-To'],'<customer-fixture@example.test>')
            self.assertEqual(mime['References'],'<customer-fixture@example.test>')
            self.assertEqual(mime['Subject'],'Re: An owl design')
            self.assertEqual(tools.execute({'action':reply,'reservation':reply_reservation}),sent)
            self.assertEqual(len(transport.sent),1)
            public=json.dumps(batch)
            self.assertNotIn('fixture-access-never-log',public)
            self.assertNotIn('fixture-secret-never-log',public)


if __name__=='__main__':unittest.main()
