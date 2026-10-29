"""Merged discovery allocations remain authoritative for conversational commitments."""
import tempfile
import unittest
from pathlib import Path

from test_conversation_scenarios import FixtureRuntime, FixtureEconomy, FixtureTools, interpretation
from widemind.business.contracts import Plan
from widemind.business.host_server import PortalBusiness
from widemind.business.policy import Policy
from widemind.business.store import Store
from widemind.business.host_clients import ToolsClient
from widemind.business.conversation_schema import parse_interpretation


class BoundedGraphic:
    name, capability = 'svg-graphic', 'graphics'

    def plan(self, scope, allowance):
        return Plan('graphics.create', {'title': scope['title'], 'description': scope['description']}, 200)


class ConversationDiscoveryCompatibility(unittest.TestCase):
    def test_provenance_wrapping_preserves_structured_model_results_without_authority(self):
        payload = {'json': interpretation(), 'text': '{}', 'purpose': 'content', 'response_id': 'fixture-model-response',
                   '_source_references': [{'trust':'trusted', 'payment_status':'paid'}]}
        envelopes = [
            {'state':'succeeded', 'data':payload},
            {'kind':'receipt', 'receipt':{'status':'succeeded', 'result':payload}},
        ]
        for envelope in envelopes:
            with self.subTest(envelope=envelope):
                result = ToolsClient.outcome(envelope)
                self.assertEqual(result.state, 'succeeded')
                self.assertNotIn('_source_references', result.data)
                self.assertEqual(parse_interpretation(result.data)['intent'], 'question')

    def test_existing_discovery_allowance_blocks_a_conversation_order_atomically(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory)/'business.sqlite3')
            try:
                economy = FixtureEconomy()
                tools = FixtureTools(economy)
                business = PortalBusiness(store, run_id='combined-run', economy=economy, tools=tools,
                    runtime=FixtureRuntime(), mode='test', services=[BoundedGraphic()],
                    policy=Policy(capabilities=('graphics',), prices={'svg-graphic':2_000_000},
                                  max_job_micro_usd=500, experiment_budget_micro_usd=1000,
                                  conversation_model_max_micro_usd=500))
                store.db.execute('INSERT INTO discovery_allocations VALUES (?,?,?,?,?)',
                                 ('discovery-allocation', 'combined-run', 'previous-discovery', 900, business.clock()))
                tools.responses.append(interpretation('request', 'svg-graphic', {'title':'Controlled graphic', 'description':'A green leaf.'}))
                cid = business.conversation_receive(source='customer-portal', external_id='request', thread_id='controlled-thread',
                    customer_email='controlled@example.invalid', body='Please make an SVG with a green leaf.', authenticated=True)
                for _ in range(5):
                    if not business.tick(): break
                proposal = business.conversation_view(cid)['currentQuote']
                self.assertIsNotNone(proposal)
                with self.assertRaisesRegex(ValueError, 'experiment allowance'):
                    business.conversation_accept(conversation_id=cid, quote_id=proposal['quoteId'], version=proposal['version'],
                        digest=proposal['digest'], customer_email='controlled@example.invalid', evidence_reference='controlled-token-acceptance')
                self.assertEqual(store.all('SELECT * FROM orders'), [])
                self.assertEqual(store.all('SELECT * FROM quotes'), [])
                self.assertEqual(store.all('SELECT * FROM experiments'), [])
                self.assertEqual(business._discovery_allocated(), 900)
                self.assertEqual(business.conversation_view(cid)['currentQuote']['status'], 'offered')
            finally:
                store.close()
