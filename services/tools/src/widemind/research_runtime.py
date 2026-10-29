"""PayPal service-family research orchestration; no wallet or Stripe protocol.

The economy owns the parent budget and each child reservation. This journal
stores progress/leases only. Recovery reads old receipts before any new stage.
"""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
import time
import uuid

from .tools.contracts import ToolError, ToolResult, Uncertain, Usage


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(",",":"),allow_nan=False).encode()).hexdigest()


class ResearchLease:
    def __init__(self,journal,action_id,duration):
        self.journal,self.action_id=journal,action_id
        self.token=str(uuid.uuid4())
        with journal.connection() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS research_execution_leases(
                action_id TEXT PRIMARY KEY,token TEXT NOT NULL,expires REAL NOT NULL)''')
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM research_execution_leases WHERE action_id=?',(action_id,)).fetchone()
            if row and row['expires']>time.time():
                raise ToolError('research_busy','Another worker still holds this research execution lease.')
            db.execute('INSERT INTO research_execution_leases VALUES(?,?,?) ON CONFLICT(action_id) DO UPDATE SET token=excluded.token,expires=excluded.expires',
                       (action_id,self.token,time.time()+duration+15))

    def check(self):
        with self.journal.connection() as db:
            row=db.execute('SELECT token,expires FROM research_execution_leases WHERE action_id=?',(self.action_id,)).fetchone()
        if not row or row['token']!=self.token or row['expires']<time.time():
            raise ToolError('authorization','Research execution lease expired or changed owner.')

    def close(self):
        with self.journal.connection() as db:
            db.execute('DELETE FROM research_execution_leases WHERE action_id=? AND token=?',(self.action_id,self.token))

    def save(self,**fields):
        """Commit accepted parent status/result under the same lease lock.

        A terminal receipt becomes immutable to future parent workers before
        settlement. Repeated settlement can therefore only use that receipt.
        """
        if not fields or not set(fields)<={'status','result','started'}:raise ValueError('Research lease field')
        with self.journal.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT token,expires FROM research_execution_leases WHERE action_id=?',(self.action_id,)).fetchone()
            if not row or row['token']!=self.token or row['expires']<time.time():return False
            accepted=db.execute('SELECT result FROM operations WHERE id=?',(self.action_id,)).fetchone()
            if accepted['result'] and json.loads(accepted['result']).get('kind')=='receipt':
                # Settlement/network failure must never erase an accepted
                # terminal result, even under the same current lease token.
                return fields.get('result')==accepted['result']
            db.execute('UPDATE operations SET '+','.join(key+'=?' for key in fields)+',updated=? WHERE id=?',
                       (*fields.values(),time.time(),self.action_id))
            return True


class ChildExecutor:
    def __init__(self,application,parent,reservation,ctx):
        self.app,self.parent,self.reservation,self.ctx=application,parent,reservation,ctx

    def records(self):
        return self.ctx.load_state().get('research_children',{})

    def _save(self,key,value):
        # Parent execution is fenced; child identities are immutable once saved.
        records=self.records();records[key]=value
        self.ctx.save_state({'research_children':records})

    def execute(self,key,tool,arguments):
        self.ctx.check()
        if tool not in {'ai.generate','research.fetch','research.search','browser.read','files'}:
            raise ToolError('authorization','Research cannot dispatch this child capability.')
        records=self.records();record=records.get(key)
        fingerprint=digest({'tool':tool,'arguments':arguments})
        if record is not None and record['fingerprint']!=fingerprint:
            raise ToolError('conflict','A research stage changed its durable child inputs.')
        if record is None:
            adapter=self.app.adapters.get(tool)
            settings=dict(self.app.config.get('tools',{}).get(tool,{}))
            if adapter is None or settings.get('enabled') is not True:
                raise ToolError('unavailable','Enable the required research child tool in owner configuration: '+tool)
            health=adapter.availability(settings,self.app.secrets)
            if not health.get('available'):
                raise ToolError('unavailable','Complete owner setup for the required research child: '+tool)
            quote=adapter.quote(arguments,settings)
            maximum=quote.maximum_microusd
            if type(maximum) is not int or maximum<0 or quote.paid and maximum==0:
                raise ToolError('budget_limit','Child tool did not supply an enforceable quote.')
            child_id=str(uuid.uuid5(uuid.NAMESPACE_URL,'widemind.paypal.research:'+self.parent['actionId']+':'+key))
            milliseconds=max(1,min(self.parent['limit']['maxDurationMs'],int(self.ctx.remaining(300)*1000)))
            limits={'max_duration_ms':milliseconds,'max_storage_bytes':self.parent['limit']['maxOutputBytes']}
            if 'max_output_tokens' in quote.limits:
                limits['max_tokens']=int(quote.limits['max_output_tokens'])
            elif self.parent['limit'].get('maxTokens'):
                limits['max_tokens']=self.parent['limit']['maxTokens']
            record={'key':key,'action_id':child_id,'tool':tool,'fingerprint':fingerprint,
                    'maximum_micro_usd':maximum,'limits':limits,'status':'planned'}
            self._save(key,record)
        if record.get('reservation_id') is None:
            try:
                row=self.app.economy.reserve_child(self.reservation['reservationId'],
                    action_id=record['action_id'],amount_micro_usd=record['maximum_micro_usd'],
                    idempotency_key='research:'+record['action_id'],provider=tool,account=self.app.account,limits=record['limits'])
            except ToolError:
                raise
            except Exception as exc:
                raise Uncertain('Child budget authorization is unavailable; no provider replay was attempted.') from exc
            record={**record,'reservation_id':row['reservation_id'],'status':'authorized'}
            self._save(key,record)
        limit={'maximum':{'currency':'USD','micros':str(record['maximum_micro_usd'])},
               'maxDurationMs':record['limits']['max_duration_ms'],'maxOutputBytes':record['limits']['max_storage_bytes']}
        if record['limits'].get('max_tokens') is not None:limit['maxTokens']=record['limits']['max_tokens']
        action={'version':1,'actionId':record['action_id'],'runId':self.parent['runId'],
                'jobId':self.parent.get('jobId'),'workKey':record['action_id'],'tool':tool,'input':arguments,'limit':limit}
        reservation={'reservationId':record['reservation_id'],'runId':action['runId'],
                     'actionId':action['actionId'],'limit':limit,'authorization':'authoritative-economy-verified'}
        # Preserve the exact request before invoking the native gateway. Its own
        # journal persists provider intent/result and refuses a second dispatch.
        record={**record,'action':action,'reservation':reservation}
        self._save(key,record)
        outcome=self.app.execute({'action':action,'reservation':reservation},_parent_ctx=self.ctx)
        return self._consume(key,record,outcome)

    def _consume(self,key,record,outcome):
        if outcome.get('kind')!='receipt':
            self._save(key,{**record,'status':'unknown'})
            self.app.economy.mark_unknown(record['reservation_id'],record['action_id'])
            raise Uncertain('Research child outcome or usage is uncertain; its own reservation remains held.')
        receipt=outcome['receipt']
        self.app.economy.settle(record['reservation_id'],receipt)
        charge=int(receipt['usage']['actualCharge']['micros'])
        self._save(key,{**record,'status':receipt['status'],'usage':receipt['usage'],
                       'native_usage':receipt['result'].get('nativeUsage',{}),'receipt_ref':'tools-journal:'+record['action_id']})
        if charge>record['maximum_micro_usd']:
            raise ToolError('job_budget_exceeded','A child incurred over-limit liability; recorded charges require operator review.')
        if receipt['status']!='succeeded':
            raise ToolError('research_child_failed','A separately authorized research child failed; its usage was retained.')
        payload=receipt['result'];native=payload.get('nativeUsage',{})
        return ToolResult(payload['data'],Usage(**native) if native else Usage(),
                          artifacts=payload.get('artifacts',[]),references=payload.get('references',[]))

    def reconcile_pending(self):
        """Observational only. Never reserve, start, or execute a new child."""
        for key,record in self.records().items():
            if record.get('status') in {'succeeded','failed'}:continue
            if not record.get('reservation_id') or not record.get('action'):
                # Admission interrupted before any child gateway execution.
                # execute() can replay that same reservation request safely.
                continue
            outcome=self.app.reconcile({'action':record['action'],'reservation':record['reservation']},_parent_ctx=self.ctx)
            if outcome.get('kind')=='not_performed':
                # Authoritative gateway proof permits the original child ID;
                # this method itself does not execute it.
                self._save(key,{**record,'status':'authorized'})
                continue
            if outcome.get('kind')!='receipt':return False
            try:self._consume(key,record,outcome)
            except ToolError as exc:
                if isinstance(exc,Uncertain):return False
                # A confirmed failed child is terminal and will be reported by
                # the resumed workflow without repeating its provider request.
        return True

    def summary(self):
        records=self.records()
        return [{'stage':key,'action_id':r['action_id'],'tool':r['tool'],'reservation_id':r.get('reservation_id'),
                 'status':r['status'],'maximum_micro_usd':r['maximum_micro_usd'],
                 'usage':r.get('usage'),'receipt_ref':r.get('receipt_ref')} for key,r in records.items()]


class WorkflowState:
    """Fence parent progress writes without suppressing native child receipts."""
    def __init__(self,journal,lease):self.journal,self.lease=journal,lease

    def _bound(self,action_id):
        if action_id!=self.lease.action_id:raise ToolError('authorization','Workflow state is bound to its parent action.')

    def load(self,action_id):
        self._bound(action_id);self.lease.check()
        return self.journal.load(action_id)

    def update(self,action_id,patch):
        self._bound(action_id)
        with self.journal.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            lease=db.execute('SELECT token,expires FROM research_execution_leases WHERE action_id=?',(action_id,)).fetchone()
            if not lease or lease['token']!=self.lease.token or lease['expires']<time.time():
                raise ToolError('authorization','A superseded workflow cannot overwrite research progress.')
            row=db.execute('SELECT state FROM operations WHERE id=?',(action_id,)).fetchone()
            value=json.loads(row['state']);value.update(patch)
            payload=json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)
            if len(payload.encode())>32_000_000:raise ToolError('output_limit','Private workflow state exceeded its bound.')
            db.execute('UPDATE operations SET state=?,updated=? WHERE id=?',(payload,time.time(),action_id))

    def dispatched(self,action_id,intent):self.update(action_id,{'external_intent':dict(intent)})
