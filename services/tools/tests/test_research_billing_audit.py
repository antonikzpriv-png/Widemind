"""Native economy HTTP, synthetic measured AI output; no paid provider calls."""
from dataclasses import asdict
import json
import pytest

from widemind.integrated_service import IntegratedTools
from widemind.tools.contracts import Quote, ToolError, ToolResult, ToolSpec, Usage
from test_research_economy_http import native_economy
from test_integrated_service import Economy, Secrets, action as fixture_action


class OversizedPaidFixture:
    spec = ToolSpec('ai.generate', 'Synthetic known bill with oversized presentation or diagnostics', 'external')

    def __init__(self, *, diagnostics=False):
        self.calls, self.diagnostics = 0, diagnostics

    def availability(self, config, secrets):
        return {'available': True}

    def quote(self, args, config):
        return Quote(10, 'synthetic-audit', True)

    def execute(self, args, ctx):
        self.calls += 1
        ctx.mark_dispatched({'provider': 'synthetic-audit', 'live_calls': 0})
        usage = Usage(7, {'synthetic_tokens': 10}, provider_request_id='fixture-reused-native-trace', provisional=True,
                      details={'blob': 'x' * 4_100_000} if self.diagnostics else {})
        if self.diagnostics:
            raise ToolError('fixture_paid_error', 'Synthetic measured provider failure.', usage=usage)
        return ToolResult({'blob': 'x' * 4_100_000}, usage)


@pytest.mark.parametrize('diagnostics', [False, True], ids=['oversized-paid-result', 'oversized-error-usage'])
def test_known_child_bill_posts_before_presentation_or_usage_metadata_can_overflow(tmp_path, monkeypatch, diagnostics):
    monkeypatch.delenv('WIDEMIND_RUNTIME_DATABASE', raising=False)
    monkeypatch.setenv('WIDEMIND_TOOLS_ACCOUNT', 'dedicated-tools')
    economy, adapter = Economy(), OversizedPaidFixture(diagnostics=diagnostics)
    config = {'control_dir': str(tmp_path / 'control'), 'workspace_dir': str(tmp_path / 'workspace'),
              'policy': {'profile': 'sandbox', 'allow_external_effects': True},
              'tools': {'ai.generate': {'enabled': True}}}
    tools = IntegratedTools(config, economy, adapters=[adapter], secrets=Secrets())
    action = fixture_action('ai.generate', {'prompt': 'Synthetic fixture only.'}, id='billing-diagnostics' if diagnostics else 'billing-output')
    action['limit']['maximum']['micros'] = '10'
    reservation = economy.add(action)
    payload = {'action': action, 'reservation': reservation}
    outcome = tools.execute(payload)
    assert outcome['kind'] == 'unknown'
    assert len(economy.settlements) == 1
    identity, receipt = economy.settlements[0]
    assert identity == reservation['reservationId']
    assert receipt['usage']['actualCharge']['micros'] == '7'
    assert receipt['usage']['provisional'] is True
    assert receipt['usage']['externalEventId'] == action['actionId']
    assert len(json.dumps(receipt).encode()) < 65536
    partial = tools.journal.load(action['actionId'])['partial_usage']
    assert partial['cost_microusd'] == 7
    assert len(json.dumps(partial).encode()) < 65536
    assert economy.rows[identity]['state'] == 'unknown'
    assert tools.execute(payload) == outcome
    assert adapter.calls == 1 and len(economy.settlements) == 1


class StructuredMeasuredFixture:
    spec = ToolSpec('ai.generate', 'Synthetic structured result with known tariff usage', 'external')

    def __init__(self):
        self.calls = 0

    def availability(self, config, secrets):
        return {'available': True}

    def quote(self, args, config):
        return Quote(10, 'synthetic-audit', True, {'max_output_tokens': 100})

    def execute(self, args, ctx):
        self.calls += 1
        value = {'blob': 'x' * 15900}
        text = json.dumps(value)
        assert len(text.encode()) < ctx.config['max_output_bytes']
        usage = Usage(7, {'synthetic_tokens': 10}, provider_request_id='fixture-native-trace', provisional=True)
        ctx.mark_dispatched({'provider': 'synthetic-audit', 'live_calls': 0})
        ctx.save_state({'ai_usage': asdict(usage)})
        # Actual structured AI results include both original text and parsed
        # JSON. Each is bounded, but their combined receipt exceeds the cap.
        return ToolResult({'text': text, 'json': value}, usage)


def test_oversized_child_presentation_posts_known_native_bill_and_preserves_unused_hold(tmp_path, monkeypatch):
    monkeypatch.delenv('WIDEMIND_RUNTIME_DATABASE', raising=False)
    monkeypatch.setenv('WIDEMIND_TOOLS_ACCOUNT', 'dedicated-tools')
    with native_economy(tmp_path, 'billing-audit') as economy:
        parent = economy.request('POST', '/v1/runs/billing-audit/reservations', {
            'action_id': 'billing-parent', 'amount_micro_usd': 100, 'idempotency_key': 'billing-parent',
            'provider': 'research.collect', 'account': 'dedicated-tools',
            'limits': {'max_duration_ms': 10000, 'max_storage_bytes': 20000}})
        economy.start(parent['reservation_id'])
        child = economy.reserve_child(parent['reservation_id'], action_id='billing-child',
            amount_micro_usd=10, idempotency_key='billing-child', provider='ai.generate',
            account='dedicated-tools', limits={'max_duration_ms': 10000, 'max_storage_bytes': 20000})
        adapter = StructuredMeasuredFixture()
        config = {'control_dir': str(tmp_path / 'tools-control'), 'workspace_dir': str(tmp_path / 'workspace'),
                  'policy': {'profile': 'sandbox', 'allow_external_effects': True},
                  'tools': {'ai.generate': {'enabled': True, 'max_output_bytes': 20000}}}
        tools = IntegratedTools(config, economy, adapters=[adapter], secrets=Secrets())
        action = {'version': 1, 'actionId': 'billing-child', 'runId': 'billing-audit', 'tool': 'ai.generate',
                  'workKey': 'billing-child', 'input': {},
                  'limit': {'maximum': {'currency': 'USD', 'micros': '10'}, 'maxDurationMs': 10000, 'maxOutputBytes': 20000}}
        reservation = {'reservationId': child['reservation_id'], 'runId': action['runId'], 'actionId': action['actionId'],
                       'limit': action['limit'], 'authorization': 'native-service-v1-fixture'}
        outcome = tools.execute({'action': action, 'reservation': reservation})
        assert outcome['kind'] == 'unknown'
        economy.mark_unknown(child['reservation_id'], action['actionId'])
        measured = economy.reservation(child['reservation_id'])
        assert measured['state'] == 'unknown'
        assert measured['provisional_micro_usd'] == 7
        balance = economy.request('GET', '/v1/runs/billing-audit/balance')
        assert balance['posted_micro_usd'] == 5_000_000 - 7
        assert balance['reserved_micro_usd'] == 100 - 7
        assert balance['available_micro_usd'] == 5_000_000 - 100
        assert tools.execute({'action': action, 'reservation': reservation}) == outcome
        assert adapter.calls == 1
