"""Integrated path: real local tools, no local wallet or customer payment fixtures."""
import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.request import Request,build_opener,ProxyHandler
from urllib.error import HTTPError
from widemind.integrated_service import IntegratedTools,server
from widemind.tools.contracts import ToolSpec,Quote,ToolResult,Usage,Reconciliation,Uncertain

class Economy:
    def __init__(self):self.rows={};self.starts=[];self.settlements=[]
    def add(self,action,id='reservation-1'):
        self.rows[id]={'reservation_id':id,'run_id':action['runId'],'action_id':action['actionId'],'provider':action['tool'],'account':'dedicated-tools','amount_micro_usd':int(action['limit']['maximum']['micros']),'limits':{'max_duration_ms':10000,'max_storage_bytes':100000},'state':'reserved'}
        return {'reservationId':id,'runId':action['runId'],'actionId':action['actionId'],'limit':action['limit'],'authorization':'fixture-opaque-not-authority'}
    def reservation(self,id):return copy.deepcopy(self.rows[id])
    def start(self,id):
        assert self.rows[id]['state']=='reserved';self.rows[id]['state']='running';self.starts.append(id)
    def settle(self,id,receipt):self.settlements.append((id,receipt));self.rows[id]['state']='unknown' if receipt['usage']['provisional'] else 'settled';return {}

class Secrets:
    def get(self,name):return None
    def set(self,name,value):raise AssertionError('No secrets in local acceptance')

def action(tool='files',arguments=None,id='action-1'):
    return {'version':1,'actionId':id,'runId':'run-1','workKey':id,'tool':tool,'input':arguments or {'operation':'create','path':'note.txt','text':'Actual local note'},'limit':{'maximum':{'currency':'USD','micros':'0'},'maxDurationMs':10000,'maxOutputBytes':100000}}

