"""Durable free inbox cadence; fixture scheduling never invokes provider tools."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from widemind.business.conversation_poll import poll_seconds,schedule_inbox
from widemind.business.store import Store


class Tools:
    def __init__(self):self.available_value=True;self.checks=0;self.executions=0
    def available(self,tool):
        assert tool=='gmail.read'
        self.checks+=1
        return self.available_value
    def execute(self,**_kwargs):
        self.executions+=1
        raise AssertionError('Scheduling cannot invoke any provider/model')


class BusinessFixture:
    def __init__(self,path,clock=1200):
        self.store=Store(path);self.run_id='run';self.now=clock;self.active=True
        self.policy=SimpleNamespace(conversation_enabled=True);self.tools=Tools()
    def clock(self):return self.now
    def _active(self):return self.active
    def notify(self,**event):
        return self.store.event(self.run_id,event['source'],event['external_id'],event['kind'],event['payload'],event['due_at'])


class ConversationPollTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'business.sqlite3'
        self.business=BusinessFixture(self.path);self.env={'WIDEMIND_CONVERSATION_GMAIL_POLL_SECONDS':'60'}
    def tearDown(self):self.business.store.close();self.temp.cleanup()
    def events(self):return self.business.store.all('SELECT * FROM events ORDER BY due_at,id')
    def test_disabled_by_default_and_malformed_interval_is_not_a_busy_poll(self):
        for env in ({},{'WIDEMIND_CONVERSATION_GMAIL_POLL_SECONDS':''},{'WIDEMIND_CONVERSATION_GMAIL_POLL_SECONDS':'0'}):
            schedule_inbox(self.business,env)
        self.assertEqual(self.events(),[]);self.assertEqual(self.business.tools.checks,0)
        for value in ('1','59','86401','-60','60.0','always',True):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):poll_seconds({'WIDEMIND_CONVERSATION_GMAIL_POLL_SECONDS':value})
    def test_one_durable_event_per_cadence_and_restart_keeps_the_same_bound(self):
        schedule_inbox(self.business,self.env);schedule_inbox(self.business,self.env)
        self.assertEqual(len(self.events()),1)
        self.assertEqual(json.loads(self.events()[0]['payload']),{'cursor':None})
        self.business.store.db.execute("UPDATE events SET state='done'")
        another=BusinessFixture(self.path,clock=1259)
        try:
            schedule_inbox(another,self.env)
            self.assertEqual(another.tools.checks,0)
            self.assertEqual(len(self.events()),1)
            another.now=1260;schedule_inbox(another,self.env)
            self.assertEqual(len(self.events()),2)
        finally:another.store.close()
        self.assertEqual(self.business.tools.executions,0)
    def test_pause_stop_disabled_policy_and_unavailable_mail_do_not_queue_work(self):
        self.business.active=False;schedule_inbox(self.business,self.env)
        self.business.active=True;self.business.policy.conversation_enabled=False;schedule_inbox(self.business,self.env)
        self.assertEqual(self.business.tools.checks,0)
        self.business.policy.conversation_enabled=True;self.business.tools.available_value=False
        schedule_inbox(self.business,self.env)
        self.assertEqual(self.events(),[])
    def test_cursor_pages_and_blocked_or_unknown_reads_prevent_fresh_scan_ids(self):
        for state in ('queued','running','blocked'):
            self.business.store.db.execute('DELETE FROM events')
            self.business.store.event('run','gmail-cursor','provider-page','gmail.sync',{'cursor':'frozen-page'},100)
            self.business.store.db.execute('UPDATE events SET state=?',(state,))
            schedule_inbox(self.business,self.env)
            self.assertEqual(len(self.events()),1)
        self.business.store.db.execute('DELETE FROM events')
        self.business.store.db.execute("INSERT INTO effects(id,run_id,job_id,provider,action,request,state,created_at,logical_id,attempt) VALUES ('uncertain-inbox','run','inbox','tools','gmail.read','{}','unknown',0,'uncertain-inbox',0)")
        schedule_inbox(self.business,self.env)
        self.assertEqual(self.events(),[])
        self.business.store.db.execute("UPDATE effects SET state='succeeded'")
        schedule_inbox(self.business,self.env)
        self.assertEqual(len(self.events()),1)
    def test_other_runs_do_not_block_the_configured_run_and_no_model_is_called(self):
        self.business.store.event('another-run','gmail-cursor','other','gmail.sync',{'cursor':None},100)
        schedule_inbox(self.business,self.env)
        self.assertEqual(len([row for row in self.events() if row['run_id']=='run']),1)
        self.assertEqual(self.business.tools.executions,0)


if __name__=='__main__':unittest.main()
