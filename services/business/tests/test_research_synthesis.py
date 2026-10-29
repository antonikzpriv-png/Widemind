"""Research delivery and staged recovery contracts, using explicit test fixtures."""
import unittest
import hashlib
import json
import test_business as original
from widemind.business.contracts import Outcome
from widemind.business.engine import Deferred
from widemind.business.host_clients import ToolsClient
from widemind.business.services import ResearchBrief
from widemind.demo import drain, synthetic_research
SCOPE = {'topic': 'SQLite for a small customer application', 'sections': ['Overview', 'Risks'], 'min_sources': 2}

def rehash(result):
    content = result['document']['content']
    result['document']['artifact'].update(sha256=hashlib.sha256(content.encode()).hexdigest(), size_bytes=len(content.encode()))
    return result

def pending_research(harness):
    h = harness
    job = h.order()
    original_execute, original_reconcile = (h.adapters.execute, h.adapters.reconcile)
    calls = []
    gate = {'ready': False}

    def execute(**request):
        if request['action'] == 'research.collect':
            calls.append(request['action_id'])
            if len(calls) == 1:
                return Outcome('unknown', reason='a paid child outcome is unresolved')
        return original_execute(**request)

    def reconcile(*, action_id):
        if calls and action_id == calls[0]:
            saved = original_reconcile(action_id=action_id)
            if saved.state != 'unknown':
                return saved
            if gate['ready']:
                return Outcome('unknown', {'workflow_resumable': True, 'progress_key': 'verified-child-stage-1'})
            return Outcome('unknown', reason='unknown child must be observed first')
        return original_reconcile(action_id=action_id)
    h.adapters.execute, h.adapters.reconcile = (execute, reconcile)
    h.paid(job)
    drain(h.business)
    effect = h.store.one("SELECT * FROM effects WHERE action='research.collect'")
    assert h.business.order(job)['status'] == 'funded' and effect['state'] == 'unknown'
    return (job, effect, calls, gate)

