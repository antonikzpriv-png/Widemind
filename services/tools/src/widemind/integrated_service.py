"""Authenticated journal-only gateway using the authoritative economy service.

Unlike the legacy standalone development Gateway, this service owns no wallet,
grants or ledger. It verifies and starts economy reservations before adapters.
"""
from __future__ import annotations
import argparse
import base64
from dataclasses import asdict, replace
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
import os
import re
from pathlib import Path
import sqlite3
import threading
import time
from urllib.parse import urlsplit, quote as urlquote
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler
from urllib.error import HTTPError

from .config import load_config, private_directory
from .gateway import default_adapters, AuthorizedTransport
from .http import HTTPTransport
from .secrets import SecretStore
from .workspace import DedicatedWorkspace
from .tools.contracts import ToolContext, ToolError, ToolResult, Usage, Uncertain, Unavailable


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)


def integer(value, maximum=9_000_000_000_000_000):
    if isinstance(value,str) and value.isascii() and value.isdecimal() and (value=='0' or not value.startswith('0')):
        value=int(value)
    if type(value) is not int or not 0<=value<=maximum:raise ToolError('invalid_input','Expected bounded integer micro-USD or resource value.')
    return value


def service_url(value):
    parsed=urlsplit(value)
    if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('','/'):
        raise ToolError('configuration','Service URL must be a bare origin.')
    if parsed.scheme!='https' and not(parsed.scheme=='http' and parsed.hostname in {'127.0.0.1','localhost','::1'}):
        raise ToolError('configuration','HTTP services are loopback-only; use HTTPS otherwise.')
    return value.rstrip('/')


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):return None


class EconomyClient:
    def __init__(self,url,token):
        self.url=service_url(url);self.token=token
        if len(token)<32:raise ToolError('configuration','Configure a distinct economy gateway token.')
        # Loopback service traffic never leaves the executor or provider proxy.
        self.opener=build_opener(ProxyHandler({}),NoRedirect()) if urlsplit(url).hostname in {'127.0.0.1','localhost','::1'} else build_opener(NoRedirect())
    def request(self,method,path,data=None):
        req=Request(self.url+path,data=None if data is None else canonical(data).encode(),method=method,headers={'Authorization':'Bearer '+self.token,'Content-Type':'application/json'})
        try:
            with self.opener.open(req,timeout=10) as response:
                raw=response.read(2_000_001)
                if len(raw)>2_000_000:raise ToolError('economy_unavailable','Economy response exceeded limit.')
                return json.loads(raw)
        except HTTPError as exc:
            raise ToolError('economy_unavailable','Economy rejected reservation or settlement; inspect its operator diagnostics.') from exc
        except (OSError,ValueError) as exc:raise ToolError('economy_unavailable','Economy unavailable; no execution authorized.') from exc
    def reservation(self,id):return self.request('GET','/v1/reservations/'+urlquote(id,safe=''))
    def start(self,id):return self.request('POST','/v1/reservations/'+urlquote(id,safe='')+'/start',{})
    def settle(self,id,receipt):
        usage=receipt['usage']
        # Provisional tariff costs stay an unknown liability until reconciled.
        return self.request('POST','/v1/reservations/'+urlquote(id,safe='')+'/settle',{'actual_micro_usd':integer(usage['actualCharge']['micros']),'provider':usage['provider'],'account':usage['accountRef'],'external_charge_id':usage['externalEventId'],'usage':{'evidence_ref':usage['evidenceRef'],'billable_failure':1 if usage['billableFailure'] else 0},'outcome':'unknown' if usage['provisional'] else receipt['status']})


