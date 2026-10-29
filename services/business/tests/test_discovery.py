"""Explicit synthetic discovery fixtures; no verified real market/customer claims."""
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from widemind.business.contracts import Outcome, Plan
from widemind.business.engine import Business
from widemind.business.policy import Policy
from widemind.business.store import Store, encode, identity


class PrototypeService:
    name, capability = 'fixture-prototype', 'graphics'

    def plan(self, scope, allowance):
        if not isinstance(scope.get('title'), str):
            raise ValueError('a title is required')
        return Plan('graphics.create', {'title': scope['title']}, allowance)

    def deliverable(self, scope, result):
        if result.get('bad_scope'):
            raise ValueError('scope failed')
        return {'filename': 'prototype.svg', 'media_type': 'image/svg+xml', 'content': '<svg><title>' + scope['title'] + '</title></svg>', 'checks': {'scope': True}}


class FixtureAuthorities:
    mode = 'test'

    def __init__(self, clock):
        self.clock = clock
        self.run_state, self.funds = 'active', 1000000
        self.calls, self.reservations, self.results = [], {}, {}
        self.source_text = 'A synthetic market says: we need a graphic for a small shop.'
        self.disabled = set()
        self.retrieved_offset = 0
        self.provenance = True
        self.unknown_once = False
        self.costs_final = True
        self.payment_snapshots = {}
        self.on_available = None

    def state(self, run_id):
        return self.run_state

    def available(self, action):
        if self.on_available:
            hook, self.on_available = self.on_available, None
            hook()
        return action not in self.disabled

    def reserve_work(self, **request):
        self.calls.append(('reserve', request['tool_action_id']))
        if self.funds < request['maximum_micro_usd']:
            return Outcome('failed', {'denial': 'insufficient_funds'}, 'insufficient funds')
        reservation = identity(request['tool_action_id'], 'fixture-reservation')
        self.reservations[reservation] = {'request': request, 'actual_micro_usd': None, 'costs_final': False, 'state': 'reserved'}
        return Outcome('succeeded', {'reservation_id': reservation})

    def execute(self, **request):
        self.calls.append((request['action'], request['action_id']))
        row = self.reservations[request['reservation_id']]
        assert row['request']['tool_action_id'] == request['action_id']
        assert row['request']['tool'] == request['action']
        if request['action'] == 'research.fetch':
            url = request['arguments']['url']
            digest = hashlib.sha256(self.source_text.encode()).hexdigest()
            data = {'url': url, 'text': self.source_text, 'title': 'Explicit synthetic page', 'status': 200, 'source_id': 'sha256:' + digest}
            if self.provenance:
                data['_source_references'] = [{'url': url, 'source_id': 'sha256:' + digest, 'sha256': digest,
                    'retrieved_at': datetime.fromtimestamp(self.clock() + self.retrieved_offset, timezone.utc).isoformat(), 'trust': 'untrusted'}]
            actual = 1000
        else:
            data, actual = {}, 20000
        row.update(actual_micro_usd=actual, costs_final=self.costs_final, state='settled' if self.costs_final else 'unknown')
        outcome = Outcome('succeeded', data)
        self.results[request['action_id']] = outcome
        if self.unknown_once:
            self.unknown_once = False
            return Outcome('unknown', reason='explicit lost fixture response')
        return outcome

    def reconcile(self, *, action_id):
        return self.results.get(action_id, Outcome('unknown', reason='fixture has no observational result'))

    def work_cost(self, *, run_id, reservation_id):
        return {key: self.reservations[reservation_id][key] for key in ('actual_micro_usd', 'costs_final', 'state')}

    def payment(self, *, run_id, job_id):
        return self.payment_snapshots[job_id]

    def release(self, *, run_id, job_id, action_id, reservation_id):
        row = self.reservations[reservation_id]
        if row['state'] != 'reserved':
            return Outcome('unknown', reason='cannot cancel an executed fixture action')
        row.update(actual_micro_usd=0, costs_final=True, state='cancelled')
        return Outcome('succeeded', {'reservation_id': reservation_id})


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / 'business.sqlite3'
        self.now = 1800000000.0
        self.adapters = FixtureAuthorities(lambda: self.now)
        self.open()
        self.market = {'id': 'fixture-market', 'label': 'Explicit fixture market', 'need_terms': ['need a graphic'],
            'source_urls': ['https://market.example/need?utm_source=fixture'], 'service': 'fixture-prototype',
            'scope': {'title': 'A bounded sample'}, 'kind': 'experiment', 'proposed_offering': 'A useful shop graphic',
            'research_allowance_micro_usd': 10000, 'interval_seconds': 300, 'freshness_seconds': 600,
            'cooldown_seconds': 300, 'max_attempts': 2, 'priority': 30}
        self.config = {'enabled': True, 'allowance_micro_usd': 500000, 'adaptation_limit': 5, 'adaptation_min_samples': 1, 'markets': [self.market]}

    def open(self):
        self.store = Store(self.path)
        self.business = Business(self.store, run_id='synthetic-run', mode='test', economy=self.adapters, tools=self.adapters,
            runtime=self.adapters, services=[PrototypeService()], clock=lambda: self.now,
            policy=Policy(capabilities=('research', 'graphics'), max_job_micro_usd=250000, experiment_budget_micro_usd=1000000,
                          prices={'fixture-prototype': 2000000}))

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    def drain(self):
        for _ in range(40):
            if not self.business.tick():
                return
        self.fail('fixture queue did not become idle')

    def discover(self):
        self.business.configure_discovery(self.config)
        self.drain()
        return self.store.one('SELECT * FROM discoveries')

    def spec(self):
        return {'hypothesis': 'A single sample will test the observed need.', 'allowance_micro_usd': 100000,
                'deadline': self.now + 600, 'success_criteria': {'artifact_valid': True, 'min_conversions': 1, 'min_contribution_micro_usd': 1},
                'stop_conditions': ['deadline', 'allowance_exhausted', 'single_attempt', 'scope_failure', 'operator_stop']}

    def count(self, action):
        return sum(a == action for a, _ in self.adapters.calls)

    def completed_order_fixture(self, key, *, amount=100000, completed_at=None):
        """Persist explicit outcome fixtures; creation/delivery is qualified separately."""
        opportunity = self.business.receive(source='explicit-outcome-fixture', external_id=key, service='fixture-prototype',
            scope={'title': 'Independent real-shape fixture order'}, customer_email=key + '@example.invalid')
        customer = self.store.one('SELECT customer_id FROM opportunities WHERE id=?', (opportunity,))['customer_id']
        plan = encode({'action': 'graphics.create', 'arguments': {'title': 'Fixture'}, 'maximum_micro_usd': 100000})
        self.store.db.execute("INSERT INTO quotes(id,opportunity_id,price_micro_usd,cost_micro_usd,turnaround_seconds,expires_at,scope,status,plan) VALUES (?,?,?,100000,600,?,'{}','accepted',?)", (key + '-quote', opportunity, amount, self.now + 600, plan))
        self.store.db.execute("INSERT INTO orders(id,run_id,quote_id,customer_id,service,scope,status,price_micro_usd,cost_micro_usd,created_at,completed_at,plan) VALUES (?,?,?,?,'fixture-prototype','{}','completed',?,100000,?,?,?)",
            (key, self.business.run_id, key + '-quote', customer, amount, self.now, completed_at if completed_at is not None else self.now, plan))
        self.adapters.payment_snapshots[key] = {'run_id': self.business.run_id, 'job_id': key, 'currency': 'USD', 'mode': 'test',
            'revision': 1, 'status': 'settled', 'receipts_micro_usd': amount, 'refunds_micro_usd': 0, 'fees_micro_usd': 1000,
            'earned_micro_usd': amount, 'operating_cost_micro_usd': 20000, 'costs_final': True}
        return key

    def test_source_to_opportunity_preserves_canonical_evidence_and_never_fakes_customer(self):
        discovery = self.discover()
        self.assertEqual(discovery['kind'], 'experiment')
        evidence = json.loads(discovery['evidence'])
        self.assertIn(evidence['excerpt'], self.adapters.source_text)
        self.assertEqual(evidence['retrieved_at'], self.now)
        self.assertEqual(evidence['url'], 'https://market.example/need')
        self.assertFalse(evidence['fixture'])
        opportunity = self.store.one('SELECT * FROM opportunities')
        self.assertEqual(opportunity['status'], 'experiment_proposed')
        self.assertIsNone(opportunity['customer_id'])
        self.assertEqual(self.store.one('SELECT COUNT(*) AS n FROM customers')['n'], 0)
        self.assertEqual(self.store.one('SELECT COUNT(*) AS n FROM orders')['n'], 0)
        self.assertEqual(self.count('research.fetch'), 1)

    def test_dedup_source_freshness_cooldown_and_lifetime_attempt_history(self):
        self.discover()
        scheduled = self.business.schedule_discovery('fixture-market', 'operator duplicate')
        self.assertEqual(scheduled['due_at'], self.now + 300)
        self.drain()
        self.assertEqual(self.count('research.fetch'), 1)
        self.now += 300
        self.drain()
        self.assertEqual(self.count('research.fetch'), 2)
        self.assertEqual(self.store.one('SELECT COUNT(*) AS n FROM discoveries')['n'], 1)
        self.assertEqual(self.store.one('SELECT COUNT(*) AS n FROM opportunities')['n'], 1)
        self.assertEqual(self.store.one('SELECT COUNT(*) AS n FROM discovery_observations')['n'], 2)
        self.store.close()
        self.open()
        self.business.configure_discovery(self.config)
        self.now += 300
        self.drain()
        self.assertEqual(self.count('research.fetch'), 2)
        self.assertEqual(self.business.discovery_status()['markets'][0]['state'], 'attempt_limit')

    def test_stale_future_and_missing_gateway_provenance_cannot_create_opportunity(self):
        for offset, provenance in [(-1000, True), (20, True), (0, False)]:
            with self.subTest(offset=offset, provenance=provenance):
                self.adapters.retrieved_offset, self.adapters.provenance = offset, provenance
                market = dict(self.market, id='case-' + str(offset) + '-' + str(provenance))
                self.business.configure_discovery({**self.config, 'markets': [market]})
                self.drain()
                self.assertEqual(self.store.one('SELECT COUNT(*) AS n FROM discoveries')['n'], 0)
                self.assertEqual(self.store.one('SELECT COUNT(*) AS n FROM opportunities')['n'], 0)

    def test_identifiable_lead_requires_exact_observed_address_and_respects_opt_out(self):
        self.market.update(kind='lead', lead_email='owner@example.com')
        for fake in ['notowner@example.com', 'owner@example.com.evil']:
            self.adapters.source_text = 'We need a graphic. Contact ' + fake
            self.business.configure_discovery(self.config)
            self.drain()
            self.assertEqual(self.store.one('SELECT COUNT(*) AS n FROM customers')['n'], 0)
            self.now += 300
        self.market['max_attempts'] = 3
        self.adapters.source_text = 'We need a graphic. Contact owner@example.com'
        self.business.opt_out('owner@example.com', 'explicit prior customer opt-out')
        self.business.configure_discovery(self.config)
        self.drain()
        discovery = self.store.one('SELECT * FROM discoveries')
        self.assertIsNotNone(discovery)
        self.assertEqual(self.store.one('SELECT status FROM opportunities')['status'], 'needs_review')
        self.assertEqual(self.count('gmail.send'), 0)

    def test_lost_research_response_restart_uses_same_effect_without_repeat_or_freshened_time(self):
        self.adapters.unknown_once = True
        self.business.configure_discovery(self.config)
        self.drain()
        self.assertEqual(self.count('research.fetch'), 1)
        self.assertIsNone(self.store.one('SELECT * FROM discoveries'))
        self.store.close()
        self.open()
        self.now += 300
        self.business.schedule_discovery('fixture-market', 'explicit reconcile wake')
        self.drain()
        self.assertEqual(self.count('research.fetch'), 1)
        evidence = json.loads(self.store.one('SELECT evidence FROM discoveries')['evidence'])
        self.assertEqual(evidence['retrieved_at'], self.now - 300)

    def test_budget_exhaustion_and_insufficient_authoritative_funds_never_dispatch(self):
        self.config['allowance_micro_usd'] = 0
        self.discover()
        self.assertEqual(self.count('reserve'), 0)
        self.assertEqual(self.count('research.fetch'), 0)
        self.assertEqual(self.business.discovery_status()['markets'][0]['state'], 'budget_exhausted')
        self.config['allowance_micro_usd'] = 10000
        self.adapters.funds = 0
        self.business.configure_discovery(self.config)
        self.drain()
        self.assertEqual(self.count('reserve'), 1)
        self.assertEqual(self.count('research.fetch'), 0)
        self.now += 300
        self.drain()
        self.assertEqual(self.count('reserve'), 1)

    def test_single_bounded_experiment_ends_without_repeated_spending(self):
        discovery = self.discover()
        spec = self.spec()
        experiment = self.business.start_experiment(discovery['opportunity_id'], spec, 'explicit owner request')
        self.assertEqual(self.business.start_experiment(discovery['opportunity_id'], spec, 'explicit owner request'), experiment)
        self.drain()
        row = self.store.one('SELECT * FROM discovery_experiments')
        self.assertEqual(row['state'], 'completed')
        result = json.loads(row['result'])
        self.assertTrue(result['prototype_valid'])
        self.assertFalse(result['commercial_success'])
        self.assertEqual(result['measured_contribution_micro_usd'], -21000)
        self.assertEqual(self.count('graphics.create'), 1)
        self.market['max_attempts'] = 1
        self.business.configure_discovery(self.config)
        self.now += 1000
        self.drain()
        self.assertEqual(self.count('graphics.create'), 1)
        self.assertEqual(self.count('research.fetch'), 1)
        self.assertEqual(self.store.one('SELECT COUNT(*) AS n FROM customers')['n'], 0)
        with self.assertRaises(ValueError):
            self.business.start_experiment(discovery['opportunity_id'], self.spec(), 'another request')
        status = self.business.discovery_status()
        self.assertNotIn('content', status['experiments'][0]['artifact'])
        self.assertIn('<svg>', self.business.experiment_artifact(experiment)['content'])

    def test_deadline_and_operator_stop_prevent_queued_prototype_dispatch(self):
        discovery = self.discover()
        experiment = self.business.start_experiment(discovery['opportunity_id'], self.spec(), 'owner request')
        self.business.stop_experiment(experiment, 'operator changed plan')
        self.drain()
        self.assertEqual(self.count('graphics.create'), 0)
        self.now += 700
        self.drain()
        self.assertEqual(self.store.one('SELECT state FROM discovery_experiments')['state'], 'stopped')
        self.assertEqual(self.count('graphics.create'), 0)

    def test_owner_reduction_of_allowance_or_capability_stops_new_dispatch(self):
        discovery = self.discover()
        self.business.start_experiment(discovery['opportunity_id'], self.spec(), 'owner request')
        self.business.configure_discovery({**self.config, 'allowance_micro_usd': 0})
        self.drain()
        self.assertEqual(self.count('graphics.create'), 0)
        self.assertEqual(self.count('reserve'), 1)

    def test_concurrent_policy_change_during_capability_read_is_rechecked(self):
        discovery = self.discover()
        self.business.start_experiment(discovery['opportunity_id'], self.spec(), 'owner request')
        def revoke():
            self.business.configure(replace(self.business.policy, capabilities=(), max_job_micro_usd=0))
        self.adapters.on_available = revoke
        self.drain()
        self.assertEqual(self.count('graphics.create'), 0)

    def test_policy_change_inside_last_capability_check_does_not_dispatch(self):
        discovery = self.discover()
        self.business.start_experiment(discovery['opportunity_id'], self.spec(), 'owner request')
        original, checks = self.adapters.available, []
        def available(action):
            if action == 'graphics.create':
                checks.append(action)
                if len(checks) == 3:
                    self.business.configure(replace(self.business.policy, capabilities=(), max_job_micro_usd=0))
            return original(action)
        self.adapters.available = available
        self.drain()
        self.assertEqual(len(checks), 3)
        self.assertEqual(self.count('graphics.create'), 0)
        effect = self.store.one("SELECT * FROM effects WHERE action='graphics.create'")
        self.assertEqual(effect['state'], 'failed')
        self.assertTrue(json.loads(effect['result'])['not_dispatched'])
        self.assertEqual(self.store.one('SELECT state FROM discovery_experiments')['state'], 'failed')

    def test_experiment_measures_acquisition_and_refunds_without_gross_earned_profit(self):
        discovery = self.discover()
        experiment = self.business.start_experiment(discovery['opportunity_id'], self.spec(), 'owner request')
        self.drain()
        order = self.completed_order_fixture('synthetic-customer-order')
        self.business.link_experiment_order(experiment, order, 'explicit fixture attribution')
        self.business._discovery_observe()
        result = json.loads(self.store.one('SELECT result FROM discovery_experiments')['result'])
        self.assertEqual(result['measured_contribution_micro_usd'], 58000)
        self.assertEqual(result['acquisition_cost_micro_usd'], 1000)
        self.assertTrue(result['commercial_success'])
        self.adapters.payment_snapshots[order].update(revision=2, status='refunded', refunds_micro_usd=100000)
        self.business._discovery_observe()
        result = json.loads(self.store.one('SELECT result FROM discovery_experiments')['result'])
        self.assertFalse(result['commercial_success'])
        self.assertEqual(result['measured_contribution_micro_usd'], -42000)
        self.assertLessEqual(self.business._discovery_priorities()[0]['adaptation_delta'], 0)

    def test_late_conversion_cannot_repair_failed_deadline_success_criteria(self):
        discovery = self.discover()
        experiment = self.business.start_experiment(discovery['opportunity_id'], self.spec(), 'owner request')
        self.drain()
        in_window = self.completed_order_fixture('small-in-window-order', amount=30000)
        self.business.link_experiment_order(experiment, in_window, 'explicit fixture attribution in window')
        self.now += 1000
        late = self.completed_order_fixture('late-large-order', amount=1000000)
        self.business.link_experiment_order(experiment, late, 'explicit fixture attribution after deadline')
        self.business._discovery_observe()
        result = json.loads(self.store.one('SELECT result FROM discovery_experiments')['result'])
        self.assertEqual(result['conversions'], 1)
        self.assertEqual(result['late_or_prior_conversions'], 1)
        self.assertEqual(result['measured_contribution_micro_usd'], -12000)
        self.assertGreater(result['lifetime_contribution_micro_usd'], 0)
        self.assertFalse(result['commercial_success'])

    def test_inherited_budget_is_shared_with_existing_fulfillment_allowances(self):
        self.store.db.execute('INSERT INTO experiments VALUES (?,?,?)', (self.business.run_id, 'explicit prior fixture allocation', 1000000))
        self.discover()
        self.assertEqual(self.count('research.fetch'), 0)
        self.assertEqual(self.business.discovery_status()['markets'][0]['state'], 'budget_exhausted')

    def test_fixture_network_permission_cannot_be_enabled_in_live_mode(self):
        from widemind.business.discovery_policy import configuration
        with self.assertRaises(ValueError):
            configuration({**self.config, 'fixture_mode': True}, 'live')
        with self.assertRaises(ValueError):
            configuration({**self.config, 'fixture_mode': True, 'markets': [{**self.market, 'source_urls': ['http://localhost/private']}]}, 'test')

    def test_unicode_matching_preserves_actual_source_span(self):
        self.adapters.source_text = 'ß' * 500 + ' We need a graphic for the shop.'
        discovery = self.discover()
        self.assertIn('need a graphic', discovery['observed_need'])

    def test_unknown_costs_or_new_unobserved_work_never_improve_learned_priority(self):
        discovery = self.discover()
        experiment = self.business.start_experiment(discovery['opportunity_id'], self.spec(), 'owner request')
        self.drain()
        order = self.completed_order_fixture('fixture-order', amount=2000000)
        self.business.link_experiment_order(experiment, order, 'explicit fixture attribution')
        self.business._discovery_observe()
        positive = self.business._discovery_priorities()[0]
        self.assertEqual(positive['adaptation_delta'], 5)
        unobserved = self.completed_order_fixture('fixture-unobserved-order', amount=2000000)
        self.business.link_experiment_order(experiment, unobserved, 'explicit fixture second attribution')
        self.assertEqual(self.business._discovery_priorities()[0]['adaptation_delta'], 0)
        self.business._discovery_observe()
        self.assertEqual(self.business._discovery_priorities()[0]['adaptation_delta'], 5)
        self.now += 300
        self.adapters.unknown_once = True
        self.drain()
        uncertain = self.business._discovery_priorities()[0]
        self.assertEqual(uncertain['adaptation_delta'], 0)
        self.assertIsNone(uncertain['measured_contribution_micro_usd'])

    def test_unsupported_narrative_stop_condition_is_rejected(self):
        discovery = self.discover()
        spec = self.spec()
        spec['stop_conditions'] = ['give up when it feels hard']
        with self.assertRaises(ValueError):
            self.business.start_experiment(discovery['opportunity_id'], spec, 'owner request')

    def test_response_without_order_is_tracked_without_conversion_or_score(self):
        self.market.update(kind='lead', lead_email='owner@example.com')
        self.adapters.source_text = 'We need a graphic. Contact owner@example.com'
        discovery = self.discover()
        customer = self.store.one('SELECT customer_id FROM opportunities')['customer_id']
        self.store.db.execute("INSERT INTO conversations VALUES (?,?,?,?,?,'inbound','response','explicit synthetic message',?)",
            ('fixture-response', self.business.run_id, customer, 'fixture-thread', 'fixture-message', self.now))
        self.business._discovery_observe()
        outcome = json.loads(self.store.one("SELECT document FROM discovery_outcomes WHERE entity_id=? AND kind='opportunity'", (discovery['opportunity_id'],))['document'])
        self.assertEqual(outcome['responses'], 1)
        self.assertFalse(outcome['conversion'])
        self.assertEqual(self.business._discovery_priorities()[0]['adaptation_delta'], 0)

    def test_intake_crash_window_cannot_offer_before_canonical_evidence(self):
        self.market.update(kind='lead', lead_email='owner@example.com')
        self.adapters.source_text = 'We need a graphic. Contact owner@example.com'
        original = self.business.receive
        observed = []
        def interrupted_window(**kwargs):
            opportunity = original(**kwargs)
            row = self.store.one('SELECT * FROM opportunities WHERE id=?', (opportunity,))
            observed.append(self.business._assess(row))
            self.store.db.execute("UPDATE opportunities SET status='needs_review',assessment=? WHERE id=?", (encode(observed[-1]), opportunity))
            return opportunity
        self.business.receive = interrupted_window
        self.discover()
        self.assertFalse(observed[0]['feasible'])
        self.assertEqual(observed[0]['reason'], 'canonical discovery evidence is missing or stale')
        # The canonical evidence creates a fresh select event. Existing outbound
        # restrictions remain authoritative after the provenance gap is repaired.
        assessment = json.loads(self.store.one('SELECT assessment FROM opportunities')['assessment'])
        self.assertEqual(assessment['reason'], 'outbound disabled')

    def test_observation_is_bounded_and_fair_across_restart_without_rechecking_final_costs(self):
        self.discover()
        reads = []
        original = self.adapters.work_cost
        def observe(**request):
            reads.append(request['reservation_id'])
            return original(**request)
        self.adapters.work_cost = observe
        for index in range(12):
            key = 'explicit-pending-fixture-' + str(index).zfill(2)
            self.adapters.reservations[key] = {'actual_micro_usd': None, 'costs_final': False, 'state': 'unknown'}
            self.store.db.execute("INSERT INTO discovery_attempts(id,run_id,market_id,ordinal,source_url,market_document,state,reservation_id,created_at) VALUES (?,?,?,?,?,?,'unknown',?,?)",
                (key, self.business.run_id, self.market['id'], index + 2, 'https://market.example/pending', encode(self.market), key, self.now))
        self.business._discovery_observe()
        self.assertEqual(len(reads), 5)
        self.store.close()
        self.open()
        self.business._discovery_observe()
        self.assertEqual(len(reads), 10)
        self.business._discovery_observe()
        self.assertEqual(len(reads), 15)
        self.assertEqual(len(set(reads)), 12)
        self.assertNotIn(self.store.one("SELECT reservation_id FROM discovery_attempts WHERE state='completed'")['reservation_id'], reads)

    def test_experiment_does_not_claim_success_while_batched_acquisition_costs_are_missing(self):
        discovery = self.discover()
        experiment = self.business.start_experiment(discovery['opportunity_id'], self.spec(), 'owner request')
        self.drain()
        order = self.completed_order_fixture('synthetic-success-before-new-research')
        self.business.link_experiment_order(experiment, order, 'explicit fixture attribution')
        self.business._discovery_observe()
        self.assertTrue(json.loads(self.store.one('SELECT result FROM discovery_experiments')['result'])['commercial_success'])
        for index in range(6):
            key = 'explicit-unobserved-fixture-' + str(index)
            self.adapters.reservations[key] = {'actual_micro_usd': 1000, 'costs_final': True, 'state': 'settled'}
            self.store.db.execute("INSERT INTO discovery_attempts(id,run_id,market_id,ordinal,source_url,market_document,state,reservation_id,created_at) VALUES (?,?,?,?,?,?,'completed',?,?)",
                (key, self.business.run_id, self.market['id'], index + 2, 'https://market.example/unobserved', encode(self.market), key, self.now))
        self.business._discovery_observe()
        result = json.loads(self.store.one('SELECT result FROM discovery_experiments')['result'])
        self.assertFalse(result['commercial_success'])
        self.assertIsNone(result['measured_contribution_micro_usd'])


if __name__ == '__main__':
    unittest.main()
