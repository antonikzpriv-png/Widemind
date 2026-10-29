"""PayPal Business Orders adapter. Provider reads, not approval redirects, post cash.

Uses dedicated OAuth credentials and a configured merchant. No requests occur
until an authenticated operation is invoked. Fixtures are never live receipts.
"""
from __future__ import annotations
import hashlib
import json
import re
import time
from datetime import datetime
from urllib.parse import urlsplit
import httpx
from .service import EconomyError, ident, money, now
from .stripe import _bounded_operation, _operation_budget


def _id(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',value):
        raise EconomyError('invalid_provider_record','Invalid PayPal object identity',502)
    return value


def _amount(value):
    if not isinstance(value,dict) or value.get('currency_code')!='USD' or not isinstance(value.get('value'),str) or not re.fullmatch(r'\d{1,12}\.\d{2}',value['value']):
        raise EconomyError('invalid_provider_record','Expected exact USD provider amount',502)
    whole,cents=value['value'].split('.')
    return money(int(whole)*1_000_000+int(cents)*10_000)


def _usd(value):
    money(value,positive=True)
    if value%10_000:raise EconomyError('invalid_amount','PayPal USD amounts require whole cents',422)
    return {'currency_code':'USD','value':f'{value//1_000_000}.{value%1_000_000//10_000:02d}'}


class PayPalPayments:
    provider='paypal'
    def __init__(self,service,*,client_id,client_secret,account,mode,webhook_id,success_url,cancel_url,client=None,clock=time.time,monotonic=time.monotonic,test_api_base=None,allow_test_endpoint=False):
        if mode not in ('test','live') or not client_id or not client_secret or not webhook_id:raise EconomyError('invalid_config','PayPal mode and dedicated OAuth/webhook credentials are required',422)
        self.service,self.account,self.mode=service,_id(account),mode
        self.client_id,self._secret,self.webhook_id=client_id,client_secret,webhook_id
        self.success_url,self.cancel_url=success_url,cancel_url
        self.client=client or httpx.Client();self._owns_client=client is None
        self.clock,self._monotonic=clock,monotonic
        self.base='https://api-m.paypal.com' if mode=='live' else 'https://api-m.sandbox.paypal.com'
        if test_api_base:
            url=urlsplit(test_api_base)
            if mode!='test' or not allow_test_endpoint or url.scheme not in ('http','https') or url.hostname not in ('localhost','127.0.0.1','::1') or url.username or url.password or url.path not in ('','/') or url.query or url.fragment:raise EconomyError('invalid_config','Custom PayPal endpoint requires explicit loopback test mode',422)
            self.base=test_api_base.rstrip('/')
        self._token=None;self._expires=0
    def close(self):
        if self._owns_client:self.client.close()
    def _http(self,method,path,**kwargs):
        budget=_operation_budget.get()
        if budget is None or budget.requests>=32 or self._monotonic()>=budget.deadline:raise EconomyError('provider_operation_budget','PayPal operation exceeded its bounded budget',503)
        budget.requests+=1
        try:
            with self.client.stream(method,self.base+path,timeout=min(20,max(0.01,budget.deadline-self._monotonic())),follow_redirects=False,**kwargs) as response:
                if not 200<=response.status_code<300:raise EconomyError('provider_unavailable' if response.status_code>=500 else 'provider_rejected','PayPal operation rejected or remains uncertain',503 if response.status_code>=500 else 502)
                data=bytearray()
                for chunk in response.iter_bytes():
                    if len(data)+len(chunk)>2_097_152 or self._monotonic()>=budget.deadline:raise EconomyError('provider_operation_budget','PayPal response exceeded its bounded budget',503)
                    data.extend(chunk)
            value=json.loads(data)
        except httpx.HTTPError:raise EconomyError('provider_unavailable','PayPal outcome is uncertain; reconcile before retry',503) from None
        except (ValueError,UnicodeDecodeError):raise EconomyError('invalid_provider_record','PayPal returned malformed JSON',502) from None
        if not isinstance(value,dict):raise EconomyError('invalid_provider_record','PayPal returned an invalid object',502)
        return value
    def _request(self,method,path,*,body=None,key=None):
        if not self._token or self.clock()>=self._expires:
            token=self._http('POST','/v1/oauth2/token',auth=httpx.BasicAuth(self.client_id,self._secret),data={'grant_type':'client_credentials'})
            if not isinstance(token.get('access_token'),str) or type(token.get('expires_in')) is not int or token['expires_in']<=0:raise EconomyError('invalid_provider_record','PayPal returned an invalid access token',502)
            self._token=token['access_token'];self._expires=self.clock()+max(1,token['expires_in']-60)
        headers={'Authorization':'Bearer '+self._token,'Content-Type':'application/json'}
        if key:headers['PayPal-Request-Id']=key
        return self._http(method,path,headers=headers,**({'json':body} if body is not None else {}))
    @_bounded_operation
    def verify_setup(self):
        # Token retrieval verifies configured OAuth credentials; merchant identity
        # is additionally checked against authenticated order payee records.
        self._request('GET','/v1/identity/oauth2/userinfo?schema=paypalv1.1')
        return {'provider':'paypal','account':self.account,'mode':self.mode,'oauth_verified':True,'merchant_verified_per_order':True}
    def _key(self,*values):return hashlib.sha256(json.dumps(['paypal',self.account,self.mode,*values],separators=(',',':')).encode()).hexdigest()[:38]
    def _order(self,payment_id):
        order=self._request('GET',f'/v2/checkout/orders/{_id(payment_id)}')
        if order.get('id')!=payment_id or order.get('intent')!='CAPTURE':raise EconomyError('invalid_provider_record','PayPal order identity or intent mismatch',502)
        units=order.get('purchase_units')
        if not isinstance(units,list) or len(units)!=1 or not isinstance(units[0],dict) or units[0].get('payee',{}).get('merchant_id')!=self.account:raise EconomyError('provider_account_mismatch','PayPal order payee differs from configured merchant',502)
        return order,units[0]
    @_bounded_operation
    def create_checkout(self,run_id,request_id,job_id,amount_micro_usd,description='WideMind order'):
        amount=_usd(amount_micro_usd)
        claim=self.service.record_checkout(run_id,'paypal',self.account,self.mode,request_id,job_id,amount_micro_usd,{'description':description,'success_url':self.success_url,'cancel_url':self.cancel_url})
        if claim.get('payment_id'):return claim
        if self.clock()-datetime.fromisoformat(claim['created_at'].replace('Z','+00:00')).timestamp()>5*3600:raise EconomyError('checkout_reconciliation_required','Uncertain PayPal creation exceeded safe retry window',409)
        key=self._key(run_id,request_id)
        order=self._request('POST','/v2/checkout/orders',key=key,body={'intent':'CAPTURE','purchase_units':[{'reference_id':key,'custom_id':key,'payee':{'merchant_id':self.account},'description':description,'amount':amount}],'payment_source':{'paypal':{'experience_context':{'return_url':self.success_url,'cancel_url':self.cancel_url,'user_action':'PAY_NOW'}}}})
        payment_id=_id(order.get('id'));links=order.get('links',[]);url=next((item.get('href') for item in links if item.get('rel') in ('approve','payer-action')),None)
        parsed=urlsplit(url or '')
        allowed='www.paypal.com' if self.mode=='live' else 'www.sandbox.paypal.com'
        if parsed.scheme!='https' or parsed.hostname!=allowed or parsed.username or parsed.password:raise EconomyError('invalid_provider_record','PayPal approval URL is invalid',502)
        # Approval alone creates no receipt or earnings.
        return self.service.finish_checkout('paypal',self.account,self.mode,request_id,payment_id,url)
    @_bounded_operation
    def capture(self,payment_id,request_id):
        order,_unit=self._order(payment_id)
        if order.get('status')!='COMPLETED':
            if order.get('status')!='APPROVED':raise EconomyError('payment_not_approved','PayPal order must be approved before capture',409)
            self._request('POST',f'/v2/checkout/orders/{_id(payment_id)}/capture',body={},key=self._key('capture',payment_id,ident(request_id)))
        return self.reconcile(payment_id)
    @_bounded_operation
    def reconcile(self,payment_id):
        order,unit=self._order(payment_id)
        payment=self.service.payment('paypal',self.account,self.mode,payment_id)
        transactions=[];pending=0;refunded=0;gross_total=0
        captures=unit.get('payments',{}).get('captures',[])
        if not isinstance(captures,list):raise EconomyError('invalid_provider_record','Malformed PayPal capture list',502)
        for captured in captures:
            capture_id=_id(captured.get('id'));capture=self._request('GET',f'/v2/payments/captures/{capture_id}')
            if capture.get('id')!=capture_id:raise EconomyError('invalid_provider_record','PayPal capture mismatch',502)
            related=capture.get('supplementary_data',{}).get('related_ids',{}).get('order_id')
            if related!=payment_id:raise EconomyError('invalid_provider_record','PayPal capture belongs to another order',502)
            detail=capture.get('seller_receivable_breakdown',{});gross=_amount(detail.get('gross_amount',capture.get('amount')));fee=_amount(detail.get('paypal_fee',{'currency_code':'USD','value':'0.00'}));net=_amount(detail.get('net_amount',{'currency_code':'USD','value':f'{(gross-fee)//1_000_000}.{(gross-fee)%1_000_000//10000:02d}'}))
            if gross-fee!=net:raise EconomyError('invalid_provider_record','PayPal charge does not reconcile',502)
            settled=capture.get('status') in ('COMPLETED','PARTIALLY_REFUNDED','REFUNDED');gross_total+=gross if settled else 0
            transactions.append({'transaction_id':capture_id,'kind':'receipt','amount_micro_usd':gross,'fee_micro_usd':fee,'net_micro_usd':net,'currency':'USD','settled':settled,'native':{'provider':'paypal','status':capture.get('status'),'order_id':payment_id,'captured_at':capture.get('create_time')}})
        refunds=unit.get('payments',{}).get('refunds',[])
        for record in refunds:
            refund_id=_id(record.get('id'));refund=self._request('GET',f'/v2/payments/refunds/{refund_id}')
            if refund.get('id')!=refund_id:raise EconomyError('invalid_provider_record','PayPal refund identity mismatch',502)
            value=_amount(refund.get('amount'));status=refund.get('status')
            if status=='COMPLETED':refunded+=value
            if status not in ('CANCELLED','FAILED'):
                transactions.append({'transaction_id':refund_id,'kind':'refund','amount_micro_usd':-value,'fee_micro_usd':0,'net_micro_usd':-value,'currency':'USD','settled':status=='COMPLETED','native':{'provider':'paypal','status':status,'order_id':payment_id}})
        snapshot={'provider':'paypal','account':self.account,'mode':self.mode,'payment_id':payment_id,'run_id':payment['run_id'],'job_id':payment['job_id'],'status':'refunded' if gross_total and refunded>=gross_total else 'partially_refunded' if refunded else 'settled' if gross_total and order.get('status')=='COMPLETED' else 'pending','expected_revision':payment['revision'],'transactions':transactions,'pending_liability_micro_usd':pending}
        return self.service.apply_payment_snapshot(snapshot)
    @_bounded_operation
    def verify_webhook(self,raw_body,headers):
        try:event=json.loads(raw_body)
        except (ValueError,UnicodeDecodeError):raise EconomyError('invalid_signature','Invalid PayPal webhook body',400) from None
        if not isinstance(event,dict) or len(raw_body)>1_048_576:raise EconomyError('invalid_signature','Invalid PayPal webhook body',400)
        required=('paypal-auth-algo','paypal-cert-url','paypal-transmission-id','paypal-transmission-sig','paypal-transmission-time')
        if any(not headers.get(name) for name in required):raise EconomyError('invalid_signature','PayPal verification headers required',400)
        result=self._request('POST','/v1/notifications/verify-webhook-signature',body={'auth_algo':headers['paypal-auth-algo'],'cert_url':headers['paypal-cert-url'],'transmission_id':headers['paypal-transmission-id'],'transmission_sig':headers['paypal-transmission-sig'],'transmission_time':headers['paypal-transmission-time'],'webhook_id':self.webhook_id,'webhook_event':event})
        if result.get('verification_status')!='SUCCESS':raise EconomyError('invalid_signature','PayPal rejected webhook signature',400)
        normalized={'id':_id(event.get('id')),'type':ident(event.get('event_type')),'livemode':self.mode=='live','account':self.account,'data':{'object':event.get('resource',{})}}
        return normalized,hashlib.sha256(raw_body).hexdigest()
    @_bounded_operation
    def process_verified_event(self,event,digest):
        resource=event.get('data',{}).get('object',{});kind=event['type']
        order_id=resource.get('supplementary_data',{}).get('related_ids',{}).get('order_id')
        if kind.startswith('CHECKOUT.ORDER.'):order_id=resource.get('id')
        if not order_id:raise EconomyError('payment_mapping_unknown','PayPal event requires an order binding',409)
        if kind=='CHECKOUT.ORDER.APPROVED':self.capture(order_id,'event-'+event['id'])
        else:self.reconcile(order_id)
        return {'payment_id':order_id}
    @_bounded_operation
    def create_refund(self,payment_id,request_id,amount_micro_usd):
        amount=_usd(amount_micro_usd);payment=self.service.payment('paypal',self.account,self.mode,payment_id);order,unit=self._order(payment_id)
        captures=unit.get('payments',{}).get('captures',[])
        if len(captures)!=1:raise EconomyError('refund_reconciliation_required','Refund requires one unambiguous capture',409)
        capture_id=_id(captures[0].get('id'));key=self._key('refund',payment_id,ident(request_id))
        with self.service._tx() as db:
            claim=self.service._operation(db,payment['run_id'],'paypal.refund.request',request_id,[payment_id,amount_micro_usd],lambda:{'payment_id':payment_id,'amount_micro_usd':amount_micro_usd,'created_at':now()})
            self.service._hold(db,payment['run_id'],'refund-request:'+key,amount_micro_usd,'refund','PayPal refund pending provider reconciliation')
        if self.clock()-datetime.fromisoformat(claim['created_at'].replace('Z','+00:00')).timestamp()>5*3600:raise EconomyError('refund_reconciliation_required','Uncertain refund exceeded safe provider retry window',409)
        result=self._request('POST',f'/v2/payments/captures/{capture_id}/refund',body={'amount':amount},key=key)
        refund_id=_id(result.get('id'));known=self._request('GET',f'/v2/payments/refunds/{refund_id}')
        if known.get('id')!=refund_id or _amount(known.get('amount'))!=amount_micro_usd:raise EconomyError('invalid_provider_record','PayPal refund identity or amount mismatch',502)
        self.reconcile(payment_id)
        if known.get('status') in ('COMPLETED','CANCELLED','FAILED'):
            self.service.set_hold(payment['run_id'],'refund-request:'+key,0,'refund','Refund resolved from authenticated provider read','refund-resolved:'+key)
        return {'provider':'paypal','payment_id':payment_id,'refund_id':refund_id,'status':known.get('status'),'amount_micro_usd':amount_micro_usd}
    @_bounded_operation
    def recover_checkout(self,request_id):
        claim=self.service.checkout('paypal',self.account,self.mode,request_id)
        if claim.get('payment_id'):return claim
        raise EconomyError('checkout_reconciliation_required','Unbound PayPal checkout requires provider dashboard identification; creation is not repeated',409)