class OwnerSecrets:
    """Only owner-configured environment references may enter adapters."""
    def __init__(self,directory,mapping=None):
        self.store=SecretStore(directory);self.mapping=dict(mapping or {})
        if any(not isinstance(key,str) or not isinstance(value,str) or not re.fullmatch(r'[A-Z][A-Z0-9_]{0,127}',value) for key,value in self.mapping.items()):raise ToolError('configuration','Invalid owner secret environment mapping')
    def get(self,name):
        stored=self.store.get(name)
        return stored if stored is not None else os.environ.get(self.mapping.get(name,''))
    def set(self,name,value):self.store.set(name,value)


class Journal:
    def __init__(self,directory):
        self.root=private_directory(Path(directory));self.path=self.root/'actions.sqlite3'
        with self.connection() as db:
            db.execute('CREATE TABLE IF NOT EXISTS operations(id TEXT PRIMARY KEY,fingerprint TEXT NOT NULL,action TEXT NOT NULL,reservation TEXT NOT NULL,status TEXT NOT NULL,state TEXT NOT NULL,result TEXT,started INTEGER NOT NULL DEFAULT 0,cancelled INTEGER NOT NULL DEFAULT 0,updated REAL NOT NULL)')
    def connection(self):
        db=sqlite3.connect(self.path,timeout=10);db.row_factory=sqlite3.Row;db.execute('PRAGMA busy_timeout=10000');db.execute('PRAGMA journal_mode=WAL');return db
    def get(self,id):
        with self.connection() as db:
            row=db.execute('SELECT * FROM operations WHERE id=?',(id,)).fetchone()
            return dict(row) if row else None
    @staticmethod
    def fingerprint(action,reservation):
        return hashlib.sha256(canonical({'action':action,'reservation_id':reservation['reservationId']}).encode()).hexdigest()
    def prepare(self,action,reservation,owner_fingerprint):
        fingerprint=self.fingerprint(action,reservation)
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE');old=db.execute('SELECT * FROM operations WHERE id=?',(action['actionId'],)).fetchone()
            if old:
                if old['fingerprint']!=fingerprint:raise ToolError('conflict','Action ID reused with changed execution parameters.')
                return dict(old),False
            db.execute('INSERT INTO operations VALUES (?,?,?,?,?,?,?,?,?,?)',(action['actionId'],fingerprint,canonical(action),canonical(reservation),'prepared',canonical({'owner_fingerprint':owner_fingerprint}),None,0,0,time.time()))
        return self.get(action['actionId']),True
    def claim(self,id):
        with self.connection() as db:
            return db.execute("UPDATE operations SET status='running',updated=? WHERE id=? AND status='prepared'",(time.time(),id)).rowcount==1
    def save(self,id,**fields):
        allowed={'status','state','result','started','cancelled'}
        if not fields or not set(fields)<=allowed:raise ValueError('Journal field')
        with self.connection() as db:db.execute('UPDATE operations SET '+','.join(key+'=?' for key in fields)+',updated=? WHERE id=?',(*fields.values(),time.time(),id))
    def load(self,id):return json.loads(self.get(id)['state'])
    def update(self,id,patch):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE');state=json.loads(db.execute('SELECT state FROM operations WHERE id=?',(id,)).fetchone()[0]);state.update(patch);db.execute('UPDATE operations SET state=?,updated=? WHERE id=?',(canonical(state),time.time(),id))
    def dispatched(self,id,intent):
        self.update(id,{'external_intent':dict(intent)})


