"""Research recovery routing with explicit synthetic authorization/tool fixtures.

The real business state machine and ResearchBrief validator run here. Model,
provider and financial outcomes are fixtures, not live execution evidence.
"""
import copy
import json
import unittest

import test_discovery as fixtures
from widemind.business.contracts import Outcome
from widemind.business.services import ResearchBrief
from widemind.demo import synthetic_research


SCOPE = {'topic': 'SQLite for a small customer application',
         'sections': ['Overview', 'Risks'], 'min_sources': 2}


class ResearchDiscoveryRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.DiscoveryTests()
        self.f.setUp()
        self.addCleanup(self.f.tearDown)
        self.f.business.services = {'research-brief': ResearchBrief()}
        self.f.market.update(service='research-brief', scope=SCOPE,
                             need_terms=['need a research brief'])
        self.f.adapters.source_text = 'An explicit synthetic market says: we need a research brief.'
        self.discovery = self.f.discover()
        self.assertIsNotNone(self.discovery)
        self.calls = []
        self.gate = {'ready': False, 'completed': False}
        original_execute = self.f.adapters.execute
        original_reconcile = self.f.adapters.reconcile

        def completed(action_id):
            outcome = Outcome('succeeded', synthetic_research(SCOPE))
            self.f.adapters.results[action_id] = outcome
            return outcome

        def execute(**request):
            if request['action'] != 'research.collect':
                return original_execute(**request)
            self.calls.append(copy.deepcopy(request))
            self.f.adapters.calls.append((request['action'], request['action_id']))
            reservation = self.f.adapters.reservations[request['reservation_id']]
            self.assertEqual(reservation['request']['tool_action_id'], request['action_id'])
            self.assertEqual(reservation['request']['tool'], 'research.collect')
            if len(self.calls) == 1:
                return Outcome('unknown', reason='Synthetic child outcome requires observation')
            self.assertTrue(self.gate['ready'], 'Unknown child must prevent execution continuation')
            return completed(request['action_id'])

        def reconcile(*, action_id):
            if self.calls and action_id == self.calls[0]['action_id']:
                if self.gate['completed']:
                    return completed(action_id)
                if self.gate['ready']:
                    return Outcome('unknown', {'workflow_resumable': True,
                                               'progress_key': 'fixture-observed-children-v1'})
                return Outcome('unknown', reason='Synthetic child is still uncertain')
            return original_reconcile(action_id=action_id)

        self.f.adapters.execute = execute
        self.f.adapters.reconcile = reconcile
        self.experiment = self.f.business.start_experiment(
            self.discovery['opportunity_id'], self.f.spec(), 'explicit-fixture-research-prototype')
        self.f.drain()
        self.effect = self.f.store.one("SELECT * FROM effects WHERE action='research.collect'")
        self.assertEqual(self.row()['state'], 'unknown')
        self.assertEqual(len(self.calls), 1)

    def row(self):
        return self.f.store.one('SELECT * FROM discovery_experiments WHERE id=?', (self.experiment,))

    def recovery_events(self):
        return self.f.store.all("SELECT * FROM events WHERE external_id LIKE '%workflow:%' OR external_id LIKE '%completed:%'")

    def recover(self):
        self.f.business._reconcile(self.effect['id'])
        self.f.business._reconcile(self.effect['id'])

    def test_resumable_prototype_routes_to_experiment_and_retains_original_authorization(self):
        self.gate['ready'] = True
        self.recover()
        events = self.recovery_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['kind'], 'experiment.step')
        self.assertEqual(json.loads(events[0]['payload'])['id'], self.experiment)
        self.assertEqual(len(self.calls), 1, 'Free reconciliation must not dispatch')
        self.assertTrue(self.f.business.tick())
        self.assertEqual(self.row()['state'], 'completed')
        self.assertEqual(self.calls[0], self.calls[1], 'Continuation must preserve action, reservation and scope')
        self.assertEqual(self.f.count('reserve'), 2, 'Only source collection and original prototype were authorized')
        self.assertEqual(self.f.store.all('SELECT * FROM orders'), [])
        self.assertEqual(self.f.business.experiment_artifact(self.experiment)['media_type'], 'text/markdown')

    def test_unknown_children_do_not_enqueue_or_execute_a_continuation(self):
        self.recover()
        self.assertEqual(self.recovery_events(), [])
        self.f.business.notify(source='explicit-fixture-wake', external_id='uncertain-prototype',
                               kind='experiment.step', payload={'id': self.experiment})
        self.f.drain()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.row()['state'], 'unknown')

    def test_runtime_stop_blocks_queued_continuation_until_normal_resume(self):
        self.gate['ready'] = True
        self.f.adapters.run_state = 'stopped'
        self.recover()
        self.assertEqual(len(self.recovery_events()), 1)
        self.assertFalse(self.f.business.tick())
        self.assertEqual(len(self.calls), 1)
        self.f.adapters.run_state = 'active'
        self.assertTrue(self.f.business.tick())
        self.assertEqual(self.row()['state'], 'completed')
        self.assertEqual(self.calls[0], self.calls[1])

    def test_operator_stop_after_recovery_blocks_prototype_dispatch(self):
        self.gate['ready'] = True
        self.recover()
        self.f.business.stop_experiment(self.experiment, 'Explicit fixture operator stop')
        self.f.drain()
        self.assertEqual(self.row()['state'], 'stopped')
        self.assertEqual(len(self.calls), 1)
        count = len(self.recovery_events())
        self.gate['completed'] = True
        self.recover()
        self.assertEqual(len(self.recovery_events()), count, 'Stopped owner cannot be rescheduled')

    def test_expired_owner_is_not_scheduled_and_queued_deadline_is_rechecked(self):
        self.gate['ready'] = True
        self.recover()
        count = len(self.recovery_events())
        self.f.now += 601
        self.recover()
        self.assertEqual(len(self.recovery_events()), count)
        self.f.drain()
        self.assertEqual(self.row()['state'], 'expired')
        self.assertEqual(len(self.calls), 1)

    def test_confirmed_completed_research_wakes_artifact_validation_without_provider_replay(self):
        self.gate['completed'] = True
        self.recover()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(len(self.recovery_events()), 1)
        self.assertEqual(self.recovery_events()[0]['kind'], 'experiment.step')
        self.assertTrue(self.f.business.tick())
        self.assertEqual(self.row()['state'], 'completed')
        self.assertEqual(len(self.calls), 1, 'A confirmed receipt must be reused')
        self.assertEqual(self.f.business.experiment_artifact(self.experiment)['media_type'], 'text/markdown')
        self.recover()
        self.assertEqual(len(self.recovery_events()), 1, 'Completed owner cannot be rescheduled')


if __name__ == '__main__':
    unittest.main()
