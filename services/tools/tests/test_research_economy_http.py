"""Actual service-v1 accounting over HTTP; only source/search/model are fixtures.

The economy runs in its own interpreter/namespace. Its explicit test opening
grant is sandbox credit, not PayPal settlement, revenue or a live pilot.
"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import selectors
import subprocess

import pytest

from widemind.integrated_service import EconomyClient
from test_customer_research_workflow import demo


ROOT=Path(__file__).resolve().parents[3]
BOOTSTRAP=r'''
import json,socket,sys,uvicorn
from widemind.economy.app import create_app
from widemind.economy.config import Settings
from widemind.economy.service import EconomyService
service=EconomyService(sys.argv[1]);service.create_run(sys.argv[2],mode='test')
settings=Settings(database_path=sys.argv[1],operator_token='o'*48,gateway_token='g'*48,
                  agent_token='a'*48,agent_run_id=sys.argv[2],payment_provider='paypal')
app=create_app(settings,service=service)
sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(128)
print(json.dumps({'port':sock.getsockname()[1]}),flush=True)
uvicorn.Server(uvicorn.Config(app,log_level='error',access_log=False)).run(sockets=[sock])
'''


@contextmanager
def native_economy(root,run_id):
    python=Path(os.environ.get('WIDEMIND_TEST_ECONOMY_PYTHON',ROOT/'.venvs/economy/bin/python'))
    if not python.is_file():pytest.skip('Install the isolated services/economy test environment for native research accounting HTTP verification.')
    environment=os.environ.copy();environment['PYTHONPATH']=str(ROOT/'services/economy/src')
    process=subprocess.Popen([str(python),'-u','-c',BOOTSTRAP,str(root/'service-v1.sqlite'),run_id],
                             stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=environment,text=True)
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout,selectors.EVENT_READ)
            assert selector.select(15),'Economy fixture process did not become ready.'
        line=process.stdout.readline()
        assert line,process.stderr.read(2048)
        port=json.loads(line)['port']
        yield EconomyClient(f'http://127.0.0.1:{port}','g'*48)
    finally:
        process.terminate()
        try:process.wait(timeout=5)
        except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
        process.stdout.close();process.stderr.close()


def test_actual_paypal_family_reservations_capture_child_bill_without_double_hold_or_charge(tmp_path,monkeypatch):
    monkeypatch.delenv('WIDEMIND_RUNTIME_DATABASE',raising=False)
    monkeypatch.setenv('WIDEMIND_TOOLS_ACCOUNT','dedicated-tools')
    action=demo.request('dental',maximum=100)
    with native_economy(tmp_path,action['runId']) as economy,demo.provider_fixtures() as (origin,provider):
        row=economy.request('POST','/v1/runs/'+action['runId']+'/reservations',{
            'action_id':action['actionId'],'amount_micro_usd':100,'idempotency_key':'native-research-parent',
            'provider':'research.collect','account':'dedicated-tools',
            'limits':{'max_duration_ms':action['limit']['maxDurationMs'],'max_storage_bytes':action['limit']['maxOutputBytes']}})
        reservation={'reservationId':row['reservation_id'],'runId':action['runId'],'actionId':action['actionId'],
                     'limit':action['limit'],'authorization':'native-service-v1-reservation'}
        tools=demo.application(demo.config(tmp_path/'tools',origin),economy)
        payload={'action':action,'reservation':reservation}
        outcome=tools.execute(payload)
        assert outcome.get('kind')=='receipt',outcome
        receipt=outcome['receipt']
        assert receipt['status']=='succeeded',receipt
        assert receipt['usage']['actualCharge']['micros']=='0'
        children=economy.reservation_children(row['reservation_id'])
        ai=[item for item in children if item['provider']=='ai.generate']
        assert len(ai)==1 and ai[0]['state']=='unknown'
        assert ai[0]['provisional_micro_usd']>0 and ai[0]['actual_micro_usd'] is None
        assert {item['action_id'] for item in children}=={item['action_id'] for item in receipt['result']['data']['child_receipts']}
        balance=economy.request('GET','/v1/runs/'+action['runId']+'/balance')
        known=ai[0]['provisional_micro_usd'];maximum=ai[0]['amount_micro_usd']
        assert balance['posted_micro_usd']==5_000_000-known
        assert balance['available_micro_usd']==5_000_000-100
        assert balance['reserved_micro_usd']==100-known
        economy.settle(row['reservation_id'],receipt)
        balance=economy.request('GET','/v1/runs/'+action['runId']+'/balance')
        assert balance['reserved_micro_usd']==maximum-known,'Unknown child liability must survive zero-cost parent closure.'
        ledger=economy.request('GET','/v1/runs/'+action['runId']+'/ledger')
        costs=[item for item in ledger if item['kind']=='operating_charge']
        assert sum(item['amount_micro_usd'] for item in costs)==-known
        assert any(item['metadata'].get('reservation_id')==ai[0]['reservation_id'] and item['amount_micro_usd']==-known for item in costs)
        assert tools.execute(payload)==outcome and len(provider['ai_posts'])==1