class Integration(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory();self.root=Path(self.directory.name);self.economy=Economy()
        self.config={'control_dir':str(self.root/'control'),'workspace_dir':str(self.root/'workspace'),'policy':{'profile':'development','allow_external_effects':False,'allowed_email_recipients':[]},'tools':{'files':{'enabled':True},'graphics':{'enabled':True},'graphics.create':{'enabled':True},'software.create':{'enabled':True}}}
        self.tools=IntegratedTools(self.config,self.economy,secrets=Secrets())
    def tearDown(self):self.directory.cleanup()
    def execute(self,a):
        r=self.economy.add(a);return self.tools.execute({'action':a,'reservation':r}),r
    def test_actual_file_is_durable_and_duplicate_does_not_dispatch(self):
        a=action();result,reservation=self.execute(a);self.assertEqual(result['kind'],'receipt');self.assertEqual((self.root/'workspace/note.txt').read_text(),'Actual local note');self.assertEqual(result['receipt']['usage']['actualCharge']['micros'],'0');self.assertEqual(len(self.economy.starts),1)
        self.economy.settle('reservation-1',result['receipt']);self.assertEqual(self.tools.execute({'action':a,'reservation':reservation}),result);self.assertEqual(len(self.economy.starts),1)
        other=IntegratedTools(self.config,self.economy,secrets=Secrets());self.assertEqual(other.reconcile({'action':a,'reservation':reservation}),result)
        self.assertFalse((self.root/'control/control.sqlite3').exists(),'Integrated service must not create the legacy wallet')
    def test_binding_mismatch_and_limits_fail_before_effect(self):
        a=action();r=self.economy.add(a);self.economy.rows['reservation-1']['run_id']='other-run'
        with self.assertRaises(Exception):self.tools.execute({'action':a,'reservation':r})
        self.assertFalse((self.root/'workspace/note.txt').exists());self.assertEqual(self.economy.starts,[])
    def test_actual_svg_and_static_source_deliverables(self):
        for index,(tool,ending) in enumerate([('graphics.create','</svg>'),('software.create','</html>')]):
            a=action(tool,{'title':'Local <test>','description':'Requested work'},id=f'action-{index}');r=self.economy.add(a,id=f'reservation-{index}');result=self.tools.execute({'action':a,'reservation':r});self.assertEqual(result['kind'],'receipt');data=result['receipt']['result']['data'];self.assertIn(ending,data['content']);self.assertNotIn('<test>',data['content']);self.assertTrue(all(value is True or type(value) is int and value>0 for value in data['checks'].values()))
    def test_unknown_effect_cannot_execute_again_without_reconciliation(self):
        class Adapter:
            spec=ToolSpec('fixture','controlled uncertain provider','external')
            def availability(self,c,s):return {'available':True}
            def quote(self,a,c):return Quote()
            def execute(self,a,ctx):ctx.mark_dispatched({'effect':'fixture'});raise Uncertain('Response lost',usage=Usage(0))
            def reconcile(self,a,ctx):return Reconciliation('uncertain',detail='No definitive provider evidence')
        self.config['policy']['allow_external_effects']=True;self.config['tools']['fixture']={'enabled':True};tools=IntegratedTools(self.config,self.economy,adapters=[Adapter()],secrets=Secrets());a=action('fixture');r=self.economy.add(a);first=tools.execute({'action':a,'reservation':r});self.assertEqual(first['kind'],'unknown');self.assertEqual(tools.execute({'action':a,'reservation':r}),first);self.assertEqual(tools.reconcile({'action':a,'reservation':r})['kind'],'unknown');self.assertEqual(len(self.economy.starts),1)
    def test_authenticated_http_and_private_artifact(self):
        token='fixture-only-tools-token-'+('x'*32);http=server(self.tools,token,port=0);thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start();origin=f'http://127.0.0.1:{http.server_address[1]}';opener=build_opener(ProxyHandler({}))
        try:
            with self.assertRaises(HTTPError) as caught:opener.open(origin+'/capabilities')
            self.assertEqual(caught.exception.code,401)
            a=action();r=self.economy.add(a);request=Request(origin+'/execute',data=json.dumps({'action':a,'reservation':r}).encode(),headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
            with opener.open(request) as response:self.assertEqual(json.load(response)['kind'],'receipt')
            with opener.open(Request(origin+'/artifacts/read',data=b'{"path":"note.txt"}',headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})) as response:self.assertEqual(json.load(response)['path'],'note.txt')
        finally:http.shutdown();http.server_close();thread.join()

class ProvisionalIntegration(unittest.TestCase):
    def test_business_rpc_keeps_provisional_cost_unknown_and_replay_is_not_execution(self):
        class Paid:
            spec=ToolSpec('fixture.paid','controlled measured usage','external')
            calls=0
            def availability(self,c,s):return {'available':True}
            def quote(self,a,c):return Quote(10,'fixture',True)
            def execute(self,a,ctx):self.calls+=1;ctx.mark_dispatched({'fixture':'request'});return ToolResult({'content':'measured fixture'},Usage(4,provider_request_id='native-request-1',provisional=True))
            def reconcile(self,a,ctx):return Reconciliation('uncertain',detail='No invoice configured')
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);econ=Economy();adapter=Paid();config={'control_dir':str(root/'control'),'workspace_dir':str(root/'workspace'),'tools':{'fixture.paid':{'enabled':True}},'policy':{'profile':'sandbox','allow_external_effects':True}}
            tools=IntegratedTools(config,econ,adapters=[adapter],secrets=Secrets());a=action('fixture.paid');a['limit']['maximum']['micros']='10';econ.add(a)
            payload={'run_id':a['runId'],'action_id':a['actionId'],'job_id':'job-1','action':'fixture.paid','arguments':a['input'],'reservation_id':'reservation-1','maximum_micro_usd':10}
            result=tools.execute(payload);self.assertTrue(result['provisional']);self.assertEqual(econ.rows['reservation-1']['state'],'unknown');self.assertEqual(adapter.calls,1);self.assertEqual(tools.execute(payload),result);self.assertEqual(adapter.calls,1)

class OwnerSecretIntegration(unittest.TestCase):
    def test_environment_injection_requires_owner_mapping_and_persistent_rotation_wins(self):
        import os
        from unittest.mock import patch
        from widemind.integrated_service import OwnerSecrets
        with tempfile.TemporaryDirectory() as directory,patch.dict(os.environ,{'WIDEMIND_FIXTURE_PROVIDER_KEY':'fixture-only-env-value'}):
            secret=OwnerSecrets(directory,{'provider-key':'WIDEMIND_FIXTURE_PROVIDER_KEY'})
            self.assertEqual(secret.get('provider-key'),'fixture-only-env-value')
            self.assertIsNone(secret.get('WIDEMIND_FIXTURE_PROVIDER_KEY'))
            secret.set('provider-key','fixture-only-rotated-value')
            self.assertEqual(secret.get('provider-key'),'fixture-only-rotated-value')

