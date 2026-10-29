"""Dedicated HTTP clients. No credentials or raw exceptions enter customer output."""
import hashlib
import json
import os
import re
import sqlite3
import urllib.error
import urllib.request
from datetime import datetime
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
    def _source_references(receipt):
        """Read bounded transport provenance from the gateway receipt envelope.

        Tool result data is untrusted and cannot supply its own retrieval time or
        body hash. Invalid/absent envelopes confer no provenance; cached receipts
        retain their original observation times rather than being stamped now.
        """
        if not isinstance(receipt,dict) or not isinstance(receipt.get('result'),dict):
            return None
        references=receipt['result'].get('references')
        if not isinstance(references,list) or not 1 <= len(references) <= 30:
            return None
        cleaned=[]
        for reference in references:
            if not isinstance(reference,dict):
                return None
            digest=reference.get('sha256')
            url=reference.get('url')
            retrieved=reference.get('retrieved_at')
            kind=reference.get('kind')
            if (not isinstance(digest,str) or not re.fullmatch('[0-9a-f]{64}',digest) or
                reference.get('source_id')!='sha256:'+digest or
                not isinstance(url,str) or not 1 <= len(url) <= 4096 or
                any(c.isspace() or ord(c)<32 or ord(c)==127 or c=='\\' for c in url) or
                not isinstance(retrieved,str) or not 1 <= len(retrieved) <= 64 or
                not isinstance(kind,str) or not re.fullmatch('[a-z][a-z0-9._-]{0,39}',kind) or
                reference.get('trust')!='untrusted'):
                return None
            try:
                parsed=urlsplit(url)
                _=parsed.port
                stamp=datetime.fromisoformat(retrieved.replace('Z','+00:00'))
                if (parsed.scheme not in {'http','https'} or not parsed.hostname or
                    parsed.username is not None or parsed.password is not None or
                    stamp.tzinfo is None or stamp.utcoffset() is None):
                    return None
            except (ValueError,TypeError):
                return None
            cleaned.append({key:reference[key] for key in
                ('source_id','url','sha256','retrieved_at','kind','trust')})
        return cleaned

    @classmethod
    def _with_source_references(cls,data,receipt):
        if not isinstance(data,dict):
            return data
        data=dict(data)
        # Always discard source metadata asserted in webpage/model result data.
        data.pop('_source_references',None)
        references=cls._source_references(receipt)
        # Structured model output retains its strict JSON-result wrapper. Source
        # provenance is not an extra interpreted action field or money evidence.
        if references is not None and 'json' not in data:
            data['_source_references']=references
        return data

    @staticmethod
    def outcome(result):
        if not isinstance(result,dict):return Outcome('unknown',reason='tools did not return an outcome object')
        if result.get('state') in {'succeeded','failed','unknown'}:
            return Outcome(result['state'],ToolsClient._with_source_references(result.get('data',{}),result.get('receipt')),result.get('reason',''))
        if result.get('kind')=='receipt':
            receipt=result.get('receipt',{})
            if not isinstance(receipt,dict):return Outcome('unknown',reason='tools did not return a receipt object')
            data=receipt.get('result',{})
            # Native gateway receipts wrap adapter output; old/non-research
            # envelopes retain their existing shape when no source proof exists.
            if (isinstance(data,dict) and isinstance(data.get('data'),dict)
                    and (ToolsClient._source_references(receipt) is not None or 'json' in data['data'])):
                data=data['data']
            state={'succeeded':'succeeded','failed':'failed','cancelled':'failed','unavailable':'failed'}.get(receipt.get('status'),'unknown')
            return Outcome(state,ToolsClient._with_source_references(data,receipt),receipt.get('reason',''))
        # Authenticated tools IPC may report safe continuation metadata without
        # claiming completion. Keep it alongside transport-proven source data.
        return Outcome('unknown',ToolsClient._with_source_references(result.get('data',{}),None),result.get('reason','tools did not return a confirmed outcome'))

