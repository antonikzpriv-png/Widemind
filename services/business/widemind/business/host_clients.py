"""Dedicated HTTP clients. No credentials or raw exceptions enter customer output."""
import hashlib
import json
import os
import sqlite3
import urllib.request
from urllib.parse import urlsplit, quote
from pathlib import Path
from .contracts import Outcome
from .store import encode, identity

class Unavailable(Exception): pass

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*_args,**_kwargs):return None

class HTTP:
    def __init__(self, url, token):
        self.url, self.token = url.rstrip('/'), token
        parsed = urlsplit(self.url)
        if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {'','/'}:
            raise ValueError('service URL must be a bare origin')
        if parsed.scheme != 'https' and not (parsed.scheme == 'http' and parsed.hostname in {'127.0.0.1','localhost','::1'}):
            raise ValueError('service HTTP requires loopback; remote requires HTTPS')
        # Dedicated loopback services are local IPC, never outbound through a proxy.
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect()) if parsed.hostname in {'127.0.0.1','localhost','::1'} else urllib.request.build_opener(NoRedirect())
    def request(self, path, body=None, method=None):
        if not self.token: raise Unavailable('dedicated service credentials are not configured')
        payload = None if body is None else encode(body).encode()
        request = urllib.request.Request(self.url+path, payload, {'Authorization':'Bearer '+self.token,'Content-Type':'application/json'}, method=method or ('GET' if body is None else 'POST'))
        with self.opener.open(request, timeout=20) as response:
            data=response.read(2_000_001)
            if len(data)>2_000_000: raise Unavailable('service response exceeds limit')
            return json.loads(data)

class RuntimeState:
    def __init__(self,path,mode): self.path,self.mode=path,mode
    def exists(self,run_id):
        if not self.path:return False
        try:
            with sqlite3.connect(Path(self.path).resolve().as_uri()+'?mode=ro',uri=True,timeout=5) as db:
                return db.execute("SELECT 1 FROM runtime_runs WHERE id=? AND profile='foundation'",(run_id,)).fetchone() is not None
        except sqlite3.Error:return False
    def state(self,run_id):
        if not self.path: return 'failed'
        try:
            with sqlite3.connect(Path(self.path).resolve().as_uri()+'?mode=ro',uri=True,timeout=5) as db:
                row=db.execute('SELECT state,desired,profile FROM runtime_runs WHERE id=?',(run_id,)).fetchone()
            if not row or row[2]!='foundation': return 'failed'
            return 'paused' if row[1]=='pause' else 'stopped' if row[1]=='stop' else row[0]
        except sqlite3.Error: return 'failed'

class ToolsClient:
    def __init__(self,env,mode):
        self.mode=mode
        self._capabilities=None
        self.http=HTTP(env.get('WIDEMIND_TOOLS_URL','http://127.0.0.1:8081'),env.get('WIDEMIND_TOOLS_TOKEN',''))
    def available(self,action):
        if action=='gmail.send': return True # dedicated customer portal is an actual local delivery transport
        try:
            if self._capabilities is None:
                self._capabilities=[]
                result=self.http.request('/capabilities')
                self._capabilities=result.get('capabilities',[]) if isinstance(result,dict) else result
            items=self._capabilities
            for item in items:
                if item.get('id',item.get('name'))==action:
                    health=item.get('availability',item.get('available',False))
                    return health.get('available',False) if isinstance(health,dict) else health is True
            return False
        except Exception: return False
    def execute(self,**request):
        try:
            result=self.http.request('/execute',request)
            return self.outcome(result)
        except Exception: return Outcome('unknown',reason='tools service outcome unavailable; reconcile before retry')
    def reconcile(self,*,action_id):
        try: return self.outcome(self.http.request('/reconcile',{'action_id':action_id}))
        except Exception: return Outcome('unknown',reason='tools reconciliation unavailable')
    @staticmethod
    def outcome(result):
        if result.get('state') in {'succeeded','failed','unknown'}: return Outcome(result['state'],result.get('data',{}),result.get('reason',''))
        if result.get('kind')=='receipt':
            receipt=result['receipt'];return Outcome('succeeded' if receipt['status']=='succeeded' else 'failed',receipt.get('result',{}))
        return Outcome('unknown',reason='tools did not return a confirmed outcome')