class BusinessProtocolIntegration(unittest.TestCase):
    def test_business_outcome_shape_and_action_only_reconciliation(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);econ=Economy();config={'control_dir':str(root/'control'),'workspace_dir':str(root/'workspace'),'tools':{'files':{'enabled':True}},'policy':{'profile':'development','allow_external_effects':False}}
            tools=IntegratedTools(config,econ,secrets=Secrets());a=action();econ.add(a)
            payload={'run_id':a['runId'],'action_id':a['actionId'],'job_id':'job-1','action':'files','arguments':a['input'],'reservation_id':'reservation-1','maximum_micro_usd':0}
            result=tools.execute(payload);self.assertEqual(result['state'],'succeeded');self.assertEqual(result['data']['operation'],'create');self.assertEqual(tools.reconcile({'action_id':a['actionId']}),result);self.assertEqual(len(econ.starts),1)

class RecoveryFencingIntegration(unittest.TestCase):
    def test_changed_owner_account_configuration_does_not_reconcile_against_new_account(self):
        class Adapter:
            spec=ToolSpec('fixture.account','controlled identity','external')
            def availability(self,c,s):return {'available':True}
            def quote(self,a,c):return Quote()
            def execute(self,a,ctx):ctx.mark_dispatched({'request':'accepted'});raise Uncertain('Lost reply')
            def reconcile(self,a,ctx):raise AssertionError('Must not query a changed owner account')
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);econ=Economy();config={'control_dir':str(root/'control'),'workspace_dir':str(root/'workspace'),'tools':{'fixture.account':{'enabled':True,'expected_account':'first@example.invalid'}},'policy':{'profile':'sandbox','allow_external_effects':True}}
            tools=IntegratedTools(config,econ,adapters=[Adapter()],secrets=Secrets());a=action('fixture.account');r=econ.add(a);self.assertEqual(tools.execute({'action':a,'reservation':r})['kind'],'unknown')
            config['tools']['fixture.account']['expected_account']='other@example.invalid';self.assertEqual(tools.reconcile({'action':a,'reservation':r})['kind'],'unknown');self.assertEqual(len(econ.starts),1)
    def test_operator_stop_cancels_inner_tool_without_releasing_an_uncertain_hold(self):
        import os,sqlite3
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);runtime=root/'runtime.sqlite';db=sqlite3.connect(runtime);db.execute('CREATE TABLE runtime_runs(id TEXT,desired TEXT,profile TEXT)');db.execute("INSERT INTO runtime_runs VALUES ('run-1','stop','foundation')");db.commit();db.close()
            econ=Economy();config={'control_dir':str(root/'control'),'workspace_dir':str(root/'workspace'),'tools':{'files':{'enabled':True}},'policy':{'profile':'development','allow_external_effects':False}}
            tools=IntegratedTools(config,econ,secrets=Secrets());a=action();r=econ.add(a)
            with patch.dict(os.environ,{'WIDEMIND_RUNTIME_DATABASE':str(runtime)}):
                result=tools.execute({'action':a,'reservation':r})
            self.assertEqual(result['kind'],'unknown');self.assertFalse((root/'workspace/note.txt').exists());self.assertEqual(econ.rows['reservation-1']['state'],'running')

class ServiceRedirectIntegration(unittest.TestCase):
    def test_authoritative_service_redirect_never_forwards_gateway_credential(self):
        from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
        from widemind.integrated_service import EconomyClient
        calls=[]
        class Other(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):calls.append(self.headers.get('Authorization'));self.send_response(200);self.end_headers();self.wfile.write(b'{}')
        other=ThreadingHTTPServer(('127.0.0.1',0),Other)
        class Redirect(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):self.send_response(302);self.send_header('Location',f'http://127.0.0.1:{other.server_address[1]}/other-account');self.end_headers()
        redirect=ThreadingHTTPServer(('127.0.0.1',0),Redirect);threads=[threading.Thread(target=s.serve_forever,daemon=True) for s in [other,redirect]]
        for thread in threads:thread.start()
        try:
            client=EconomyClient(f'http://127.0.0.1:{redirect.server_address[1]}','fixture-economy-gateway-'+('x'*32))
            with self.assertRaises(Exception):client.reservation('reservation-1')
            self.assertEqual(calls,[])
        finally:
            for service in [other,redirect]:service.shutdown();service.server_close()
            for thread in threads:thread.join()
