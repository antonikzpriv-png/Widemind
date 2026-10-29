"""Conversation budget binding uses the selected economy, never a second wallet."""
import tempfile
import unittest
import json
from pathlib import Path

from widemind.business.host_clients import EconomyClient, Unavailable
from widemind.business.store import Store


class HTTPFixture:
    def __init__(self,mode='test'):
        self.mode=mode
        self.calls=[]
    def request(self,path,body=None,method=None):
        self.calls.append((path,body))
        if path.endswith('/balance'):return {'run_id':'run','mode':self.mode}
        if path.endswith('/reservations'):return {'reservation_id':'fixture-reservation'}
        raise AssertionError('unexpected provider request')


class ConversationTransportTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.store=Store(Path(self.temp.name)/'business.sqlite3')
        self.client=EconomyClient({},self.store,'test')
        self.fixture=HTTPFixture()
        self.client.http=self.fixture
    def tearDown(self):
        self.store.close();self.temp.cleanup()
    def reserve(self,**overrides):
        return self.client.reserve_conversation(**{'run_id':'run','conversation_id':'thread',
            'action_id':'exact-model-effect','maximum_micro_usd':100000,**overrides})
    def test_model_reservation_has_exact_action_identity_and_enforceable_bounds(self):
        result=self.reserve()
        self.assertEqual(result.state,'succeeded')
        request=self.fixture.calls[-1][1]
        self.assertEqual(request['action_id'],'exact-model-effect')
        self.assertEqual(request['provider'],'ai.generate')
        self.assertEqual(request['account'],'dedicated-tools')
        self.assertEqual(request['amount_micro_usd'],100000)
        self.assertEqual(request['limits'],{'max_duration_ms':120000,'max_storage_bytes':65536,'max_tokens':2000})
        self.assertEqual(self.store.all('SELECT * FROM orders'),[])
        self.reserve()
        self.assertEqual(self.fixture.calls[-1][1],request)
        self.assertEqual(self.store.all('SELECT * FROM effects'),[])
    def test_gmail_uses_native_zero_cost_reservation_bound_to_its_tool(self):
        for tool in ('gmail.read','gmail.list','gmail.thread','gmail.send'):
            self.reserve(tool=tool,maximum_micro_usd=0,action_id='effect-'+tool)
            request=self.fixture.calls[-1][1]
            self.assertEqual(request['provider'],tool)
            self.assertEqual(request['amount_micro_usd'],0)
            self.assertNotIn('max_tokens',request['limits'])
    def test_mode_mismatch_cannot_reserve_or_fall_back_to_another_deployment(self):
        self.fixture.mode='live'
        with self.assertRaises(Unavailable):self.reserve()
        self.assertEqual(len(self.fixture.calls),1)
    def test_invalid_inputs_fail_before_any_economy_call(self):
        for changes in ({'tool':'paid.unknown'},{'maximum_micro_usd':True},{'maximum_micro_usd':-1},
                        {'maximum_micro_usd':0},{'max_tokens':0},{'max_tokens':True},
                        {'max_tokens':16001},{'tool':'gmail.send','maximum_micro_usd':1},
                        {'action_id':'bad\nidentity'}):
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):self.reserve(**changes)
        self.assertEqual(self.fixture.calls,[])
    def test_lost_reservation_response_reconciles_exact_persisted_binding_without_reserving_again(self):
        request={'actual_action_id':'exact-model-effect','tool':'ai.generate','maximum_micro_usd':100000,'max_tokens':2000}
        self.store.db.execute("INSERT INTO effects(id,run_id,job_id,provider,action,request,state,created_at,logical_id,attempt) VALUES (?,?,?,?,?,?,'unknown',0,?,0)",
            ('reservation-effect','run','thread','economy','reserve_conversation',json.dumps(request),'reservation-effect'))
        calls=[]
        def observed(path,body=None,method=None):
            calls.append((path,body))
            return [{'reservation_id':'persisted','run_id':'run','action_id':'exact-model-effect','provider':'ai.generate',
                     'account':'dedicated-tools','amount_micro_usd':100000,
                     'limits':{'max_duration_ms':120000,'max_storage_bytes':65536,'max_tokens':2000}}]
        self.client.http.request=observed
        result=self.client.reconcile(action_id='reservation-effect')
        self.assertEqual(result.state,'succeeded')
        self.assertEqual(result.data['reservation_id'],'persisted')
        self.assertEqual(calls,[('/v1/runs/run/reservations',None)])


if __name__=='__main__':unittest.main()
