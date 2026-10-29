"""Real business/portal persistence and HTTP boundaries; providers are explicit fixtures."""
import json
import sqlite3
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from widemind.business.host_server import Host

class FixtureServices:
    def __init__(self):
        self.payments=[];self.reservations=[];self.earned=0;self.creation_calls=0;self.settled=False
        parent=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*_args):pass
            def do_GET(self):self.reply(parent.handle(self.path,None))
            def do_POST(self):self.reply(parent.handle(self.path,json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
            def reply(self,data):
                encoded=json.dumps(data).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(encoded)));self.end_headers();self.wfile.write(encoded)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.url=f'http://127.0.0.1:{self.server.server_port}'
    def close(self):self.server.shutdown();self.server.server_close();self.thread.join()
    def handle(self,path,body):
        if path=='/capabilities':return {'capabilities':[{'id':'graphics.create','availability':{'available':True}}]}
        if path=='/execute':
            assert body['reservation_id']==self.reservations[0]['reservation_id'];assert body['action_id']==self.reservations[0]['action_id'];self.creation_calls+=1
            self.reservations[0]['state']='settled'
            return {'state':'succeeded','data':{'filename':'graphic.svg','media_type':'image/svg+xml','content':'<svg xmlns="http://www.w3.org/2000/svg"><text>Fixture requested graphic</text></svg>','checks':{'valid_svg':True}}}
        if path.endswith('/balance'):return {'run_id':'run-1','mode':'test'}
        if path.endswith('/payments'):return self.payments
        if path.endswith('/checkout'):
            row={'payment_id':'fixture-paypal-order','job_id':body['job_id'],'request_id':body['request_id'],'url':'https://www.sandbox.paypal.com/checkoutnow?token=fixture-order','status':'pending'};self.payments=[row];return row
        if '/snapshot' in path:
            return {'run_id':'run-1','job_id':self.payments[0]['job_id'],'revision':2 if self.settled else 1,'mode':'test','currency':'USD','status':'settled' if self.settled else 'pending','receipts_micro_usd':2000000 if self.settled else 0,'refunds_micro_usd':0,'fees_micro_usd':0,'earned_micro_usd':self.earned,'operating_cost_micro_usd':0,'costs_final':bool(self.reservations and self.reservations[0]['state']=='settled')}
        if path.endswith('/reservations') and body is not None:
            if not self.reservations:self.reservations=[{**body,'reservation_id':'reservation-1','state':'reserved'}]
            return self.reservations[0]
        if path.endswith('/reservations'):return self.reservations
        if path.endswith('/earned-revenue'):self.earned=body['amount_micro_usd'];return {'recorded':True}
        if path.endswith('/release-hold'):return {'released':True}
        if path.endswith('/ledger'):return []
        if path.endswith('/charges'):return []
        raise AssertionError(path)

class HostTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.fixture=FixtureServices();self.runtime=self.root/'runtime.sqlite'
        with sqlite3.connect(self.runtime) as db:db.execute('CREATE TABLE runtime_runs(id TEXT PRIMARY KEY,state TEXT,desired TEXT,profile TEXT)');db.execute("INSERT INTO runtime_runs VALUES ('run-1','active','run','foundation')")
        self.host=Host({'WIDEMIND_BUSINESS_DB':str(self.root/'business.sqlite'),'WIDEMIND_BUSINESS_TOKEN':'b'*48,'WIDEMIND_BUSINESS_MODE':'test','WIDEMIND_RUNTIME_DATABASE':str(self.runtime),'WIDEMIND_TOOLS_URL':self.fixture.url,'WIDEMIND_TOOLS_TOKEN':'t'*48,'WIDEMIND_ECONOMY_URL':self.fixture.url,'WIDEMIND_ECONOMY_OPERATOR_TOKEN':'e'*48})
    def tearDown(self):self.fixture.close();self.temp.cleanup()
    def step(self,index):
        work=self.host.handle('/v1/work-state',{'runId':'run-1'})
        if work['eventId'] and work['dueAt']<=time.time():return self.host.handle('/v1/step',{'runId':'run-1','actionId':f'outer-{index}','eventId':work['eventId']})
        return None
    def request(self,external='request-1',token='x'*43):return self.host.handle('/v1/request',{'runId':'run-1','requestId':external,'customerToken':token,'email':'real-input@example.test','service':'svg-graphic','scope':{'title':'Requested graphic','description':'A useful SVG'}})
    def test_complete_portal_order_uses_verified_payment_and_one_authorized_creation(self):
        created=self.request();token=created['customerToken'];self.step(1);self.step(2)
        customer=self.host.handle('/v1/customer',{'runId':'run-1','token':token});self.assertEqual(customer['quotes'][0]['status'],'offered')
        order=self.host.handle('/v1/accept',{'runId':'run-1','token':token,'quoteId':customer['quotes'][0]['quoteId']})['orderId'];self.step(3);self.step(4)
        self.assertEqual(self.fixture.creation_calls,0)
        self.fixture.settled=True;self.host.handle('/v1/notify',{'runId':'run-1','event':{'source':'fixture-verified-payment','external_id':'paid-1','kind':'payment.changed','payload':{'id':order}}})
        for i in range(5,15):self.step(i)
        customer=self.host.handle('/v1/customer',{'runId':'run-1','token':token});self.assertEqual(customer['orders'][0]['status'],'completed');self.assertEqual(self.fixture.creation_calls,1);self.assertEqual(self.fixture.earned,2000000)
        delivery=self.host.handle('/v1/delivery',{'runId':'run-1','token':token,'orderId':order});self.assertIn('<svg',delivery['content'])
        self.assertEqual(self.host.handle('/v1/request',{'runId':'run-1','requestId':'request-1','customerToken':token,'email':'real-input@example.test','service':'svg-graphic','scope':{'title':'Requested graphic','description':'A useful SVG'}})['opportunityId'],created['opportunityId'])
    def test_private_access_is_scoped_and_revocable(self):
        self.request();self.request('request-2','y'*43)
        with self.assertRaises(ValueError):self.host.handle('/v1/customer',{'runId':'run-1','token':'z'*43})
        self.host.handle('/v1/revoke',{'runId':'run-1','token':'x'*43})
        with self.assertRaises(ValueError):self.host.handle('/v1/customer',{'runId':'run-1','token':'x'*43})
        self.assertEqual(self.host.handle('/v1/customer',{'runId':'run-1','token':'y'*43})['opportunityId'],self.request('request-2','y'*43)['opportunityId'])
    def test_a_customer_token_cannot_be_rebound_to_another_request(self):
        self.request()
        other=self.request('another-request','x'*43)
        self.assertEqual(other['statusCode'],409)
        self.assertEqual(self.host.handle('/v1/customer',{'runId':'run-1','token':'x'*43})['opportunityId'],self.request()['opportunityId'])
    def test_outer_step_is_idempotent_and_unknown_is_never_blindly_repeated(self):
        self.request();work=self.host.handle('/v1/work-state',{'runId':'run-1'});request={'runId':'run-1','actionId':'outer-durable','eventId':work['eventId']};first=self.host.handle('/v1/step',request);self.assertEqual(self.host.handle('/v1/step',request),first);self.assertEqual(self.host.handle('/v1/reconcile',{'runId':'run-1','actionId':'not-started'})['kind'],'unknown')
    def test_interrupted_bridge_receipt_recovers_from_exact_completed_core_event(self):
        self.request();work=self.host.handle('/v1/work-state',{'runId':'run-1'});request={'runId':'run-1','actionId':'outer-crash-window','eventId':work['eventId']};self.host.handle('/v1/step',request)
        business=self.host.business('run-1')
        try:business.store.db.execute("UPDATE host_invocations SET state='running',result=NULL WHERE action_id=?",('outer-crash-window',))
        finally:business.store.close()
        result=self.host.handle('/v1/reconcile',{'runId':'run-1','actionId':'outer-crash-window'});self.assertEqual(result['kind'],'completed');self.assertEqual(result['eventState'],'done');self.assertEqual(self.fixture.creation_calls,0)
    def test_stopped_runtime_preserves_customer_read_access_but_does_no_work(self):
        self.request()
        with sqlite3.connect(self.runtime) as db:db.execute("UPDATE runtime_runs SET desired='stop',state='stopped'")
        self.assertEqual(self.host.handle('/v1/customer',{'runId':'run-1','token':'x'*43})['status'],'received');self.assertFalse(self.step(1)['processed'])

if __name__=='__main__':unittest.main()
