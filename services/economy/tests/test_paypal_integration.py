"""Controlled HTTP fixtures, not live or sandbox payments."""
import json
from pathlib import Path
import httpx
import pytest
from fastapi.testclient import TestClient
from widemind.economy.service import EconomyService,EconomyError
from widemind.economy.paypal import PayPalPayments
from widemind.economy.app import create_app
from widemind.economy.config import Settings
from widemind.economy.reconciliation import ReconciliationWorker

class PayPalFixture:
    def __init__(self):self.captured=False;self.refunded=False;self.keys=[];self.calls=[];self.bad_merchant=False;self.bad_signature=False;self.unit=None;self.refund_status='COMPLETED';self.refund_fee=None
    def __call__(self,request):
        path=request.url.path;self.calls.append((request.method,path));amount=lambda value:{'currency_code':'USD','value':value}
        if path=='/v1/oauth2/token':return httpx.Response(200,json={'access_token':'fixture-oauth','expires_in':3600})
        assert request.headers.get('authorization')=='Bearer fixture-oauth'
        if path=='/v1/identity/oauth2/userinfo':value={}
        elif path=='/v1/notifications/verify-webhook-signature':value={'verification_status':'FAILURE' if self.bad_signature else 'SUCCESS'}
        elif path=='/v2/checkout/orders' and request.method=='POST':
            self.unit=json.loads(request.content)['purchase_units'][0];self.keys.append(request.headers['paypal-request-id']);value={'id':'ORDER_1','links':[{'rel':'approve','href':'https://www.sandbox.paypal.com/checkoutnow?token=ORDER_1'}]}
        elif path=='/v2/checkout/orders/ORDER_1/capture':self.captured=True;value={'id':'ORDER_1','status':'COMPLETED'}
        elif path=='/v2/checkout/orders/ORDER_1':value={'id':'ORDER_1','intent':'CAPTURE','status':'COMPLETED' if self.captured else 'APPROVED','purchase_units':[{**(self.unit or {}),'payee':{'merchant_id':'WRONG' if self.bad_merchant else 'MERCHANT'},'payments':{'captures':[{'id':'CAPTURE_1'}] if self.captured else [],'refunds':[{'id':'REFUND_1'}] if self.refunded else []}}]}
        elif path=='/v2/payments/captures/CAPTURE_1':value={'id':'CAPTURE_1','status':'COMPLETED','supplementary_data':{'related_ids':{'order_id':'ORDER_1'}},'amount':amount('10.00'),'seller_receivable_breakdown':{'gross_amount':amount('10.00'),'paypal_fee':amount('0.50'),'net_amount':amount('9.50')}}
        elif path=='/v2/payments/captures/CAPTURE_1/refund':self.refunded=True;self.keys.append(request.headers['paypal-request-id']);value={'id':'REFUND_1','status':'COMPLETED'}
        elif path=='/v2/payments/refunds/REFUND_1':value={'id':'REFUND_1','status':self.refund_status,'amount':amount('2.00'),**({'seller_payable_breakdown':{'paypal_fee':amount(self.refund_fee)}} if self.refund_fee else {}),'links':[{'rel':'up','href':'https://api-m.sandbox.paypal.com/v2/payments/captures/CAPTURE_1'}]}
        else:raise AssertionError((request.method,path))
        return httpx.Response(200,json=value)

def configuration(tmp_path):return Settings(str(tmp_path/'accounting.sqlite'),'fixture-op-'+'o'*40,'fixture-gateway-'+'g'*40,'fixture-agent-'+'a'*40,'run',payment_provider='paypal',paypal_client_id='fixture-id',paypal_client_secret='fixture-secret',paypal_account='MERCHANT',paypal_mode='test',paypal_webhook_id='fixture-webhook',checkout_success_url='http://localhost/success',checkout_cancel_url='http://localhost/cancel')
def adapter(service,fixture):return PayPalPayments(service,client_id='fixture-id',client_secret='fixture-secret',account='MERCHANT',mode='test',webhook_id='fixture-webhook',success_url='http://localhost/success',cancel_url='http://localhost/cancel',client=httpx.Client(transport=httpx.MockTransport(fixture)))

