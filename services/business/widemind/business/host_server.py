"""Authenticated loopback bridge driven by the Node persistent worker, not a second worker."""
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from .engine import Business, Deferred
from .store import Store, encode, identity
from .policy import Policy
from .services import ResearchBrief
from .host_services import LOCAL_SERVICES
from .host_clients import EconomyClient, ToolsClient, RuntimeState

class PortalBusiness(Business):
    """Actual private portal publication, never falsely described as Gmail delivery."""
    def _email(self,entity,step,customer_id,subject,body,*,artifact=None):
        if not self._active() or self._opted_out(customer_id): raise Deferred('portal publication disabled or opted out')
        identifier=identity(self.run_id,entity,step,'portal')
        value=(self.run_id,customer_id,entity,step,subject,body,encode(artifact) if artifact else None)
        with self.store.transaction():
            old=self.store.one('SELECT * FROM host_notifications WHERE id=?',(identifier,))
            if old:
                if tuple(old[k] for k in ('run_id','customer_id','entity_id','purpose','subject','body','artifact'))!=value:raise ValueError('portal publication identity conflict')
            else:self.store.db.execute('INSERT INTO host_notifications VALUES (?,?,?,?,?,?,?,?,?)',(identifier,*value,self.clock()))
        return identifier

class Host:
    def __init__(self,env=None):
        self.env=dict(os.environ if env is None else env)
        self.path=self.env.get('WIDEMIND_BUSINESS_DB','./var/business.sqlite3')
        self.token=self.env.get('WIDEMIND_BUSINESS_TOKEN','')
        self.mode=self.env.get('WIDEMIND_BUSINESS_MODE','test')
        if len(self.token)<32 or not self.token.isascii() or any(c.isspace() for c in self.token):raise ValueError('dedicated business bearer token >=32 characters required')
        if self.mode not in {'test','live'}:raise ValueError('business mode must be test or live')
        self.runtime=RuntimeState(self.env.get('WIDEMIND_RUNTIME_DATABASE',''),self.mode)
    def business(self,run):
        if not isinstance(run,str) or not re.fullmatch(r'[A-Za-z0-9._:-]{1,200}',run):raise ValueError('invalid run')
        state=self.runtime.state(run)
        if not self.runtime.exists(run):raise ValueError('runtime run is unavailable')
        store=Store(self.path)
        if not store.one('SELECT document FROM settings WHERE run_id=?',(run,)):
            policy=Policy(capabilities=('research','graphics','software'),prices={'research-brief':2000000,'svg-graphic':2000000,'software-starter':3000000},goal_weights={'research-brief':1,'svg-graphic':1,'software-starter':1})
            store.db.execute('INSERT OR IGNORE INTO settings VALUES (?,?)',(run,encode(policy.document())))
        return PortalBusiness(store,run_id=run,economy=EconomyClient(self.env,store,self.mode),tools=ToolsClient(self.env,self.mode),runtime=self.runtime,mode=self.mode,services=[ResearchBrief(),*LOCAL_SERVICES])
    def token_request(self,business,token):
        if not isinstance(token,str) or not re.fullmatch(r'[A-Za-z0-9_-]{32,256}',token):raise ValueError('invalid customer authorization')
        hashed=hashlib.sha256(token.encode()).hexdigest()
        row=business.store.one('SELECT * FROM host_requests WHERE token_hash=? AND run_id=? AND revoked_at IS NULL',(hashed,business.run_id))
        if not row:raise ValueError('invalid customer authorization')
        return row
    def work(self,business):
        row=business.store.one("SELECT id,CASE WHEN state='running' AND lease_until>due_at THEN lease_until ELSE due_at END AS due_at,attempts FROM events WHERE run_id=? AND state IN ('queued','running') ORDER BY due_at,id LIMIT 1",(business.run_id,))
        return {'eventId':row['id'] if row else None,'attempt':row['attempts'] if row else 0,'dueAt':row['due_at'] if row else None,'runtimeState':self.runtime.state(business.run_id)}
    def handle(self,path,payload):
        run=payload.get('runId');business=self.business(run)
        try:
            if path=='/v1/work-state':return self.work(business)
            if path=='/v1/offerings':
                return {'available':True,'offerings':[{'id':s.name,'title':s.name.replace('-',' ').title(),'description':'Scoped work with private delivery and verified payment before fulfillment.','priceMicros':str(business.policy.prices.get(s.name,0)),'capability':s.capability,'available':s.capability in business.policy.capabilities and business.tools.available({'research':'research.collect','graphics':'graphics.create','software':'software.create'}[s.capability]),'reason':'Requires configured collection tools and funded runtime'} for s in business.services.values()]}
            if path=='/v1/status':return {**business.diagnostics(),'policy':business.policy.document(),'notifications':business.store.all('SELECT id,entity_id,purpose,created_at FROM host_notifications WHERE run_id=?',(run,))}
            if path=='/v1/policy':business.configure(Policy(**payload['policy']));return {'saved':True}
            if path=='/v1/opportunities':
                p=payload['opportunity'];op=business.receive(source=p['source'],external_id=p['externalId'],service=p['service'],scope=p['scope'],customer_email=p.get('email'),origin=p.get('origin','inbound'),source_reference=p.get('sourceReference',''),priority=p.get('priority',1));return {'opportunityId':op}
            if path=='/v1/notify':return {'eventId':business.notify(**payload['event'])}
            if path=='/v1/request':
                request_id=payload['requestId'];token=payload.get('customerToken') or secrets.token_urlsafe(32)
                if not isinstance(request_id,str) or not re.fullmatch(r'[A-Za-z0-9._:-]{1,200}',request_id):raise ValueError('invalid request id')
                if not re.fullmatch(r'[A-Za-z0-9_-]{32,256}',token):raise ValueError('invalid customer token')
                hashed=hashlib.sha256(token.encode()).hexdigest()
                email=payload['email']
                if not isinstance(email,str) or len(email)>254 or not re.fullmatch(r'[^\s<>@,;]+@[^\s<>@,;]+\.[^\s<>@,;]+',email):raise ValueError('invalid contact address')
                # Claim credentials atomically before receive; retries with the same
                # token recover an interruption without returning unusable secrets.
                key=identity(run,request_id);op=identity(run,'customer-portal',request_id)
                with business.store.transaction():
                    previous=business.store.one('SELECT * FROM host_requests WHERE request_id=?',(key,))
                    used=business.store.one('SELECT request_id FROM host_requests WHERE token_hash=?',(hashed,))
                    if previous and (previous['revoked_at'] or previous['token_hash']!=hashed or previous['email']!=email.lower()):return {'error':'request already exists; use original private access','statusCode':409}
                    if used and used['request_id']!=key:return {'error':'use a new token for each request','statusCode':409}
                    business.store.db.execute('INSERT OR IGNORE INTO host_requests VALUES (?,?,?,?,?,?,NULL)',(key,run,op,email.lower(),hashed,time.time()))
                actual=business.receive(source='customer-portal',external_id=request_id,service=payload['service'],scope=payload['scope'],customer_email=email)
                if actual!=op:raise ValueError('opportunity identity mismatch')
                return {'requestId':request_id,'opportunityId':op,'customerToken':token,'runId':run}
            if path in {'/v1/customer','/v1/accept','/v1/delivery','/v1/revoke'}:
                request=self.token_request(business,payload.get('token'))
                quotes=business.store.all('SELECT * FROM quotes WHERE opportunity_id=?',(request['opportunity_id'],))
                quote_ids=[q['id'] for q in quotes]
                if path=='/v1/revoke':business.store.db.execute('UPDATE host_requests SET revoked_at=? WHERE request_id=?',(time.time(),request['request_id']));return {'revoked':True}
                if path=='/v1/accept':
                    if payload['quoteId'] not in quote_ids:raise ValueError('unknown quote')
                    return {'orderId':business.accept(payload['quoteId'],customer_email=request['email'],evidence_reference='customer-portal:'+identity(payload['token']))}
                orders=business.store.all('SELECT * FROM orders WHERE quote_id IN (SELECT id FROM quotes WHERE opportunity_id=?)',(request['opportunity_id'],))
                if path=='/v1/delivery':
                    matches=[o for o in orders if o['id']==payload['orderId']]
                    if len(matches)!=1:raise ValueError('unknown order')
                    published=business.store.one("SELECT artifact FROM host_notifications WHERE run_id=? AND entity_id=? AND purpose='delivery' AND artifact IS NOT NULL",(run,payload['orderId']))
                    if not published:raise ValueError('delivery is not available')
                    artifact=json.loads(published['artifact']);return {'filename':artifact['filename'],'mediaType':artifact['media_type'],'content':artifact['content']}
                op=business.store.one('SELECT status,customer_id FROM opportunities WHERE id=?',(request['opportunity_id'],))
                notices=business.store.all('SELECT id,purpose,subject,body,created_at,entity_id FROM host_notifications WHERE run_id=? AND customer_id=?',(run,op['customer_id']))
                # A customer with several independent requests sees only this request's entities.
                allowed={request['opportunity_id'],*[o['id'] for o in orders]};notices=[n for n in notices if n['entity_id'] in allowed]
                def order_public(o):
                    checkout=None
                    for n in notices:
                        if n['entity_id']==o['id'] and n['purpose']=='payment_request':checkout=json.loads(n['body']).get('payment_url')
                    return {'orderId':o['id'],'status':o['status'],'dueAt':o['delivery_due_at'],'checkoutUrl':checkout,'deliveryAvailable':bool(business.store.one("SELECT id FROM host_notifications WHERE entity_id=? AND purpose='delivery'",(o['id'],)))}
                return {'runId':run,'opportunityId':request['opportunity_id'],'status':op['status'],'mode':self.mode,'quotes':[{'quoteId':q['id'],'service':business.store.one('SELECT service FROM opportunities WHERE id=?',(request['opportunity_id'],))['service'],'scope':json.loads(q['scope']),'priceMicros':str(q['price_micro_usd']),'expiresAt':q['expires_at'],'status':q['status']} for q in quotes],'orders':[order_public(o) for o in orders],'notifications':[{'id':n['id'],'purpose':n['purpose'],'subject':n['subject'],'body':n['body'],'at':n['created_at']} for n in notices]}
            if path=='/v1/step':
                action=payload['actionId'];event=payload['eventId']
                with business.store.transaction():
                    prior=business.store.one('SELECT * FROM host_invocations WHERE action_id=?',(action,))
                    if prior:
                        if (prior['run_id'],prior['event_id'])!=(run,event):raise ValueError('step identity conflict')
                        return json.loads(prior['result']) if prior['state']=='completed' else {'kind':'unknown','reason':'interrupted business step requires core-effect reconciliation'}
                    expected=self.work(business)
                    if expected['eventId']!=event or expected['dueAt']>time.time():raise ValueError('business event is not due')
                    business.store.db.execute("INSERT INTO host_invocations VALUES (?,?,?,'running',NULL,?)",(action,run,event,time.time()))
                processed=business.tick();event_state=business.store.one('SELECT state FROM events WHERE id=? AND run_id=?',(event,run));result={'kind':'completed','processed':processed,'eventState':event_state['state'] if event_state else 'unknown',**self.work(business)}
                business.store.db.execute("UPDATE host_invocations SET state='completed',result=? WHERE action_id=?",(encode(result),action));return result
            if path=='/v1/reconcile':
                row=business.store.one('SELECT * FROM host_invocations WHERE action_id=? AND run_id=?',(payload['actionId'],run))
                if row and row['state']=='completed':return json.loads(row['result'])
                if row:
                    observed=business.store.one('SELECT state FROM events WHERE id=? AND run_id=?',(row['event_id'],run))
                    if observed and observed['state'] in {'done','blocked','failed'}:
                        # The core already committed its exact event outcome. This
                        # observes orchestration completion, not business/job success.
                        result={'kind':'completed','processed':True,'eventState':observed['state'],**self.work(business)}
                        business.store.db.execute("UPDATE host_invocations SET state='completed',result=? WHERE action_id=?",(encode(result),row['action_id']))
                        return result
                return {'kind':'unknown','reason':'business step outcome requires reconciliation; no blind replay'}
            raise ValueError('unknown route')
        finally:business.store.close()