class IntegratedTools:
    def __init__(self,config,economy,*,adapters=None,transport=None,secrets=None):
        self.config=config;self.economy=economy;self.account=os.environ.get('WIDEMIND_TOOLS_ACCOUNT','dedicated-tools')
        self.journal=Journal(config['control_dir']);self.secrets=secrets or OwnerSecrets(config['control_dir'],config.get('secret_environment'))
        self.workspace=DedicatedWorkspace(config['workspace_dir'],**config.get('workspace_limits',{}))
        if self.workspace.root==self.journal.root or self.workspace.root in self.journal.root.parents or self.journal.root in self.workspace.root.parents:raise ToolError('configuration','Workspace must be disjoint from control data.')
        from .tools.workflows import GraphicWorkflow,SoftwareWorkflow,ResearchWorkflow
        from .tools.creative_graphics import CreativeGraphicsAdapter
        from .tools.creative_software import CreativeSoftwareAdapter
        self.transport=transport or HTTPTransport(config.get('policy',{}));self.adapters={a.spec.name:a for a in (adapters or default_adapters()+[GraphicWorkflow(),SoftwareWorkflow(),ResearchWorkflow(),CreativeGraphicsAdapter(),CreativeSoftwareAdapter()])}
    def settings(self,name):
        tools=self.config.get('tools',{});settings=dict(tools.get(name,{}))
        if name=='creative.graphics':
            native=tools.get('graphics',{})
            for key,value in native.items():
                if key=='enabled':continue
                if key.startswith('max_') and key in settings and type(value) is int and type(settings[key]) is int:
                    settings[key]=min(value,settings[key])
                elif key not in settings:settings[key]=value
        if name=='creative.software':
            settings['code_settings']=dict(tools.get('code.execute',{}))
        return settings
    def capabilities(self):
        result=[]
        for name,adapter in self.adapters.items():
            settings=self.settings(name)
            if name in {'creative.graphics','creative.software'}:
                dependencies=['files','graphics'] if name=='creative.graphics' else ['files','code.execute']
                if any(self.config.get('tools',{}).get(dep,{}).get('enabled') is not True for dep in dependencies):
                    result.append({'id':name,'availability':{'available':False,'reason':'Enable the required existing file, graphics or code adapters first'}});continue
            if name not in {'files','graphics','graphics.create','software.create','code.execute','creative.graphics','creative.software'} and self.config.get('policy',{}).get('allow_external_effects') is not True:
                result.append({'id':name,'availability':{'available':False,'reason':'External network operations disabled in owner policy'}});continue
            try:
                health=adapter.availability(settings,self.secrets) if settings.get('enabled') is True else {'available':False,'reason':'Disabled in owner configuration'}
                result.append({'id':name,'availability':{'available':True} if health.get('available') else {'available':False,'reason':health.get('reason','Complete provider configuration')}})
            except Exception:result.append({'id':name,'availability':{'available':False,'reason':'Provider configuration is unavailable'}})
        gmail=next((item['availability'] for item in result if item['id']=='gmail'),{'available':False,'reason':'Configure Gmail'})
        result.extend({'id':name,'availability':gmail} for name in ['gmail.send','gmail.read','gmail.list','gmail.thread'])
        return result
    def normalize(self,payload):
        if 'action' in payload and isinstance(payload['action'],dict):
            return payload['action'],payload['reservation'],False
        # Business protocol: authoritative reservation supplies enforceable limits.
        row=self.economy.reservation(payload['reservation_id']);limits=row.get('limits',{})
        action={'version':1,'actionId':payload['action_id'],'runId':payload['run_id'],'workKey':payload['action_id'],'tool':payload['action'],'input':payload['arguments'],'jobId':payload.get('job_id'),'limit':{'maximum':{'currency':'USD','micros':str(integer(payload['maximum_micro_usd']))},'maxDurationMs':limits.get('max_duration_ms',120000),'maxOutputBytes':limits.get('max_storage_bytes',1048576)}}
        if limits.get('max_tokens') is not None:action['limit']['maxTokens']=limits['max_tokens']
        reservation={'reservationId':payload['reservation_id'],'runId':action['runId'],'actionId':action['actionId'],'limit':action['limit'],'authorization':'authoritative-economy-verified'}
        return action,reservation,True
    def verify(self,action,reservation,*,completed=False):
        if action.get('version')!=1 or not isinstance(action.get('input'),dict):raise ToolError('invalid_input','Expected a version-one action with object input.')
        if reservation.get('runId')!=action.get('runId') or reservation.get('actionId')!=action.get('actionId'):raise ToolError('authorization','Reservation identity mismatch.')
        row=self.economy.reservation(reservation['reservationId'])
        for key,value in [('run_id',action['runId']),('action_id',action['actionId']),('provider',action['tool']),('account',self.account)]:
            if row.get(key)!=value:raise ToolError('authorization','Authoritative reservation binding mismatch.')
        limit=action['limit'];bounds=row.get('limits',{})
        if limit.get('maximum',{}).get('currency')!='USD' or integer(limit['maximum']['micros'])>integer(row['amount_micro_usd']):raise ToolError('authorization','Reserved monetary maximum exceeded.')
        for field,key in [('maxDurationMs','max_duration_ms'),('maxOutputBytes','max_storage_bytes'),('maxTokens','max_tokens')]:
            if field in limit and (key not in bounds or integer(limit[field])>integer(bounds[key])):raise ToolError('authorization','Execution resource limits exceed authorization.')
        if row.get('state') not in ({'reserved','running','unknown','settled'} if completed else {'reserved','running','unknown'}):raise ToolError('authorization','Reservation no longer permits execution or reconciliation.')
        return row
    def owner_fingerprint(self,action):
        name=action['tool']
        if name.startswith('gmail.') and name in {'gmail.send','gmail.read','gmail.list','gmail.thread'}:name='gmail'
        value={'tool_config':self.settings(name),'policy':self.config.get('policy',{}),'workspace':str(self.workspace.root),'adapter_version':self.adapters[name].spec.version if name in self.adapters else None}
        # Keep incoming fingerprints byte-for-byte for existing adapters so
        # interrupted template/provider operations remain reconcilable.
        if name in {'creative.graphics','creative.software'}:
            dependencies={'files','graphics'} if name=='creative.graphics' else {'files','code.execute'}
            value['dependencies']={dep:self.config.get('tools',{}).get(dep,{}) for dep in dependencies}
        if action['tool']=='gmail.read' and 'message_id' not in action.get('input',{}):value['conversation_inbox_version']=1
        return hashlib.sha256(canonical(value).encode()).hexdigest()
    def runtime_stopped(self,run_id):
        path=os.environ.get('WIDEMIND_RUNTIME_DATABASE')
        if not path:return False
        try:
            with sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True,timeout=2) as db:
                row=db.execute('SELECT desired,profile FROM runtime_runs WHERE id=?',(run_id,)).fetchone()
            return row is None or row[0]=='stop' or row[1]!='foundation'
        except (OSError,sqlite3.Error):return True
    def _adapter(self,action):
        if self.journal.load(action['actionId']).get('owner_fingerprint')!=self.owner_fingerprint(action):raise Unavailable('Owner adapter/account/policy changed; restore the original configuration or reconcile through operator review before retry.')
        name=action['tool'];args=dict(action['input']);conversation_inbox=False
        if name in {'gmail.send','gmail.read','gmail.list','gmail.thread'}:
            args['operation']=name.split('.')[1];name='gmail'
            if 'body' in args:args['text']=args.pop('body')
            if isinstance(args.get('to'),str):args['to']=[args['to']]
            if 'cursor' in args:args['page_token']=args.pop('cursor')
            if args.get('operation')=='read' and 'message_id' not in args:
                args['operation']='conversation_batch';conversation_inbox=True
            if 'attachment' in args:
                attachment=args.pop('attachment');path='deliveries/'+hashlib.sha256(action['actionId'].encode()).hexdigest()+'/'+attachment['filename'];content=attachment['content'].encode()
                try:
                    old=self.workspace.read_bytes(path,max_bytes=len(content)+1)
                    if old!=content:raise ToolError('conflict','Delivery attachment changed for an existing action.')
                except ToolError as exc:
                    if exc.code!='not_found':raise
                    self.workspace.write_bytes(path,content)
                args['attachments']=[{'path':path,'filename':attachment['filename']}]
        if name not in {'files','graphics','graphics.create','software.create','code.execute','creative.graphics','creative.software'} and self.config.get('policy',{}).get('allow_external_effects') is not True:raise Unavailable('Enable external network operations in private owner policy first.')
        if name not in self.adapters:raise Unavailable('Requested workflow is not configured; use capability status.')
        settings=self.settings(name)
        if name in {'creative.graphics','creative.software'}:
            dependencies=['files','graphics'] if name=='creative.graphics' else ['files','code.execute']
            if any(self.config.get('tools',{}).get(dep,{}).get('enabled') is not True for dep in dependencies):raise Unavailable('A required existing adapter was disabled; completed stages remain recoverable.')
        if settings.get('enabled') is not True:raise Unavailable('Tool disabled in owner configuration.')
        cap=action['limit']['maxOutputBytes'];settings['max_output_bytes']=min(settings.get('max_output_bytes',cap),cap)
        if action['limit'].get('maxTokens') is not None:settings['max_output_tokens']=min(settings.get('max_output_tokens',action['limit']['maxTokens']),action['limit']['maxTokens'])
        if conversation_inbox:
            from .conversation_gmail import ConversationGmailAdapter
            return ConversationGmailAdapter(self.adapters[name]),args,settings
        return self.adapters[name],args,settings
    def context(self,action,reservation,settings,*,reconciliation=False):
        deadline=time.monotonic()+min(300,integer(action['limit']['maxDurationMs'])/1000)
        ctx=ToolContext(action_id=action['actionId'],run_id=action['runId'],reservation_id=reservation['reservationId'],config=settings,policy=self.config.get('policy',{}),workspace=self.workspace,secrets=self.secrets,transport=self.transport,state=self.journal,deadline=deadline,authorize=lambda:self.verify(action,reservation),cancelled=lambda:False if reconciliation else bool(self.journal.get(action['actionId'])['cancelled']) or self.runtime_stopped(action['runId']),job_id=action.get('jobId'))
        ctx.transport=AuthorizedTransport(self.transport,ctx);return ctx
    def receipt(self,action,result,status='succeeded'):
        usage=result.usage
        if usage.cost_microusd is None:return {'kind':'unknown','reason':'Provider usage is not reconciled; reservation retained'}
        data={'data':dict(result.data),'artifacts':result.artifacts,'references':result.references,'nativeUsage':asdict(usage),'trust':'untrusted'}
        receipt={'version':1,'actionId':action['actionId'],'status':status,'result':data,'usage':{'actualCharge':{'currency':'USD','micros':str(integer(usage.cost_microusd))},'provider':action['tool'],'accountRef':self.account,'externalEventId':usage.provider_request_id or action['actionId'],'billableFailure':status=='failed' and integer(usage.cost_microusd)>0,'provisional':usage.provisional,'evidenceRef':'tools-journal:'+action['actionId']}}
        if len(canonical(receipt).encode())>integer(action['limit']['maxOutputBytes'])+8192:return {'kind':'unknown','reason':'Result exceeded bounded output; private usage retained for reconciliation'}
        return {'kind':'receipt','receipt':receipt}
    def execute(self,payload):
        action,reservation,business=self.normalize(payload);old=self.journal.get(action['actionId'])
        completed=bool(old and old['result'] and json.loads(old['result']).get('kind')=='receipt')
        self.verify(action,reservation,completed=completed)
        row,fresh=self.journal.prepare(action,reservation,self.owner_fingerprint(action))
        if row['result'] is not None:return self._business(json.loads(row['result']),business,reservation)
        if not self.journal.claim(action['actionId']):return {'kind':'unknown','reason':'Action already dispatched; reconcile before retry'}
        try:
            adapter,args,settings=self._adapter(action);quote=adapter.quote(args,settings)
            if integer(quote.maximum_microusd)>integer(action['limit']['maximum']['micros']):raise ToolError('budget_limit','Adapter quote exceeds authorized maximum.')
            if quote.paid and integer(quote.maximum_microusd)==0:raise ToolError('budget_limit','Paid execution requires a positive bounded quote.')
            if not row['started']:
                self.economy.start(reservation['reservationId']);self.journal.save(action['actionId'],started=1)
            ctx=self.context(action,reservation,settings);ctx.check();result=adapter.execute(args,ctx)
            outcome=self.receipt(action,result)
        except ToolError as exc:
            # Any dispatch or local mutation intent makes failure uncertain.
            state=self.journal.load(action['actionId'])
            if isinstance(exc,Uncertain) or state.get('external_intent') or state.get('local_intent') or exc.usage is None and self.journal.get(action['actionId'])['started']:
                if exc.usage is not None and exc.usage.cost_microusd is not None:
                    partial=self.receipt(action,ToolResult({'outcome':'uncertain'},usage=replace(exc.usage,provisional=True)),'failed')
                    self.journal.update(action['actionId'],{'partial_usage':asdict(exc.usage)})
                    if partial['kind']=='receipt':
                        try:self.economy.settle(reservation['reservationId'],partial['receipt'])
                        except ToolError:pass
                outcome={'kind':'unknown','reason':'Tool outcome requires reconciliation; no automatic duplicate effect'}
            else:outcome=self.receipt(action,ToolResult({'error':{'code':exc.code,'message':exc.message}},usage=exc.usage or Usage()),'failed')
        except Exception:outcome={'kind':'unknown','reason':'Tool interrupted; reconcile external effect and usage before retry'}
        self.journal.save(action['actionId'],status='unknown' if outcome['kind']=='unknown' else outcome['receipt']['status'],result=canonical(outcome))
        return self._business(outcome,business,reservation)
    def reconcile(self,payload):
        if set(payload)=={'action_id'}:
            saved=self.journal.get(payload['action_id'])
            if saved is None:return {'state':'unknown','status':'unresolved','reason':'No durable execution journal proves this action'}
            action,reservation,business=json.loads(saved['action']),json.loads(saved['reservation']),True
        else:action,reservation,business=self.normalize(payload)
        row=self.journal.get(action['actionId'])
        if row is None:return {'kind':'not_performed','evidenceRef':'tools-journal:never-dispatched:'+action['actionId']}
        if row['fingerprint']!=self.journal.fingerprint(action,reservation):raise ToolError('conflict','Reconciliation parameters differ from the original durable action.')
        self.verify(action,reservation,completed=bool(row['result'] and json.loads(row['result']).get('kind')=='receipt'))
        if row['result'] is not None:
            known=json.loads(row['result'])
            if known['kind']=='receipt':return self._business(known,business,reservation)
        if row['status']=='prepared':return {'kind':'not_performed','evidenceRef':'tools-journal:prepared:'+action['actionId']}
        try:
            adapter,args,settings=self._adapter(action);ctx=self.context(action,reservation,settings,reconciliation=True);result=adapter.reconcile(args,ctx)
            if result.outcome=='completed' and result.result is not None:outcome=self.receipt(action,result.result)
            elif result.outcome=='not_executed':
                self.journal.save(action['actionId'],status='prepared',result=None)
                return {'kind':'not_performed','evidenceRef':'adapter-proof:'+action['actionId']}
            else:return {'kind':'unknown','reason':result.detail or 'Provider outcome remains unknown'}
            self.journal.save(action['actionId'],status='unknown' if outcome['kind']=='unknown' else outcome['receipt']['status'],result=canonical(outcome));return self._business(outcome,business,reservation)
        except Exception:return {'kind':'unknown','reason':'Provider reconciliation unavailable; reservation retained'}
    def _business(self,outcome,business,reservation):
        if not business:return outcome
        if outcome['kind']=='receipt':
            self.economy.settle(reservation['reservationId'],outcome['receipt'])
            r=outcome['receipt'];data=r['result']['data']
            if 'message_id' in data:data={**data,'delivery_state':'accepted'}
            return {'state':r['status'],'data':data,'status':r['status'],'result':data,'actual_micro_usd':integer(r['usage']['actualCharge']['micros']),'provisional':r['usage']['provisional'],'receipt':r}
        return {'state':'unknown','status':'unresolved','reason':outcome.get('reason','Outcome unknown')}
    def artifact(self,path):
        data=self.workspace.read_bytes(path,max_bytes=1_000_000)
        return {'path':path,'content_base64':base64.b64encode(data).decode(),'sha256':hashlib.sha256(data).hexdigest()}