class EconomyClient:
    WORK_LIMITS = {'max_duration_ms':120000,'max_storage_bytes':1000000}
    MAX_WORK_MONEY = 9_000_000_000_000_000

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


    @staticmethod
    def _work_identifier(value):
        if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,199}',value):
            raise ValueError('invalid work authorization identity')
        return value

    @classmethod
    def _work_amount(cls,value):
        if type(value) is not int or not 0 <= value <= cls.MAX_WORK_MONEY:
            raise ValueError('work costs require exact bounded integer micro-USD')
        return value

    def _work_row(self,row,*,run_id,tool_action_id,tool,maximum_micro_usd):
        """Bind a read or recovered reservation to the original work request."""
        if (not isinstance(row,dict) or
            (row.get('run_id'),row.get('action_id'),row.get('provider'),row.get('account')) !=
            (run_id,tool_action_id,tool,self.account) or
            type(row.get('amount_micro_usd')) is not int or
            row['amount_micro_usd'] != maximum_micro_usd or row.get('limits') != self.WORK_LIMITS or
            row.get('state') not in {'reserved','running','unknown','settled','cancelled'}):
            raise Unavailable('work reservation identity or limits mismatch')
        try:
            self._work_identifier(row.get('reservation_id'))
        except ValueError:
            raise Unavailable('work reservation identity unavailable') from None
        return row

    def reserve_work(self,*,run_id,job_id,action_id,tool_action_id,tool,maximum_micro_usd):
        """Reserve through the existing ledger without requiring a fictitious order.

        This creates no dispatch or charge. The existing tool gateway alone starts
        the reservation and posts usage. A lost response remains uncertain until
        the original reservation can be observed by action identity.
        """
        try:
            for value in (run_id,job_id,action_id,tool_action_id,tool):
                self._work_identifier(value)
            self._work_amount(maximum_micro_usd)
        except ValueError:
            return Outcome('failed',reason='invalid work authorization request')
        try:
            self.check_mode(run_id)
            row=self.http.request(f'/v1/runs/{quote(run_id,safe="")}/reservations',{
                'action_id':tool_action_id,'amount_micro_usd':maximum_micro_usd,
                'idempotency_key':action_id,'provider':tool,'account':self.account,
                'limits':dict(self.WORK_LIMITS)})
            self._work_row(row,run_id=run_id,tool_action_id=tool_action_id,tool=tool,
                           maximum_micro_usd=maximum_micro_usd)
            return Outcome('succeeded',{'reservation_id':row['reservation_id']})
        except urllib.error.HTTPError as exc:
            if exc.code not in {400,401,402,403,404,409,422}:
                return Outcome('unknown',reason='economy authorization unavailable; reconcile original action')
            # Never expose service bodies or submitted values in diagnostics.
            # Preserve only a recognized financial denial classification.
            denial='authorization_refused'
            try:
                body=json.loads(exc.read(65537))
                if body.get('error',{}).get('code')=='insufficient_funds':
                    denial='insufficient_funds'
            except (ValueError,AttributeError,TypeError):
                pass
            return Outcome('failed',{'denial':denial},reason=(
                'insufficient funds for work authorization' if denial=='insufficient_funds'
                else 'economy refused work authorization'))
        except Exception:
            return Outcome('unknown',reason='work authorization outcome unavailable; reconcile original action')

    def work_cost(self,*,run_id,reservation_id):
        """Read native work/family cost from economy, never a tool assertion.

        A zero-cost research parent is only orchestration. Its authenticated
        children listing supplies membership; every member must be closed before
        discovery can cache a final cost for that experiment.
        """
        self._work_identifier(run_id)
        self._work_identifier(reservation_id)
        self.check_mode(run_id)
        # Effects are the business-owned immutable request binding. No amount in
        # this journal is treated as actual cost; only the ledger can supply it.
        bindings=self.store.all("""SELECT request FROM effects WHERE run_id=? AND
            provider='economy' AND action='reserve_work' AND
            json_extract(result,'$.reservation_id')=? LIMIT 2""",(run_id,reservation_id))
        if len(bindings)!=1:
            raise Unavailable('original work authorization binding unavailable')
        request=json.loads(bindings[0]['request'])
        for key in ('tool_action_id','tool'):
            self._work_identifier(request.get(key))
        self._work_amount(request.get('maximum_micro_usd'))
        row=self._work_row(self.http.request(f'/v1/reservations/{quote(reservation_id,safe="")}'),
            run_id=run_id,tool_action_id=request['tool_action_id'],tool=request['tool'],
            maximum_micro_usd=request['maximum_micro_usd'])
        if row['reservation_id']!=reservation_id:
            raise Unavailable('work reservation identity mismatch')

        def usage(member):
            provisional=self._work_amount(member.get('provisional_micro_usd'))
            if member['state']=='settled':
                return self._work_amount(member.get('actual_micro_usd')),True,provisional
            if member['state']=='cancelled':
                # Cancellation is permitted only before dispatch, with no bill.
                if member.get('actual_micro_usd') is not None or provisional!=0:
                    raise Unavailable('cancelled reservation has conflicting usage')
                return 0,True,0
            if member.get('actual_micro_usd') is not None:
                raise Unavailable('open reservation has conflicting final usage')
            return None,False,provisional

        actual,final,provisional=usage(row)
        state=row['state']
        if request['tool']=='research.collect':
            if provisional!=0 or actual not in (None,0):
                raise Unavailable('research orchestration has unexpected direct usage')
            children=self.http.request(f'/v1/reservations/{quote(reservation_id,safe="")}/children')
            if not isinstance(children,list) or len(children)>64:
                raise Unavailable('research child membership unavailable or exceeds bound')
            child_ids=set()
            action_ids={row['action_id']}
            allocation=0
            known=actual or provisional
            allowed={'ai.generate','research.fetch','browser.read','research.search','files'}
            limit_names={'max_tokens','max_output_tokens','max_duration_ms','max_storage_bytes','max_units'}
            for child in children:
                if (not isinstance(child,dict) or child.get('run_id')!=run_id or
                    child.get('account')!=self.account or child.get('provider') not in allowed or
                    child.get('state') not in {'reserved','running','unknown','settled','cancelled'}):
                    raise Unavailable('research child binding mismatch')
                child_id=self._work_identifier(child.get('reservation_id'))
                action_id=self._work_identifier(child.get('action_id'))
                if child_id==reservation_id or child_id in child_ids or action_id in action_ids:
                    raise Unavailable('research child identity is duplicated')
                child_ids.add(child_id)
                action_ids.add(action_id)
                allocation+=self._work_amount(child.get('amount_micro_usd'))
                if allocation>row['amount_micro_usd']:
                    raise Unavailable('research child allocations exceed original job cap')
                limits=child.get('limits')
                if not isinstance(limits,dict) or not limits or set(limits)-limit_names:
                    raise Unavailable('research child limits unavailable')
                for key,value in limits.items():
                    if self._work_amount(value)==0 or key in row['limits'] and value>row['limits'][key]:
                        raise Unavailable('research child limits exceed parent authorization')
                child_actual,child_final,child_provisional=usage(child)
                known+=child_actual if child_final else child_provisional
                final=final and child_final
            if children and row['state']=='cancelled':
                raise Unavailable('cancelled research parent has allocated children')
            known=self._work_amount(known)
            actual=known if final else None
            # Include already-settled children in the known family subtotal when
            # another child's final bill remains unresolved.
            provisional=0 if final else known
            if not final and state in {'settled','cancelled'}:
                state='unknown'
        return {'actual_micro_usd':actual,'costs_final':final,'state':state,
                'provisional_micro_usd':provisional,'reservation_id':reservation_id}
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

            if action=='reserve_work':
                self.check_mode(run)
                request=json.loads(effect['request'])
                for key in ('tool_action_id','tool'):
                    self._work_identifier(request.get(key))
                self._work_amount(request.get('maximum_micro_usd'))
                found=[r for r in self.http.request(f'/v1/runs/{quote(run,safe="")}/reservations')
                       if r.get('action_id')==request['tool_action_id']]
                if len(found)==1:
                    row=self._work_row(found[0],run_id=run,tool_action_id=request['tool_action_id'],
                                       tool=request['tool'],maximum_micro_usd=request['maximum_micro_usd'])
                    return Outcome('succeeded',{'reservation_id':row['reservation_id']})
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
