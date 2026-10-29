"""Real business/portal persistence and HTTP boundaries; providers are explicit fixtures."""
import json
import sqlite3
import tempfile
import threading
import time
import unittest
import urllib.error
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from widemind.business.host_server import Host

class FixtureServices:
    def __init__(self):
        self.payments=[];self.reservations=[];self.earned=0;self.metric_reference=None;self.metric_job=None;self.creation_calls=0;self.settled=False;self.source_revision=1;self.unknown_creation=False;self.reconciled=False;self.hold_release_pending=False
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
            if self.unknown_creation:return {'state':'unknown','reason':'fixture response lost after accepted creation'}
            return self.created()
        if path=='/reconcile':return self.created() if self.reconciled else {'state':'unknown','reason':'fixture outcome not yet verified'}
        if path.endswith('/balance'):return {'run_id':'run-1','mode':'test'}
        if path.endswith('/payments'):return self.payments
        if path.endswith('/checkout'):
            row={'payment_id':'fixture-paypal-order','job_id':body['job_id'],'request_id':body['request_id'],'url':'https://www.sandbox.paypal.com/checkoutnow?token=fixture-order','status':'pending'};self.payments=[row];return row
        if '/snapshot' in path:
            return {'run_id':'run-1','job_id':self.payments[0]['job_id'],'revision':self.source_revision+(1 if self.settled else 0),'mode':'test','currency':'USD','status':'settled' if self.settled else 'pending','receipts_micro_usd':2000000 if self.settled else 0,'refunds_micro_usd':0,'fees_micro_usd':0,'earned_micro_usd':self.earned,'operating_cost_micro_usd':0,'costs_final':bool(self.reservations and self.reservations[0]['state']=='settled')}
        if path.endswith('/reservations') and body is not None:
            if not self.reservations:self.reservations=[{**body,'reservation_id':'reservation-1','state':'reserved'}]
            return self.reservations[0]
        if path.endswith('/reservations'):return self.reservations
        if path.endswith('/earned-revenue'):self.earned=body['amount_micro_usd'];self.metric_reference=body['reference'];self.metric_job=body['job_id'];return {'recorded':True}
        if path.endswith('/release-hold'):
            if self.hold_release_pending:return {'released_micro_usd':0,'pending_liability_micro_usd':2000000}
            self.payments[0]['released_micro_usd']=2000000;return {'released_micro_usd':2000000,'pending_liability_micro_usd':0}
        if path.endswith('/ledger'):return [{'kind':'earned_revenue','category':'earned_revenue','reference':self.metric_reference,'metadata':{'job_id':self.metric_job,'reported_micro_usd':self.earned}}] if self.metric_reference else []
        if path.endswith('/charges'):return []
        raise AssertionError(path)

    @staticmethod
    def created():return {'state':'succeeded','data':{'filename':'graphic.svg','media_type':'image/svg+xml','content':'<svg xmlns="http://www.w3.org/2000/svg"><text>Fixture requested graphic</text></svg>','checks':{'valid_svg':True}}}

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
        self.fixture.settled=True # work-state observes authoritative local snapshot without a notification
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
        self.request();work=self.host.handle('/v1/work-state',{'runId':'run-1'});request={'runId':'run-1','actionId':'outer-durable','eventId':work['eventId']};first=self.host.handle('/v1/step',request);self.assertEqual(self.host.handle('/v1/step',request),first);self.assertEqual(self.host.handle('/v1/reconcile',{'runId':'run-1','actionId':'not-started'})['kind'],'not_performed')
    def test_interrupted_bridge_receipt_recovers_from_exact_completed_core_event(self):
        self.request();work=self.host.handle('/v1/work-state',{'runId':'run-1'});request={'runId':'run-1','actionId':'outer-crash-window','eventId':work['eventId']};self.host.handle('/v1/step',request)
        business=self.host.business('run-1')
        try:business.store.db.execute("UPDATE host_invocations SET state='running',result=NULL WHERE action_id=?",('outer-crash-window',))
        finally:business.store.close()
        result=self.host.handle('/v1/reconcile',{'runId':'run-1','actionId':'outer-crash-window'});self.assertEqual(result['kind'],'completed');self.assertEqual(result['eventState'],'done');self.assertEqual(self.fixture.creation_calls,0)
    def test_restart_before_core_dispatch_observes_durable_queue_without_running_it(self):
        self.request();work=self.host.handle('/v1/work-state',{'runId':'run-1'})
        business=self.host.business('run-1')
        try:
            business.store.db.execute("INSERT INTO host_invocations VALUES (?,?,?,'running',NULL,?)",('outer-before-dispatch','run-1',work['eventId'],time.time()))
        finally:business.store.close()
        result=self.host.handle('/v1/reconcile',{'runId':'run-1','actionId':'outer-before-dispatch'})
        self.assertEqual(result['kind'],'completed');self.assertFalse(result['processed']);self.assertEqual(result['eventState'],'queued')
        self.assertEqual(self.fixture.creation_calls,0)
        self.assertEqual(self.host.handle('/v1/work-state',{'runId':'run-1'})['eventId'],work['eventId'])
        self.assertTrue(self.step('recovered')['processed'])

    def test_restart_during_core_processing_retains_unknown_effect_and_event_lease(self):
        self.request();work=self.host.handle('/v1/work-state',{'runId':'run-1'})
        business=self.host.business('run-1')
        try:
            business.store.db.execute("UPDATE events SET state='running',lease_until=?,attempts=1 WHERE id=?",(time.time()+120,work['eventId']))
            business.store.db.execute("INSERT INTO host_invocations VALUES (?,?,?,'running',NULL,?)",('outer-active','run-1',work['eventId'],time.time()))
            business.store.db.execute("INSERT INTO effects(id,run_id,job_id,provider,action,request,state,created_at,logical_id,attempt) VALUES (?,?,?,?,?,?,'unknown',?,?,0)",('pending-effect','run-1','job-uncertain','tools','graphics.create','{}',time.time(),'pending-effect'))
        finally:business.store.close()
        result=self.host.handle('/v1/reconcile',{'runId':'run-1','actionId':'outer-active'})
        self.assertEqual(result['kind'],'completed');self.assertFalse(result['processed']);self.assertEqual(result['eventState'],'running')
        self.assertGreater(result['dueAt'],time.time())
        business=self.host.business('run-1')
        try:self.assertEqual(business.store.one("SELECT state FROM effects WHERE id='pending-effect'")['state'],'unknown')
        finally:business.store.close()
        self.assertEqual(self.fixture.creation_calls,0)

    def test_never_dispatched_outer_action_has_definitive_journal_evidence(self):
        result=self.host.handle('/v1/reconcile',{'runId':'run-1','actionId':'not-started'})
        self.assertEqual(result['kind'],'not_performed');self.assertIn('not-started',result['evidenceRef'])

    def test_public_headquarters_has_only_available_offerings_and_curated_activity(self):
        self.test_complete_portal_order_uses_verified_payment_and_one_authorized_creation()
        result=self.host.handle('/v1/public',{'runId':'run-1'})
        self.assertTrue(result['availability']['available'])
        self.assertEqual([o['id'] for o in result['offerings']],['svg-graphic'])
        self.assertEqual(len(result['activity']),1)
        text=json.dumps(result)
        self.assertNotIn('real-input',text);self.assertNotIn('Requested graphic',text);self.assertNotIn('customerToken',text)
        self.assertIn('completed',result['activity'][0]['text'].lower())

    def test_selection_does_not_hold_business_write_lock_while_checking_tool_service(self):
        self.request();business=self.host.business('run-1');observed=[]
        original=business.tools.available
        def checked(action):
            observed.append(business.store.db.in_transaction)
            return original(action)
        business.tools.available=checked
        try:
            self.assertTrue(business.tick())
            self.assertTrue(observed)
            self.assertFalse(any(observed))
        finally:business.store.close()

    def test_capabilities_are_read_once_per_request_not_once_per_opportunity(self):
        self.request();self.request('another','y'*43)
        business=self.host.business('run-1');reads=[];original=business.tools.http.request
        def checked(path,*args,**kwargs):
            reads.append(path);return original(path,*args,**kwargs)
        business.tools.http.request=checked
        try:
            self.assertTrue(business.tick())
            self.assertEqual(reads.count('/capabilities'),1)
        finally:business.store.close()

    def test_pause_during_portal_publication_keeps_event_queued_for_resume(self):
        self.request();self.step('select');business=self.host.business('run-1');original=business._email
        def paused(*args,**kwargs):
            with sqlite3.connect(self.runtime) as db:db.execute("UPDATE runtime_runs SET state='paused',desired='pause'")
            return original(*args,**kwargs)
        business._email=paused
        try:
            self.assertTrue(business.tick())
            self.assertEqual(business.store.one("SELECT state FROM events WHERE kind='offer'")['state'],'queued')
        finally:business.store.close()
        with sqlite3.connect(self.runtime) as db:db.execute("UPDATE runtime_runs SET state='active',desired='run'")
        self.step('resumed')
        customer=self.host.handle('/v1/customer',{'runId':'run-1','token':'x'*43})
        self.assertEqual(customer['quotes'][0]['status'],'offered')

    def test_policy_changes_during_capability_read_cannot_commit_a_stale_quote(self):
        self.request();business=self.host.business('run-1');original=business.tools.available;changed=[]
        def checked(action):
            if not changed:
                changed.append(True)
                other=self.host.business('run-1')
                try:
                    policy=other.policy.document();policy['capabilities']=[]
                    from widemind.business.policy import Policy
                    other.configure(Policy(**policy))
                finally:other.store.close()
            return original(action)
        business.tools.available=checked
        try:
            self.assertTrue(business.tick())
            self.assertEqual(business.store.all('SELECT id FROM quotes'),[])
        finally:business.store.close()
        self.step('new-policy')
        self.assertEqual(self.host.handle('/v1/customer',{'runId':'run-1','token':'x'*43})['quotes'],[])

    def test_unavailable_capability_service_is_not_retried_for_each_candidate(self):
        self.request();self.request('another','y'*43);business=self.host.business('run-1');calls=[]
        def unavailable(*args,**kwargs):calls.append(args);raise OSError('controlled fixture outage')
        business.tools.http.request=unavailable
        try:
            self.assertTrue(business.tick());self.assertEqual(len(calls),1)
            self.assertEqual(business.store.all('SELECT id FROM quotes'),[])
        finally:business.store.close()

    def test_stopped_runtime_preserves_customer_read_access_but_does_no_work(self):
        self.request()
        with sqlite3.connect(self.runtime) as db:db.execute("UPDATE runtime_runs SET desired='stop',state='stopped'")
        self.assertEqual(self.host.handle('/v1/customer',{'runId':'run-1','token':'x'*43})['status'],'received');self.assertFalse(self.step(1)['processed'])

    def test_higher_unrelated_source_revisions_do_not_create_busy_payment_wakeups(self):
        self.test_complete_portal_order_uses_verified_payment_and_one_authorized_creation()
        for i in range(10):
            self.fixture.source_revision+=1
            work=self.host.handle('/v1/work-state',{'runId':'run-1'})
            self.assertTrue(work['eventId'] is None or work['dueAt']>time.time())
        self.assertEqual(self.fixture.creation_calls,1)
    def test_unknown_creation_recovers_observationally_without_repeating_the_tool(self):
        created=self.request();token=created['customerToken'];self.step(1);self.step(2)
        quote=self.host.handle('/v1/customer',{'runId':'run-1','token':token})['quotes'][0]
        self.host.handle('/v1/accept',{'runId':'run-1','token':token,'quoteId':quote['quoteId']});self.step(3);self.step(4);self.fixture.settled=True;self.fixture.unknown_creation=True
        for i in range(5,16):self.step(i)
        status=self.host.handle('/v1/status',{'runId':'run-1'});effect=next(item for item in status['effects'] if item['action']=='graphics.create')
        self.assertEqual(effect['state'],'unknown');self.assertEqual(self.fixture.creation_calls,1)
        with self.assertRaises(ValueError):self.host.handle('/v1/recovery',{'runId':'run-1','actionId':effect['id'],'mode':'retry','evidenceReference':'cannot-retry-unknown'})
        self.fixture.reconciled=True;self.host.handle('/v1/recovery',{'runId':'run-1','actionId':effect['id'],'mode':'reconcile','evidenceReference':'fixture-provider-now-confirmed'})
        self.step(20);time.sleep(1.05)
        for i in range(21,30):self.step(i)
        self.assertEqual(self.fixture.creation_calls,1)
        self.assertEqual(self.host.handle('/v1/customer',{'runId':'run-1','token':token})['orders'][0]['status'],'completed')
    def test_earned_metric_alone_does_not_finish_when_customer_hold_release_is_unresolved(self):
        created=self.request();token=created['customerToken'];self.step(1);self.step(2)
        quote=self.host.handle('/v1/customer',{'runId':'run-1','token':token})['quotes'][0]
        self.host.handle('/v1/accept',{'runId':'run-1','token':token,'quoteId':quote['quoteId']});self.step(3);self.step(4);self.fixture.settled=True;self.fixture.hold_release_pending=True
        for i in range(5,20):self.step(i)
        customer=self.host.handle('/v1/customer',{'runId':'run-1','token':token});self.assertEqual(customer['orders'][0]['status'],'delivered');self.assertEqual(self.fixture.earned,2000000)
        self.assertEqual(self.fixture.creation_calls,1)
        effect=next(item for item in self.host.handle('/v1/status',{'runId':'run-1'})['effects'] if item['action']=='record_fulfillment');self.assertEqual(effect['state'],'unknown')
    def test_internal_service_redirects_never_forward_bearer_credentials(self):
        from widemind.business.host_clients import HTTP
        calls=[]
        class Target(BaseHTTPRequestHandler):
            def log_message(self,*_args):pass
            def do_GET(self):calls.append(self.headers.get('Authorization'));self.send_response(200);self.end_headers()
        target=ThreadingHTTPServer(('127.0.0.1',0),Target);thread=threading.Thread(target=target.serve_forever,daemon=True);thread.start()
        location=f'http://127.0.0.1:{target.server_port}/stolen'
        class Redirect(BaseHTTPRequestHandler):
            def log_message(self,*_args):pass
            def do_GET(self):self.send_response(302);self.send_header('Location',location);self.end_headers()
        redirect=ThreadingHTTPServer(('127.0.0.1',0),Redirect);rt=threading.Thread(target=redirect.serve_forever,daemon=True);rt.start()
        try:
            client=HTTP(f'http://127.0.0.1:{redirect.server_port}','fixture-dedicated-service-bearer')
            with self.assertRaises(urllib.error.HTTPError):client.request('/redirect')
            self.assertEqual(calls,[])
        finally:redirect.shutdown();redirect.server_close();rt.join();target.shutdown();target.server_close();thread.join()

if __name__=='__main__':unittest.main()
