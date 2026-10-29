"""Parent progress fencing is independent of provider billing receipts."""
import json
import time

import pytest

from widemind.integrated_service import Journal
from widemind.research_runtime import ResearchLease,WorkflowState
from widemind.tools.contracts import ToolError
from widemind.tools.contracts import ToolResult,Usage


def test_expired_parent_cannot_overwrite_replacement_progress_or_cross_action_state(tmp_path):
    journal=Journal(tmp_path/'private')
    action={'actionId':'parent'};reservation={'reservationId':'reservation'}
    journal.prepare(action,reservation,'owner-profile')
    first=ResearchLease(journal,'parent',10)
    stale=WorkflowState(journal,first)
    stale.update('parent',{'stage':'first-completed'})
    with pytest.raises(ToolError):stale.load('another-customer')
    with journal.connection() as db:db.execute('UPDATE research_execution_leases SET expires=?',(time.time()-1,))
    second=ResearchLease(journal,'parent',10)
    current=WorkflowState(journal,second)
    current.update('parent',{'stage':'replacement-completed'})
    assert second.save(status='succeeded',result='{"kind":"receipt","accepted":"replacement"}')
    assert first.save(status='failed',result='{"kind":"receipt","accepted":"stale"}') is False
    with pytest.raises(ToolError,match='superseded'):stale.update('parent',{'stage':'stale-overwrite'})
    first.close()
    assert current.load('parent')['stage']=='replacement-completed'
    assert json.loads(journal.get('parent')['result'])['accepted']=='replacement'
    assert second.save(status='unknown',result='{"kind":"unknown"}') is False
    assert json.loads(journal.get('parent')['result'])['accepted']=='replacement'
    second.close()


def test_provider_trace_ids_do_not_collide_or_rebind_new_financial_events(tmp_path):
    from widemind.integrated_service import IntegratedTools
    from test_integrated_service import Economy,Secrets,action
    config={'control_dir':str(tmp_path/'control'),'workspace_dir':str(tmp_path/'workspace'),'tools':{}}
    app=IntegratedTools(config,Economy(),secrets=Secrets())
    events=[]
    for index in range(2):
        a=action('ai.generate',{},id='new-generation-'+str(index))
        r={'reservationId':'r-'+str(index)}
        app.journal.prepare(a,r,'owner')
        result=ToolResult({'text':'Synthetic measured output'},Usage(7,provider_request_id='repeated-provider-trace',provisional=True))
        first=app.receipt(a,result)['receipt']
        again=app.receipt(a,ToolResult(result.data,Usage(7,provider_request_id='retrieval-trace',provisional=True)))['receipt']
        assert first['usage']['externalEventId']==again['usage']['externalEventId']==a['actionId']
        assert first['result']['nativeUsage']['provider_request_id']=='repeated-provider-trace'
        events.append(first['usage']['externalEventId'])
    assert len(set(events))==2


def test_pre_upgrade_posted_subtotal_keeps_its_original_event_binding(tmp_path):
    from widemind.integrated_service import IntegratedTools
    from test_integrated_service import Economy,Secrets,action
    app=IntegratedTools({'control_dir':str(tmp_path/'control'),'workspace_dir':str(tmp_path/'workspace'),'tools':{}},Economy(),secrets=Secrets())
    a=action('ai.generate',{},id='legacy-generation')
    app.journal.prepare(a,{'reservationId':'legacy-reservation'},'owner')
    app.journal.update(a['actionId'],{'partial_usage':{'provider_request_id':'pre-upgrade-posted-event','cost_microusd':4}})
    receipt=app.receipt(a,ToolResult({},Usage(7,provider_request_id='later-retrieval',provisional=True)))['receipt']
    assert receipt['usage']['externalEventId']=='pre-upgrade-posted-event'