class EconomyClient:
    def __init__(self,env,store,mode):
        self.store,self.mode=store,mode
        self.http=HTTP(env.get('WIDEMIND_ECONOMY_URL','http://127.0.0.1:8080'),env.get('WIDEMIND_ECONOMY_OPERATOR_TOKEN',''))
        self.account=env.get('WIDEMIND_TOOLS_ACCOUNT','dedicated-tools')
    def check_mode(self,run_id):
        observed=self.http.request(f'/v1/runs/{quote(run_id,safe="")}/balance')
        if observed.get('run_id')!=run_id or observed.get('mode')!=self.mode:raise Unavailable('economy run mode differs from business configuration')
    def reserve(self,*,run_id,job_id,action_id,maximum_micro_usd):
        self.check_mode(run_id)
        order=self.store.one('SELECT plan FROM orders WHERE id=? AND run_id=?',(job_id,run_id))
        if not order: return Outcome('failed',reason='frozen order plan unavailable')
        plan=json.loads(order['plan'])
        # The reserve-effect ID and actual create-tool ID differ in the audited business journal.
        actual_action=identity(run_id,job_id,'create')
        result=self.http.request(f'/v1/runs/{quote(run_id,safe="")}/reservations',{'action_id':actual_action,'amount_micro_usd':maximum_micro_usd,'idempotency_key':action_id,'provider':plan['action'],'account':self.account,'limits':{'max_duration_ms':120000,'max_storage_bytes':1000000}})
        return Outcome('succeeded',{'reservation_id':result['reservation_id']})
    def reserve_conversation(self,*,run_id,conversation_id,action_id,maximum_micro_usd,max_tokens=2000,tool='ai.generate'):
        """Reserve an exact conversational tool action without inventing an order.

        The caller supplies the actual durable tool-effect ID, not the separate
        reservation-effect ID. This uses the selected integrated economy only;
        provider execution and actual-charge settlement remain gateway-owned.
        """
        if tool not in {'ai.generate','gmail.read','gmail.list','gmail.thread','gmail.send'}:
            raise ValueError('unsupported conversation tool')
        if type(maximum_micro_usd) is not int or not 0 <= maximum_micro_usd <= 10**12:
            raise ValueError('invalid conversation spending ceiling')
        if tool == 'ai.generate' and maximum_micro_usd == 0:
            raise ValueError('model calls require a positive spending reservation')
        if tool != 'ai.generate' and maximum_micro_usd != 0:
            raise ValueError('Gmail transport uses zero direct provider cost')
        if type(max_tokens) is not int or not 1 <= max_tokens <= 16000:
            raise ValueError('invalid model output ceiling')
        for value in (run_id,conversation_id,action_id):
            if not isinstance(value,str) or not value or len(value)>200 or any(ord(c)<32 for c in value):
                raise ValueError('invalid conversation action identity')
        self.check_mode(run_id)
        limits={'max_duration_ms':120000,'max_storage_bytes':65536}
        if tool == 'ai.generate':limits['max_tokens']=max_tokens
        result=self.http.request(f'/v1/runs/{quote(run_id,safe="")}/reservations',{
            'action_id':action_id,'amount_micro_usd':maximum_micro_usd,
            'idempotency_key':identity(run_id,conversation_id,'conversation-reservation',action_id),
            'provider':tool,'account':self.account,'limits':limits})
        if (not isinstance(result,dict) or not isinstance(result.get('reservation_id'),str)
                or not result['reservation_id']):
            return Outcome('unknown',reason='conversation reservation needs authoritative reconciliation')
        return Outcome('succeeded',{'reservation_id':result['reservation_id']})
    def payment_link(self,*,run_id,job_id,action_id,amount_micro_usd,currency):
        if currency!='USD':return Outcome('failed',reason='unsupported currency')
        self.check_mode(run_id)
        result=self.http.request(f'/v1/runs/{quote(run_id,safe="")}/checkout',{'request_id':action_id,'job_id':job_id,'amount_micro_usd':amount_micro_usd,'description':'WideMind scoped order'})
        if not result.get('url'):return Outcome('unknown',reason='checkout requires recovery')
        return Outcome('succeeded',{'url':result['url'],'payment_id':result['payment_id']})
    def payment(self,*,run_id,job_id):
        payments=self.http.request(f'/v1/runs/{quote(run_id,safe="")}/payments')
        matches=[item for item in payments if item.get('job_id')==job_id]
        if len(matches)>1: raise Unavailable('multiple payment mappings require operator review')
        if not matches:
            document={'run_id':run_id,'job_id':job_id,'status':'pending','currency':'USD','mode':self.mode,'receipts_micro_usd':0,'refunds_micro_usd':0,'fees_micro_usd':0,'earned_micro_usd':0,'operating_cost_micro_usd':0,'costs_final':True,'_provider_revision':0}
        else:
            payment=matches[0]
            order=self.store.one('SELECT reservation_id FROM orders WHERE id=? AND run_id=?',(job_id,run_id))
            suffix=('?reservation_id='+quote(order['reservation_id'],safe='')) if order and order.get('reservation_id') else ''
            snapshot=self.http.request(f'/v1/payments/{quote(payment["payment_id"],safe="")}/snapshot'+suffix)
            if snapshot.get('run_id')!=run_id or snapshot.get('job_id')!=job_id or snapshot.get('mode')!=self.mode:raise Unavailable('payment snapshot identity mismatch')
            document={key:snapshot[key] for key in ('run_id','job_id','status','currency','mode','receipts_micro_usd','refunds_micro_usd','fees_micro_usd','earned_micro_usd','operating_cost_micro_usd','costs_final')}
            document['_provider_revision']=snapshot['revision']
            document['status']={'partially_refunded':'partial','failed':'pending'}.get(document['status'],document['status'])
        digest=hashlib.sha256(encode({key:value for key,value in document.items() if not key.startswith('_')}).encode()).hexdigest()
        with self.store.transaction():
            previous=self.store.one('SELECT * FROM host_payment_snapshots WHERE job_id=?',(job_id,))
            if previous and json.loads(previous['document']).get('_provider_revision',0)>document['_provider_revision']:raise Unavailable('stale provider snapshot cannot overwrite newer financial observation')
            revision=(previous['revision']+(previous['digest']!=digest)) if previous else 1
            self.store.db.execute('INSERT INTO host_payment_snapshots VALUES (?,?,?,?,?) ON CONFLICT(job_id) DO UPDATE SET revision=excluded.revision,digest=excluded.digest,document=excluded.document',(job_id,run_id,revision,digest,encode(document)))
        return {**{key:value for key,value in document.items() if not key.startswith('_')},'revision':revision}
    def record_fulfillment(self,*,run_id,job_id,action_id,delivery_reference):
        order=self.store.one('SELECT * FROM orders WHERE id=? AND run_id=?',(job_id,run_id))
        notification=self.store.one('SELECT id FROM host_notifications WHERE id=? AND entity_id=? AND purpose=?',(delivery_reference,job_id,'delivery'))
        if not order or not notification: return Outcome('failed',reason='private delivery publication not verified')
        snapshot=self.payment(run_id=run_id,job_id=job_id)
        amount=max(0,min(order['price_micro_usd'],snapshot['receipts_micro_usd']-snapshot['refunds_micro_usd']))
        self.http.request(f'/v1/runs/{quote(run_id,safe="")}/earned-revenue',{'amount_micro_usd':amount,'job_id':job_id,'reference':delivery_reference,'idempotency_key':action_id})
        ledger=self.http.request(f'/v1/runs/{quote(run_id,safe="")}/ledger')
        if not any(row.get('category',row.get('kind'))=='earned_revenue' and row.get('reference')==delivery_reference and row.get('metadata',{}).get('job_id')==job_id and row.get('metadata',{}).get('reported_micro_usd')==amount for row in ledger):return Outcome('unknown',reason='earned metric observation requires reconciliation')
        payments=self.http.request(f'/v1/runs/{quote(run_id,safe="")}/payments')
        matching=[payment for payment in payments if payment.get('job_id')==job_id]
        if len(matching)!=1:return Outcome('unknown',reason='payment mapping requires review')
        for payment in matching:
            released=self.http.request(f'/v1/payments/{quote(payment["payment_id"],safe="")}/release-hold',{'idempotency_key':action_id+':release'})
            expected=max(0,snapshot['receipts_micro_usd']-snapshot['refunds_micro_usd']-snapshot['fees_micro_usd'])
            if released.get('released_micro_usd',-1)<expected or released.get('pending_liability_micro_usd',0)>0:return Outcome('unknown',reason='customer hold release remains unresolved')
        return Outcome('succeeded',{'delivery_reference':delivery_reference})
    def release(self,*,run_id,job_id,action_id,reservation_id):
        self.http.request(f'/v1/reservations/{quote(reservation_id,safe="")}/cancel',{})
        return Outcome('succeeded',{'reservation_id':reservation_id})
    def reconcile(self,*,action_id):
        effect=self.store.one('SELECT * FROM effects WHERE id=?',(action_id,))
        if not effect:return Outcome('unknown',reason='unknown economy action')
        run,job,action=effect['run_id'],effect['job_id'],effect['action']
        try:
            if action=='reserve_conversation':
                request=json.loads(effect['request'])
                expected=request.get('actual_action_id')
                tool=request.get('tool','ai.generate')
                limits={'max_duration_ms':120000,'max_storage_bytes':65536}
                if tool=='ai.generate':limits['max_tokens']=request.get('max_tokens',2000)
                found=[r for r in self.http.request(f'/v1/runs/{quote(run,safe="")}/reservations')
                       if r.get('action_id')==expected and r.get('run_id')==run
                       and r.get('provider')==tool and r.get('account')==self.account
                       and r.get('amount_micro_usd')==request.get('maximum_micro_usd') and r.get('limits')==limits]
                if len(found)==1:return Outcome('succeeded',{'reservation_id':found[0]['reservation_id']})
            if action=='reserve':
                found=[r for r in self.http.request(f'/v1/runs/{quote(run,safe="")}/reservations') if r['action_id']==identity(run,job,'create')]
                if len(found)==1:return Outcome('succeeded',{'reservation_id':found[0]['reservation_id']})
            if action=='payment_link':
                found=[c for c in self.http.request(f'/v1/runs/{quote(run,safe="")}/checkouts') if c['request_id']==action_id]
                if len(found)==1 and found[0].get('url'):return Outcome('succeeded',{'url':found[0]['url'],'payment_id':found[0]['payment_id']})
            if action=='record_fulfillment':
                ledger=self.http.request(f'/v1/runs/{quote(run,safe="")}/ledger')
                if any(row.get('category',row.get('kind'))=='earned_revenue' and row.get('reference')==json.loads(effect['request'])['reference'] and row.get('metadata',{}).get('job_id')==job for row in ledger):
                    return self.record_fulfillment(run_id=run,job_id=job,action_id=action_id,delivery_reference=json.loads(effect['request'])['reference'])
        except Exception: pass
        return Outcome('unknown',reason='authoritative economy outcome requires reconciliation')
