"""Full real-adapter workflow using explicitly offline HTTP transport fixtures.

Actual Gmail/OpenAI/Stripe adapter, economy, runtime and business code execute.
No network, real customer, settled money, or live-provider evidence is created.
"""
import hashlib
import hmac
import json
from pathlib import Path
import tempfile
import unittest
from urllib.parse import parse_qs

from test_tools import FixtureTransport, inbox_message, public_dns, response
from test_economy_stripe import StripeTransportFixture, session_fixture
from widemind.business.engine import Business
from widemind.business.store import Store
from widemind.demo import drain
from widemind.economy import EconomyService, StripeClient
from widemind.runtime import RuntimeControl
from widemind.tools import GmailClient, GmailConfig, LiveTools, OpenAIResearch, ResearchConfig
from widemind.tools.gmail import rfc_message_id


class ProviderWorkflowTests(unittest.TestCase):
    def test_actual_adapters_request_to_verified_payment_delivery_and_refund(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            now = 1800000000
            class MailModelFixture(FixtureTransport):
                def request(self, method, url, **kwargs):
                    result = super().request(method, url, **kwargs)
                    if url.endswith('/messages/send'):
                        sent = sum('/messages/send' in item[1] for item in self.requests)
                        return response({'id': f'offline-sent-{sent}', 'threadId': f'offline-thread-{sent}'})
                    return result
            transport = MailModelFixture()
            stripe_transport = StripeTransportFixture()
            provider = {'session': None, 'paid': False, 'refunded': False}
            def checkout(method, url, headers, body):
                values = parse_qs(body.decode())
                metadata = {key: values[f'metadata[{key}]'][0] for key in ('run_id','job_id','action_id')}
                provider['session'] = session_fixture(metadata=metadata, status='open', payment_status='unpaid', payment_intent=None)
                return provider['session']
            def inspect(*args):
                if not provider['paid']:
                    return provider['session']
                session = session_fixture(metadata=provider['session']['metadata'])
                if provider['refunded']:
                    charge = session['payment_intent']['latest_charge']
                    charge['amount_refunded'] = 200
                    charge['refunds']['data'] = [{'id':'re_offline_fixture','object':'refund','livemode':False,
                        'currency':'usd','amount':200,'charge':'ch_fixture','status':'succeeded'}]
                return session
            stripe_transport.add('POST','/v1/checkout/sessions',checkout)
            stripe_transport.add('GET','/v1/checkout/sessions/cs_test_fixture',inspect)
            stripe_transport.add('GET','/v1/disputes',{'object':'list','data':[],'has_more':False})
            stripe_transport.add('GET','/v1/checkout/sessions',lambda *args:{'object':'list','data':[inspect()],'has_more':False})
            stripe = StripeClient(api_key='sk_test_OFFLINE_FIXTURE', webhook_secret='whsec_OFFLINE_FIXTURE', mode='test',
                success_url='https://example.invalid/success', cancel_url='https://example.invalid/cancel',
                transport=stripe_transport, clock=lambda:now)
            economy = EconomyService(root/'protected-economy.sqlite3',mode='test',stripe=stripe,clock=lambda:now)
            runtime = RuntimeControl(root/'protected-runtime.sqlite3',mode='test')
            runtime.initialize('provider-fixture-run',initial_state='active',evidence_reference='explicit offline fixture run')
            economy.create_run('provider-fixture-run',evidence_reference='explicit offline fixture funding')
            gmail = GmailClient(GmailConfig('widemind@example.invalid','OFFLINE_CLIENT','OFFLINE_SECRET','OFFLINE_REFRESH'),transport=transport)
            research = OpenAIResearch(ResearchConfig('OFFLINE_OPENAI_KEY',input_micro_usd_per_million_tokens=400000,
                output_micro_usd_per_million_tokens=1600000,cached_input_micro_usd_per_million_tokens=100000,
                source_urls=('https://example.com/a','https://example.com/b')),transport=transport,resolver=public_dns)
            tools = LiveTools(root/'protected-tools.sqlite3',economy=economy,runtime=runtime,gmail=gmail,research=research,
                              mode='test',allowed_recipients=('customer@example.invalid',))
            business = Business(Store(root/'business.sqlite3'),run_id='provider-fixture-run',mode='test',
                                economy=economy,runtime=runtime,tools=tools,clock=lambda:now)
            try:
                transport.inbox = [inbox_message(command={'type':'request','service':'research-brief','scope':
                    {'topic':'OFFLINE provider integration test','sections':['Overview','Risks'],'min_sources':2}})]
                business.notify(source='fixture-gmail',external_id='request-sync',kind='gmail.sync',payload={})
                drain(business)
                quote = business.store.one('SELECT * FROM quotes')
                self.assertIsNotNone(quote, business.diagnostics())
                self.assertEqual(quote['status'],'offered')  # Gmail transport acceptance allows notices.
                offer = business.store.one("SELECT * FROM effects WHERE action='gmail.send'")
                transport.inbox = [inbox_message('offline-accept',command={'type':'accept','quote_id':quote['id']},reply_to=rfc_message_id(offer['id']))]
                business.notify(source='fixture-gmail',external_id='accept-sync',kind='gmail.sync',payload={})
                drain(business)
                order = business.store.one('SELECT * FROM orders')
                self.assertEqual(order['status'],'awaiting_payment',business.diagnostics())
                job = order['id']
                self.assertEqual(economy.balance(run_id=business.run_id)['receipts_micro_usd'],0)
                provider['paid'] = True
                event = {'id':'evt_offline_paid','type':'checkout.session.completed','livemode':False,'data':{'object':inspect()}}
                raw = json.dumps(event,separators=(',',':')).encode()
                digest = hmac.new(b'whsec_OFFLINE_FIXTURE',str(now).encode()+b'.'+raw,hashlib.sha256).hexdigest()
                jobs = economy.handle_webhook(raw,f't={now},v1={digest}')
                self.assertEqual(jobs,[job])
                business.notify(source='fixture-stripe',external_id=event['id'],kind='payment.changed',payload={'id':job})
                drain(business)
                current = business.order(job)
                self.assertEqual(current['status'],'ready',business.diagnostics())
                self.assertEqual(json.loads(current['payment'])['earned_micro_usd'],0)
                self.assertIn('OFFLINE provider integration test',json.loads(current['artifact'])['content'])
                self.assertEqual(economy.balance(run_id=business.run_id)['operating_cost_micro_usd'],104)
                delivery = business.store.one("SELECT * FROM effects WHERE action='gmail.send' AND request LIKE '%attachment%'")
                transport.inbox = [inbox_message('offline-received',command={'type':'acknowledge','action_id':delivery['id']},reply_to=rfc_message_id(delivery['id']))]
                business.notify(source='fixture-gmail',external_id='receipt-sync',kind='gmail.sync',payload={})
                drain(business)
                completed = business.order(job)
                self.assertEqual(completed['status'],'completed',business.diagnostics())
                balance = economy.balance(run_id=business.run_id)
                self.assertEqual(balance['earned_micro_usd'],2000000)
                self.assertEqual(balance['holds_micro_usd'],0)
                self.assertEqual(balance['fees_micro_usd'],360000)
                self.assertEqual(balance['spendable_micro_usd'],6639896)
                self.assertEqual(sum(url == OpenAIResearch.API for _,url,_ in transport.requests),1)
                # Duplicate signed event and business hint do not pay or execute twice.
                self.assertEqual(economy.handle_webhook(raw,f't={now},v1={digest}'),[job])
                business.notify(source='fixture-stripe',external_id=event['id'],kind='payment.changed',payload={'id':job})
                drain(business)
                self.assertEqual(economy.balance(run_id=business.run_id),balance)
                self.assertEqual(sum(url == OpenAIResearch.API for _,url,_ in transport.requests),1)
                provider['refunded'] = True
                refund_event = {'id':'evt_offline_refund','type':'charge.refunded','livemode':False,
                                'data':{'object':inspect()['payment_intent']['latest_charge']}}
                refund_raw = json.dumps(refund_event,separators=(',',':')).encode()
                refund_digest = hmac.new(b'whsec_OFFLINE_FIXTURE',str(now).encode()+b'.'+refund_raw,hashlib.sha256).hexdigest()
                self.assertEqual(economy.handle_webhook(refund_raw,f't={now},v1={refund_digest}'),[job])
                business.notify(source='fixture-stripe',external_id='evt_offline_refund',kind='payment.changed',payload={'id':job})
                drain(business)
                refunded = economy.balance(run_id=business.run_id)
                self.assertEqual(refunded['earned_micro_usd'],0)
                self.assertEqual(refunded['refunds_micro_usd'],2000000)
                self.assertEqual(refunded['spendable_micro_usd'],4639896)
                self.assertEqual(business.order(job)['status'],'completed')
                self.assertEqual(json.loads(business.order(job)['payment'])['earned_micro_usd'],0)
            finally:
                business.close()


if __name__ == '__main__':
    unittest.main()