def default_config():
    state=os.environ.get('WIDEMIND_TOOLS_STATE','./var/tools/control');workspace=os.environ.get('WIDEMIND_TOOLS_WORKSPACE','./var/tools/workspace')
    return {'control_dir':str(Path(state).absolute()),'workspace_dir':str(Path(workspace).absolute()),'policy':{'profile':'development','allow_external_effects':False,'allowed_email_recipients':[]},'tools':{'files':{'enabled':True},'graphics':{'enabled':True},'graphics.create':{'enabled':True},'software.create':{'enabled':True}}}


def server(application,token,host='127.0.0.1',port=8081):
    if host not in {'127.0.0.1','::1','localhost'}:raise ValueError('Use an authenticated TLS proxy for non-loopback exposure')
    if len(token)<32:raise ValueError('Tools service token must be at least 32 characters')
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def respond(self,status,value):
            raw=canonical(value).encode();self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
        def do_GET(self):
            if not self.headers.get('Authorization','').isascii() or not hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+token):self.respond(401,{'error':'Tools authentication required'});return
            try:
                if self.path=='/health':self.respond(200,{'available':True,'service':'tools','wallet':'external-economy'});return
                if self.path=='/capabilities':self.respond(200,{'capabilities':application.capabilities()});return
                self.respond(404,{'error':'Not found'})
            except Exception:self.respond(503,{'error':'Tools dependency unavailable'})
        def do_POST(self):
            if not self.headers.get('Authorization','').isascii() or not hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+token):self.respond(401,{'error':'Tools authentication required'});return
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=1_100_000:self.respond(413,{'error':'Bounded JSON body required'});return
                payload=json.loads(self.rfile.read(size))
                if self.path=='/execute':self.respond(200,application.execute(payload));return
                if self.path=='/reconcile':self.respond(200,application.reconcile(payload));return
                if self.path=='/artifacts/read':self.respond(200,application.artifact(payload['path']));return
                if self.path=='/cancel':application.journal.save(payload['actionId'],cancelled=1);self.respond(200,{'cancelRequested':True});return
                self.respond(404,{'error':'Not found'})
            except ToolError as exc:self.respond(409 if exc.code in {'authorization','conflict','budget_limit'} else 503,{'error':exc.code,'message':exc.message})
            except Exception:self.respond(400,{'error':'Invalid request or unavailable dependency'})
    return ThreadingHTTPServer((host,port),Handler)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--host',default='127.0.0.1');parser.add_argument('--port',type=int,default=8081);parser.add_argument('--config',default=os.environ.get('WIDEMIND_TOOLS_CONFIG'));args=parser.parse_args()
    config=load_config(args.config) if args.config else default_config()
    economy=EconomyClient(os.environ.get('WIDEMIND_ECONOMY_URL','http://127.0.0.1:8080'),os.environ.get('WIDEMIND_ECONOMY_GATEWAY_TOKEN',''))
    http=server(IntegratedTools(config,economy),os.environ.get('WIDEMIND_TOOLS_TOKEN',''),args.host,args.port)
    try:http.serve_forever()
    except KeyboardInterrupt:pass
    finally:http.server_close()
if __name__=='__main__':main()
