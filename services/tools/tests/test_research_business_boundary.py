"""Actual generated research DTO consumed by isolated PayPal business code.

Only controlled fixture bytes are used. HTTPS citation names below are fixture
identities, transported to a loopback server by this test-only transport.
"""
import json
import os
from pathlib import Path
import subprocess
from urllib.parse import urlsplit

import pytest

from widemind.http import HTTPTransport
from widemind.tools.contracts import HTTPResponse
from test_customer_research_workflow import demo


ROOT=Path(__file__).resolve().parents[3]


def test_generated_document_is_accepted_by_business_without_forwarding_private_manifest(tmp_path,monkeypatch):
    python=Path(os.environ.get('WIDEMIND_TEST_BUSINESS_PYTHON',ROOT/'.venvs/business/bin/python'))
    if not python.is_file():pytest.skip('Install the isolated services/business environment to verify the generated research delivery boundary.')
    monkeypatch.delenv('WIDEMIND_RUNTIME_DATABASE',raising=False)
    monkeypatch.setenv('WIDEMIND_TOOLS_ACCOUNT','dedicated-tools')
    with demo.provider_fixtures() as (origin,provider):
        config=demo.config(tmp_path,origin)
        app=demo.application(config,demo.ReservationFamilyFixture())
        transport=HTTPTransport(config['policy'])
        class FixtureCitationTransport:
            def request(self,method,url,**arguments):
                parsed=urlsplit(url)
                if parsed.hostname=='controlled-sources.example.test':
                    raw=transport.request(method,origin+parsed.path,**arguments)
                    return HTTPResponse(raw.status,raw.headers,raw.body,url)
                raw=transport.request(method,url,**arguments)
                if parsed.path=='/search':
                    data=json.loads(raw.body)
                    for item in data['results']:
                        item['url']='https://controlled-sources.example.test'+urlsplit(item['url']).path
                    return HTTPResponse(raw.status,raw.headers,json.dumps(data).encode(),raw.url)
                return raw
        app.transport=FixtureCitationTransport()
        action=demo.request('dental')
        reservation=app.economy.admit(action)
        outcome=app.execute({'action':action,'reservation':reservation})
        assert outcome.get('kind')=='receipt' and outcome['receipt']['status']=='succeeded',outcome
        data=outcome['receipt']['result']['data']
        bootstrap='''import json,sys
from widemind.business.services import ResearchBrief
value=json.load(sys.stdin)
result=ResearchBrief().deliverable(value['scope'],value['result'])
assert 'evidence_manifest' not in result and all('excerpt' not in s for s in result['sources'])
assert result['content']==value['result']['document']['content']
print(json.dumps({'filename':result['filename'],'media_type':result['media_type'],'private_manifest_forwarded':False}))
'''
        environment=os.environ.copy();environment['PYTHONPATH']=str(ROOT/'services/business')
        result=subprocess.run([str(python),'-c',bootstrap],input=json.dumps({'scope':action['input'],'result':data}),
                              text=True,capture_output=True,timeout=10,env=environment)
        assert result.returncode==0,result.stderr
        verified=json.loads(result.stdout)
        assert verified['filename']=='research-brief.md' and verified['private_manifest_forwarded'] is False
        assert len(provider['ai_posts'])==1