def main():
    os.umask(0o077);host=Host()
    import argparse
    parser=argparse.ArgumentParser(description='WideMind authenticated business bridge');parser.add_argument('--host',default=os.environ.get('WIDEMIND_BUSINESS_HOST','127.0.0.1'));parser.add_argument('--port',type=int,default=int(os.environ.get('WIDEMIND_BUSINESS_PORT','8082')));args=parser.parse_args();address,port=args.host,args.port
    if not 1<=port<=65535:raise ValueError('invalid business port')
    if address not in {'127.0.0.1','localhost','::1'}:raise ValueError('business bridge must bind loopback')
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*_args):pass
        def do_GET(self):
            if self.path!='/health':self.respond(404,{'error':'not found'});return
            self.respond(200,{'service':'widemind-business','status':'ok','mode':host.mode})
        def do_POST(self):
            if not hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+host.token):self.respond(401,{'error':'authentication required'});return
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=65536 or self.headers.get('Content-Type','').split(';')[0]!='application/json':raise ValueError('bounded JSON body required')
                data=json.loads(self.rfile.read(size));result=host.handle(self.path,data);self.respond(result.pop('statusCode',200),result)
            except (ValueError,KeyError,TypeError):self.respond(400,{'error':'invalid request, private authorization or unavailable transition'})
            except Exception:self.respond(503,{'error':'business dependency unavailable; inspect private setup'})
        def respond(self,status,data):
            encoded=encode(data).encode();self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(encoded)));self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(encoded)
    server=ThreadingHTTPServer((address,port),Handler)
    try:server.serve_forever()
    finally:server.server_close()
if __name__=='__main__':main()