class ResearchSynthesisTests(unittest.TestCase):

    def setUp(self):
        self.harness = original.BusinessTests()
        self.harness.setUp()

    def tearDown(self):
        self.harness.tearDown()

    def test_customer_constraints_survive_plan_without_gaining_budget_authority(self):
        scope = {**SCOPE, 'context': 'A Dublin school board with limited technical staff', 'requirements': ['Explain backup recovery risk', 'Identify unknown operating costs'], 'as_of': '2026-10-29', 'max_age_days': 90, 'policy': {'allow_paid': True}, 'price_micro_usd': 1, 'maximum_micro_usd': 999999999}
        plan = ResearchBrief().plan(scope, 250000)
        for key in ('context', 'requirements', 'as_of', 'max_age_days'):
            self.assertEqual(plan.arguments[key], scope[key])
        self.assertEqual(plan.maximum_micro_usd, 250000)
        self.assertTrue(not {'policy', 'price_micro_usd', 'maximum_micro_usd'} & plan.arguments.keys())

    def test_invalid_customer_constraints_fail_before_offer(self):
        for changes in [{'context': []}, {'requirements': 'bad'}, {'requirements': [None]}, {'as_of': '2026-02-30'}, {'as_of': '2026-10-29\n'}, {'max_age_days': True}, {'max_age_days': 0}, {'max_age_days': 3651}]:
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    ResearchBrief().plan({**SCOPE, **changes}, 1000)

    def test_delivery_uses_exact_downloadable_markdown_and_strips_private_evidence(self):
        result = synthetic_research(SCOPE)
        result['document']['artifact']['private_evidence_manifest'] = {'private': 'never forwarded'}
        delivered = ResearchBrief().deliverable(SCOPE, result)
        self.assertEqual(delivered['content'], result['document']['content'])
        self.assertEqual(delivered['filename'], 'research-brief.md')
        self.assertNotIn('private_evidence_manifest', json.dumps(delivered))
        self.assertTrue(all(('excerpt' not in source for source in delivered['sources'])))
        self.assertNotIn('Excerpt:', delivered['content'])

    def test_claimed_positive_checks_do_not_bypass_real_delivery_validation(self):
        for mutation in [lambda result: result.pop('synthesis_version'), lambda result: result.update(synthesis_version=True), lambda result: result.update(synthesis_version=1), lambda result: result.update(quality={'passed': False}), lambda result: result.update(checks={'unverified': 'true'}), lambda result: result.update(checks={'failed': 0}), lambda result: result['document'].update(content='No scoped research document'), lambda result: result['document']['artifact'].update(sha256='0' * 64), lambda result: result['sections'][0].update(source_urls=['https://unknown.example/source']), lambda result: result['sections'][1].update(text=result['sections'][0]['text'])]:
            with self.subTest(mutation=mutation):
                result = synthetic_research(SCOPE)
                mutation(result)
                with self.assertRaises(ValueError):
                    ResearchBrief().deliverable(SCOPE, result)

    def test_excerpt_dump_and_filler_are_rejected_even_with_claimed_quality(self):
        result = synthetic_research(SCOPE)
        result['sections'][0]['text'] = 'Source excerpts (not an inferred synthesis): ' + ' '.join((source['excerpt'] for source in result['sources']))
        with self.assertRaises(ValueError):
            ResearchBrief().deliverable(SCOPE, result)
        result = synthetic_research(SCOPE)
        result['sections'][0]['text'] = 'This is a placeholder for the customer overview. ' * 5
        with self.assertRaises(ValueError):
            ResearchBrief().deliverable(SCOPE, result)
        result = synthetic_research(SCOPE)
        result['sources'][0]['excerpt'] = 'The original source has a long body containing implementation details that must remain private evidence rather than an appended customer research section.'
        result['document']['content'] += '\nSource excerpt: ' + result['sources'][0]['excerpt']
        with self.assertRaises(ValueError):
            ResearchBrief().deliverable(SCOPE, rehash(result))

    def test_section_specificity_and_document_citations_are_checked_independently(self):
        scope = {**SCOPE, 'sections': ['Pricing']}
        result = synthetic_research(scope)
        result['sections'][0]['text'] = 'SQLite stores application records in a local file with explicit transactions. A small team should document operational ownership and keep a reversible migration approach before adopting the technology.'
        with self.assertRaises(ValueError):
            ResearchBrief().deliverable(scope, result)
        result = synthetic_research(SCOPE)
        result['document']['content'] = result['document']['content'].replace(result['sources'][0]['url'], 'missing-source')
        with self.assertRaises(ValueError):
            ResearchBrief().deliverable(SCOPE, rehash(result))

    def test_free_reconciliation_queues_once_then_next_authorized_tick_continues_original_parent(self):
        h = self.harness
        job, effect, calls, gate = pending_research(h)
        gate['ready'] = True
        h.business._reconcile(effect['id'])
        h.business._reconcile(effect['id'])
        self.assertEqual(len(calls), 1)
        queued = h.store.all("SELECT * FROM events WHERE kind='advance' AND external_id LIKE '%workflow:%'")
        self.assertTrue(len(queued) == 1 and queued[0]['state'] == 'queued')
        self.assertIs(h.business.tick(), True)
        self.assertEqual(calls, [effect['id'], effect['id']])
        drain(h.business)
        self.assertEqual(h.business.order(job)['status'], 'completed')

    def test_unresolved_children_never_resume_parent_execution(self):
        h = self.harness
        job, effect, calls, gate = pending_research(h)
        h.business._reconcile(effect['id'])
        self.assertEqual(h.store.all("SELECT * FROM events WHERE kind='advance' AND external_id LIKE '%workflow:%'"), [])
        h.wake(job)
        drain(h.business)
        self.assertEqual(len(calls), 1)
        self.assertEqual(h.business.order(job)['status'], 'funded')

    def test_confirmed_completed_research_wakes_order_and_reuses_saved_receipt(self):
        h = self.harness
        job, effect, calls, gate = pending_research(h)
        h.adapters.save(effect['id'], Outcome('succeeded', synthetic_research(h.scope)))
        h.business._reconcile(effect['id'])
        h.business._reconcile(effect['id'])
        queued = h.store.all("SELECT * FROM events WHERE kind='advance' AND external_id LIKE '%completed:%'")
        self.assertEqual(len(queued), 1)
        self.assertEqual(len(calls), 1, 'Observation must never replay generation')
        self.assertTrue(h.business.tick())
        self.assertEqual(h.business.order(job)['status'], 'ready')
        self.assertEqual(len(calls), 1)
        drain(h.business)
        h.business._reconcile(effect['id'])
        self.assertEqual(len(h.store.all("SELECT * FROM events WHERE kind='advance' AND external_id LIKE '%completed:%'")), 1)

    def test_stopped_runtime_can_observe_progress_but_cannot_execute_queued_resume(self):
        h = self.harness
        job, effect, calls, gate = pending_research(h)
        h.adapters.set('state', 'stopped')
        gate['ready'] = True
        h.business._reconcile(effect['id'])
        self.assertEqual(len(calls), 1)
        self.assertIs(h.business.tick(), False)
        self.assertEqual(h.business.order(job)['status'], 'funded')
        h.adapters.set('state', 'active')
        self.assertIs(h.business.tick(), True)
        self.assertEqual(calls, [effect['id'], effect['id']])

    def test_explicit_reconciliation_effect_never_executes_resumable_parent(self):
        h = self.harness
        job, effect, calls, gate = pending_research(h)
        gate['ready'] = True
        with self.assertRaises(Deferred):
            h.business._effect(job, 'create', 'tools', 'research.collect', json.loads(effect['request']), lambda aid: self.fail('free reconciliation dispatched paid work'), reconciliation=True)
        self.assertEqual(len(calls), 1)

    def test_tools_client_preserves_only_outcome_status_and_unknown_progress_data(self):
        progress = {'workflow_resumable': True, 'progress_key': 'verified-stage-1'}
        for payload in ({'state': 'unknown', 'data': progress}, {'kind': 'unknown', 'data': progress}):
            result = ToolsClient.outcome(payload)
            self.assertTrue(result.state == 'unknown' and result.data == progress)
        result = ToolsClient.outcome({'kind': 'receipt', 'receipt': {'status': 'uncertain', 'result': progress}})
        self.assertTrue(result.state == 'unknown' and result.data == progress)