def test_paypal_checkout_capture_replay_refund_and_public_projection(tmp_path):
    service=EconomyService(tmp_path/'ledger.sqlite');service.create_run('run');fixture=PayPalFixture();provider=adapter(service,fixture)
    created=provider.create_checkout('run','checkout-1','job-1',10_000_000)
    assert created['payment_id']=='ORDER_1';assert len(fixture.keys)==1
    assert provider.create_checkout('run','checkout-1','job-1',10_000_000)==created;assert len(fixture.keys)==1
    assert service.balance('run')['posted_micro_usd']==5_000_000
    provider.reconcile('ORDER_1');assert service.balance('run')['posted_micro_usd']==5_000_000
    provider.capture('ORDER_1','capture-1');provider.capture('ORDER_1','capture-1')
    balance=service.balance('run');assert balance['posted_micro_usd']==14_500_000;assert balance['held_micro_usd']==9_500_000;assert balance['available_micro_usd']==5_000_000
    service.release_payment_hold('paypal','MERCHANT','test','ORDER_1','fulfilled-1');assert service.balance('run')['available_micro_usd']==14_500_000
    refund=provider.create_refund('ORDER_1','refund-1',2_000_000);assert refund['refund_id']=='REFUND_1';assert service.balance('run')['posted_micro_usd']==12_500_000
    provider.create_refund('ORDER_1','refund-1',2_000_000);assert service.balance('run')['posted_micro_usd']==12_500_000;assert len(fixture.keys)==2;assert service.balance('run')['held_micro_usd']==0
    snapshot=service.payment_snapshot('paypal','MERCHANT','test','ORDER_1');assert snapshot['refunds_micro_usd']==2_000_000;assert snapshot['receipts_micro_usd']==10_000_000

def test_verified_paypal_webhook_durable_worker_uses_provider_reads(tmp_path):
    settings=configuration(tmp_path);service=EconomyService(settings.database_path);service.create_run('run');fixture=PayPalFixture();provider=adapter(service,fixture);provider.create_checkout('run','checkout','job',10_000_000)
    with TestClient(create_app(settings,service=service,payments=provider)) as app:
        raw=json.dumps({'id':'EVENT_1','event_type':'CHECKOUT.ORDER.APPROVED','resource':{'id':'ORDER_1'}}).encode();headers={name:'fixture-value' for name in ('paypal-auth-algo','paypal-cert-url','paypal-transmission-id','paypal-transmission-sig','paypal-transmission-time')}
        fixture.bad_signature=True;assert app.post('/v1/webhooks/paypal',content=raw,headers=headers).status_code==400
        fixture.bad_signature=False
        for _ in range(2):assert app.post('/v1/webhooks/paypal',content=raw,headers=headers).status_code==202
        assert service.balance('run')['posted_micro_usd']==5_000_000
        assert ReconciliationWorker(app.app.state.reconciliation_queue,provider).run_once()['completed']==1
        assert service.balance('run')['posted_micro_usd']==14_500_000
        assert ReconciliationWorker(app.app.state.reconciliation_queue,provider).run_once()['processed']==0
        auth={'Authorization':'Bearer '+settings.operator_token};summary=app.get('/v1/runs/run/runtime-summary',headers=auth).json();assert summary['postedMicros']=='14500000';assert summary['mode']=='sandbox'

def test_wrong_merchant_and_live_custom_endpoint_fail_closed(tmp_path):
    service=EconomyService(tmp_path/'ledger.sqlite');service.create_run('run');fixture=PayPalFixture();provider=adapter(service,fixture);provider.create_checkout('run','checkout','job',10_000_000);fixture.bad_merchant=True
    with pytest.raises(EconomyError,match='payee'):provider.capture('ORDER_1','capture')
    assert not fixture.captured
    with pytest.raises(EconomyError,match='test mode'):PayPalPayments(service,client_id='id',client_secret='secret',account='MERCHANT',mode='live',webhook_id='wh',success_url='https://example.test/s',cancel_url='https://example.test/c',test_api_base='http://127.0.0.1:9999',allow_test_endpoint=True)
    with pytest.raises(EconomyError,match='funding reference'):service.create_run('live',mode='live')

def test_zero_cost_authorization_records_identity_and_rejects_duplicate_dispatch(tmp_path):
    service=EconomyService(tmp_path/'ledger.sqlite');service.create_run('run');reservation=service.reserve('run','free-action',0,'free-key','files','dedicated-tools',{'max_duration_ms':1000,'max_storage_bytes':4096});assert reservation['amount_micro_usd']==0
    service.start_reservation(reservation['reservation_id'])
    with pytest.raises(EconomyError,match='dispatched'):service.start_reservation(reservation['reservation_id'])
    service.settle(reservation['reservation_id'],0,'files','dedicated-tools','free-charge');assert service.balance('run')['posted_micro_usd']==5_000_000

def test_version_four_upgrade_preserves_charged_reservation_foreign_keys(tmp_path):
    import sqlite3
    from widemind.economy.reporting import audit_integrity
    path=tmp_path/'v4.sqlite';service=EconomyService(path);service.create_run('run')
    reservation=service.reserve('run','charged',1000,'charged','files','dedicated-tools',{'max_duration_ms':1000})
    service.start_reservation(reservation['reservation_id']);service.settle(reservation['reservation_id'],100,'files','dedicated-tools','charged-event')
    with sqlite3.connect(path) as db:db.execute('PRAGMA user_version=4')
    recovered=EconomyService(path)
    assert recovered.balance('run')['posted_micro_usd']==4_999_900
    assert recovered.reservation(reservation['reservation_id'])['state']=='settled'
    assert audit_integrity(recovered,'run')['ok'] is True

def test_snapshot_revision_includes_later_costs_and_earned_performance(tmp_path):
    service=EconomyService(tmp_path/'ledger.sqlite');service.create_run('run');service.register_payment('run','paypal','MERCHANT','ORDER_1','job','binding')
    first=service.payment_snapshot('paypal','MERCHANT','test','ORDER_1')
    service.record_earned_revenue('run',10,'job','verified-fixture-delivery','earned')
    second=service.payment_snapshot('paypal','MERCHANT','test','ORDER_1')
    assert second['provider_revision']==first['provider_revision']
    assert second['revision']>first['revision'];assert second['earned_micro_usd']==10
    assert service.balance('run')['posted_micro_usd']==5_000_000


def test_unregistered_order_cannot_be_captured(tmp_path):
    service=EconomyService(tmp_path/'ledger.sqlite');service.create_run('run');fixture=PayPalFixture();provider=adapter(service,fixture)
    with pytest.raises(EconomyError,match='not registered'):provider.capture('ORDER_1','capture')
    assert fixture.calls==[]

def test_pending_failed_refund_reverses_only_verified_liability_once(tmp_path):
    service=EconomyService(tmp_path/'ledger.sqlite');service.create_run('run');fixture=PayPalFixture();provider=adapter(service,fixture);provider.create_checkout('run','checkout','job',10_000_000);provider.capture('ORDER_1','capture');fixture.refund_status='PENDING';provider.create_refund('ORDER_1','refund',2_000_000)
    assert service.balance('run')['posted_micro_usd']==12_500_000
    fixture.refund_status='FAILED';provider.create_refund('ORDER_1','refund',2_000_000);provider.reconcile('ORDER_1')
    assert service.balance('run')['posted_micro_usd']==14_500_000
    assert len(fixture.keys)==2
    assert service.payment_snapshot('paypal','MERCHANT','test','ORDER_1')['refunds_micro_usd']==0


def test_verified_refund_fee_rebate_is_separate_and_never_double_counted(tmp_path):
    service=EconomyService(tmp_path/'ledger.sqlite');service.create_run('run');fixture=PayPalFixture();provider=adapter(service,fixture);provider.create_checkout('run','checkout','job',10_000_000);provider.capture('ORDER_1','capture');fixture.refund_fee='0.10';provider.create_refund('ORDER_1','refund',2_000_000);provider.reconcile('ORDER_1')
    assert service.balance('run')['posted_micro_usd']==12_600_000
    snapshot=service.payment_snapshot('paypal','MERCHANT','test','ORDER_1');assert snapshot['fees_micro_usd']==400_000


def test_refund_webhook_maps_through_authenticated_capture_parent(tmp_path):
    service=EconomyService(tmp_path/'ledger.sqlite');service.create_run('run');fixture=PayPalFixture();provider=adapter(service,fixture);provider.create_checkout('run','checkout','job',10_000_000);provider.capture('ORDER_1','capture');fixture.refunded=True
    event={'id':'EVENT_REFUND','type':'PAYMENT.CAPTURE.REFUNDED','data':{'object':{'id':'REFUND_1','links':[{'rel':'up','href':'https://api-m.sandbox.paypal.com/v2/payments/captures/CAPTURE_1'}]}}}
    provider.process_verified_event(event,'a'*64);assert service.balance('run')['posted_micro_usd']==12_500_000
