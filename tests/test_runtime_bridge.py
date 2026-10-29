"""Actual runtime RPC/accounting integration, with synthetic trusted tool receipts."""
from __future__ import annotations

import copy
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from widemind.economy.app import create_app
from widemind.economy.config import Settings
from widemind.economy.runtime import RuntimeBridge
from widemind.economy.service import EconomyService


@pytest.fixture
def setup(tmp_path):
    settings = Settings(str(tmp_path / 'runtime.sqlite3'), 'operator-'+'o'*48, 'gateway-'+'g'*48,
                        'agent-'+'a'*48, 'run', runtime_auth_secret='proof-'+'p'*48,
                        runtime_tool_profiles={'fixture.tool': {'provider': 'fixture-provider', 'account': 'fixture-account', 'mode': 'sandbox'},
                                               'live.tool': {'provider': 'live-provider', 'account': 'live-account', 'mode': 'live'}})
    app = create_app(settings)
    with TestClient(app) as client:
        yield settings, app.state.economy, client


def headers(settings, role='gateway'):
    return {'Authorization': 'Bearer '+getattr(settings, role+'_token')}


def post(setup, route, body, role='gateway'):
    settings, service, client = setup
    response = client.post('/v1/runtime/'+route, json=body, headers=headers(settings, role))
    assert response.status_code == 200, response.text
    return response.json()


def seed(setup):
    return post(setup, 'seed', {'runId': 'run', 'mode': 'sandbox'}, 'operator')


def request(action='a', maximum='1000000'):
    return {'runId': 'run', 'actionId': action, 'tool': 'fixture.tool',
            'limit': {'maximum': {'currency': 'USD', 'micros': maximum}, 'maxDurationMs': 1000, 'maxOutputBytes': 1024, 'maxTokens': 10}}


def action(req):
    return {**copy.deepcopy(req), 'version': 1, 'workKey': 'work-'+req['actionId'], 'input': {'fixture': True}}


def receipt(reservation, cost='250000', *, provisional=False, failed=False, event='charge-1'):
    return {'version': 1, 'actionId': reservation['actionId'], 'status': 'failed' if failed else 'succeeded',
            'result': {'fixture': True}, 'usage': {'actualCharge': {'currency': 'USD', 'micros': cost},
            'provider': 'fixture-provider', 'accountRef': 'fixture-account', 'externalEventId': event,
            'billableFailure': failed, 'provisional': provisional, 'evidenceRef': 'fixture-evidence'}}


def test_unfunded_summary_never_creates_credit_and_seed_operator_only(setup):
    settings, service, client = setup
    assert post(setup, 'summary', {'runId': 'run'}) is None
    assert post(setup, 'authorize', request())['kind'] == 'unavailable'
    assert client.post('/v1/runtime/seed', headers=headers(settings), json={'runId': 'run', 'mode': 'sandbox'}).status_code == 403
    assert seed(setup)['openingCreditMicros'] == '5000000'
    assert seed(setup)['postedMicros'] == '5000000'
    assert len(service.ledger('run')) == 1
    assert client.post('/v1/runtime/seed', headers=headers(settings, 'operator'), json={'runId': 'run', 'mode': 'live'}).status_code == 409


def test_exact_binding_replay_verification_dispatch_and_charged_failure(setup):
    settings, service, client = setup
    seed(setup)
    req = request()
    result = post(setup, 'authorize', req)
    reservation = result['reservation']
    assert result['kind'] == 'authorized'
    assert post(setup, 'authorize', req) == result
    for field, value in [('tool', 'live.tool'), ('limit', request(maximum='2000000')['limit'])]:
        assert post(setup, 'authorize', {**req, field: value})['kind'] == 'unavailable'
    proof = {'reservation': reservation, 'action': action(req), 'dispatch': True}
    verified = post(setup, 'verify', proof)
    assert verified == {'kind': 'verified', 'profile': {'provider': 'fixture-provider', 'account': 'fixture-account', 'mode': 'sandbox'}}
    assert post(setup, 'verify', proof)['kind'] == 'unavailable'
    assert post(setup, 'authorize', req)['kind'] == 'unavailable'
    assert post(setup, 'verify', {**proof, 'dispatch': False})['kind'] == 'verified'
    charge = receipt(reservation, failed=True)
    assert post(setup, 'settle', {'reservation': reservation, 'receipt': charge}) == {'kind': 'settled'}
    assert post(setup, 'settle', {'reservation': reservation, 'receipt': charge}) == {'kind': 'settled'}
    summary = post(setup, 'summary', {'runId': 'run'})
    assert summary['postedMicros'] == '4750000' and summary['reservedMicros'] == '0'
    assert summary['operatingChargesMicros'] == '250000'
    assert post(setup, 'authorize', req)['kind'] == 'unavailable'
    assert service.charges('run')[0]['outcome'] == 'failed'


@pytest.mark.parametrize('mutation', ['authorization', 'unicode-proof', 'runId', 'actionId', 'reservationId', 'limit', 'tool'])
def test_forged_or_rebound_proofs_fail_without_dispatch_or_charge(setup, mutation):
    settings, service, client = setup
    seed(setup)
    req = request()
    reservation = post(setup, 'authorize', req)['reservation']
    proof = {'reservation': copy.deepcopy(reservation), 'action': action(req), 'dispatch': True}
    if mutation == 'authorization':
        proof['reservation']['authorization'] = 'wm1.'+'0'*64
    elif mutation == 'unicode-proof':
        proof['reservation']['authorization'] = 'é'*40
    elif mutation in {'runId', 'actionId', 'reservationId'}:
        proof['reservation'][mutation] = 'forged'
    elif mutation == 'limit':
        proof['reservation']['limit']['maximum']['micros'] = '2000000'
    else:
        proof['action']['tool'] = 'live.tool'
    assert post(setup, 'verify', proof)['kind'] == 'unavailable'
    assert service.reservation(reservation['reservationId'])['state'] == 'reserved'
    assert service.balance('run')['posted_micro_usd'] == 5_000_000


def test_provisional_cost_remains_reserved_then_final_trueup_after_restart(setup):
    settings, service, client = setup
    seed(setup)
    req = request()
    reservation = post(setup, 'authorize', req)['reservation']
    assert post(setup, 'verify', {'reservation': reservation, 'action': action(req), 'dispatch': True})['kind'] == 'verified'
    subtotal = receipt(reservation, cost='400000', provisional=True)
    assert post(setup, 'settle', {'reservation': reservation, 'receipt': subtotal})['kind'] == 'unavailable'
    assert service.balance('run')['posted_micro_usd'] == 4_600_000
    assert service.balance('run')['reserved_micro_usd'] == 600_000
    with TestClient(create_app(settings)) as restarted:
        final = receipt(reservation, cost='700000', failed=True, event='final-event-2')
        response = restarted.post('/v1/runtime/settle', headers=headers(settings), json={'reservation': reservation, 'receipt': final})
        assert response.json() == {'kind': 'settled'}
    assert service.balance('run')['posted_micro_usd'] == 4_300_000
    assert service.balance('run')['reserved_micro_usd'] == 0


def test_free_actions_have_native_zero_cap_and_unexpected_charge_records_liability(setup):
    settings, service, client = setup
    seed(setup)
    service.set_hold('run', 'refund-hold', 6_000_000, 'refund', 'refund-exposure', 'hold')
    req = request(maximum='0')
    reservation = post(setup, 'authorize', req)['reservation']
    assert reservation['limit']['maximum']['micros'] == '0'
    assert service.reservation(reservation['reservationId'])['amount_micro_usd'] == 0
    assert post(setup, 'verify', {'reservation': reservation, 'action': action(req), 'dispatch': True})['kind'] == 'verified'
    assert post(setup, 'settle', {'reservation': reservation, 'receipt': receipt(reservation, cost='10')}) == {'kind': 'settled'}
    assert service.balance('run')['posted_micro_usd'] == 4_999_990
    assert post(setup, 'authorize', request(action='paid', maximum='1'))['kind'] == 'insufficient'


def test_profile_account_and_mode_are_enforced_independently_of_receipt_claims(setup):
    settings, service, client = setup
    seed(setup)
    assert post(setup, 'authorize', {**request(), 'tool': 'unmapped'})['kind'] == 'unavailable'
    assert post(setup, 'authorize', {**request(), 'tool': 'live.tool'})['kind'] == 'unavailable'
    reservation = post(setup, 'authorize', request())['reservation']
    for field, value in [('provider', 'other-provider'), ('accountRef', 'other-account')]:
        invalid = receipt(reservation)
        invalid['usage'][field] = value
        assert post(setup, 'settle', {'reservation': reservation, 'receipt': invalid})['kind'] == 'unavailable'
    assert service.balance('run')['posted_micro_usd'] == 5_000_000
    changed = RuntimeBridge(service, secret=settings.runtime_auth_secret,
                            profiles={'fixture.tool': {'provider': 'fixture-provider', 'account': 'changed', 'mode': 'sandbox'}})
    from widemind.economy.runtime import AuthorizationRequest
    assert changed.authorize(AuthorizationRequest.model_validate(request()))['kind'] == 'unavailable'


@pytest.mark.parametrize('value', [0, 1.0, True, None, '-1', '+1', '01', '1.0', ' 1', '9223372036854775808', '9'*129])
def test_wire_money_rejects_numbers_and_noncanonical_decimal_strings(setup, value):
    settings, service, client = setup
    seed(setup)
    req = request()
    req['limit']['maximum']['micros'] = value
    assert client.post('/v1/runtime/authorize', headers=headers(settings), json=req).status_code == 422
    assert service.reservations('run') == []


def test_above_js_safe_integer_and_aggregate_over_int64_remain_exact(setup):
    settings, service, client = setup
    seed(setup)
    cap = 9_007_199_254_740_993
    service.record_funding('run', cap, 'owner_funding', 'fund', 'fund-1')
    reservation = post(setup, 'authorize', request(maximum=str(cap)))['reservation']
    assert reservation['limit']['maximum']['micros'] == str(cap)
    assert service.reservation(reservation['reservationId'])['amount_micro_usd'] == cap
    assert post(setup, 'settle', {'reservation': reservation, 'receipt': receipt(reservation, cost=str(cap))}) == {'kind': 'settled'}
    for index in range(2):
        service.record_funding('run', 9_223_372_036_854_775_807, 'owner_funding', 'fund', f'huge-{index}')
    summary = post(setup, 'summary', {'runId': 'run'})
    assert summary['postedMicros'] == str(5_000_000 + 2*9_223_372_036_854_775_807)
    assert all(isinstance(value, str) for key, value in summary.items() if key.endswith('Micros'))


def test_concurrent_authorization_caps_are_atomic_and_same_action_is_single_identity(setup):
    settings, service, client = setup
    seed(setup)
    bridge = client.app.state.runtime_bridge
    from widemind.economy.runtime import AuthorizationRequest
    def reserve(index):
        return bridge.authorize(AuthorizationRequest.model_validate(request(action=f'concurrent-{index}', maximum='3000000')))
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(reserve, range(8)))
    assert sum(result['kind'] == 'authorized' for result in results) == 1
    assert service.balance('run')['reserved_micro_usd'] == 3_000_000
    free = AuthorizationRequest.model_validate(request(action='free-same', maximum='0'))
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: bridge.authorize(free), range(8)))
    assert len({result['reservation']['reservationId'] for result in results}) == 1
    assert len({result['reservation']['authorization'] for result in results}) == 1


def test_optional_diagnostics_cannot_drop_a_verified_charge_and_native_evidence_preserved(setup):
    settings, service, client = setup
    seed(setup)
    reservation = post(setup, 'authorize', request())['reservation']
    charge = receipt(reservation)
    charge['result'] = {'large': 'x'*200_000}
    charge['externalRef'] = {'large': 'x'*200_000}
    charge['usage']['nativeAmount'] = {'currency': 'EUR', 'minorUnits': '23', 'conversionRef': 'verified-fx-record', 'rounding': 'ceiling to micro-USD'}
    assert post(setup, 'settle', {'reservation': reservation, 'receipt': charge}) == {'kind': 'settled'}
    stored = service.charges('run')[0]['usage']
    assert stored['nativeAmount'] == charge['usage']['nativeAmount']
    assert 'large' not in json.dumps(stored)
    assert service.balance('run')['posted_micro_usd'] == 4_750_000


def test_runtime_routes_reject_agent_privilege_and_configuration_partial(setup):
    settings, service, client = setup
    for route, body in [('summary', {'runId': 'run'}), ('authorize', request()), ('seed', {'runId': 'run', 'mode': 'sandbox'})]:
        assert client.post('/v1/runtime/'+route, headers=headers(settings, 'agent'), json=body).status_code == 403
    for values in ({'runtime_auth_secret': None}, {'runtime_tool_profiles': {}}, {'runtime_auth_secret': settings.gateway_token}):
        with pytest.raises(ValueError):
            replace(settings, **values)
    disabled = replace(settings, runtime_auth_secret=None, runtime_tool_profiles={})
    with TestClient(create_app(disabled)) as unconfigured:
        assert unconfigured.post('/v1/runtime/summary', headers=headers(settings), json={'runId': 'run'}).status_code == 503


def test_provider_event_ids_cannot_cross_actions_and_late_provisional_never_redebits(setup):
    settings, service, client = setup
    seed(setup)
    first = post(setup, 'authorize', request(action='first'))['reservation']
    partial = receipt(first, cost='100000', provisional=True, event='provider-subtotal')
    assert post(setup, 'settle', {'reservation': first, 'receipt': partial})['kind'] == 'unavailable'
    final = receipt(first, cost='200000', event='provider-final')
    assert post(setup, 'settle', {'reservation': first, 'receipt': final}) == {'kind': 'settled'}
    assert post(setup, 'settle', {'reservation': first, 'receipt': partial})['kind'] == 'unavailable'
    second = post(setup, 'authorize', request(action='second'))['reservation']
    reused = receipt(second, cost='200000', event='provider-final')
    assert post(setup, 'settle', {'reservation': second, 'receipt': reused})['kind'] == 'unavailable'
    assert service.balance('run')['posted_micro_usd'] == 4800000
    assert len(service.charges('run')) == 1


def test_atomic_rpc_receipt_admission_failure_rolls_back_charge(setup):
    settings, service, client = setup
    seed(setup)
    reservation = post(setup, 'authorize', request())['reservation']
    with service._tx() as db:
        db.execute("CREATE TRIGGER fail_receipt BEFORE INSERT ON runtime_receipts BEGIN SELECT RAISE(ABORT,'test interruption'); END;")
    with pytest.raises(sqlite3.IntegrityError):
        post(setup, 'settle', {'reservation': reservation, 'receipt': receipt(reservation)})
    assert service.balance('run')['posted_micro_usd'] == 5000000
    assert service.balance('run')['reserved_micro_usd'] == 1000000
    assert service.charges('run') == []
    with service._tx() as db:
        db.execute('DROP TRIGGER fail_receipt')
    assert post(setup, 'settle', {'reservation': reservation, 'receipt': receipt(reservation)}) == {'kind': 'settled'}


def test_not_performed_recovery_is_scoped_audited_and_bounded(setup):
    settings, service, client = setup
    seed(setup)
    req = request()
    reservation = post(setup, 'authorize', req)['reservation']
    verify = {'reservation': reservation, 'action': action(req), 'dispatch': True}
    body = {'reservation': reservation, 'evidenceRef': 'provider-definitively-not-performed-1'}
    assert post(setup, 'not-performed', body)['kind'] == 'unavailable'
    assert post(setup, 'verify', verify)['kind'] == 'verified'
    assert post(setup, 'not-performed', body) == {'kind': 'renewed'}
    assert post(setup, 'not-performed', body) == {'kind': 'renewed'}
    assert post(setup, 'authorize', req)['reservation'] == reservation
    assert post(setup, 'verify', verify)['kind'] == 'verified'
    assert post(setup, 'not-performed', body)['kind'] == 'unavailable'  # Cannot replay old evidence onto attempt 2.
    assert post(setup, 'not-performed', {**body, 'evidenceRef': 'provider-definitively-not-performed-2'}) == {'kind': 'renewed'}
    assert post(setup, 'verify', verify)['kind'] == 'verified'
    final_body = {**body, 'evidenceRef': 'provider-definitively-not-performed-3'}
    assert post(setup, 'not-performed', final_body) == {'kind': 'closed'}
    assert post(setup, 'not-performed', final_body) == {'kind': 'closed'}
    assert post(setup, 'verify', verify)['kind'] == 'unavailable'
    assert service.balance('run')['reserved_micro_usd'] == 0
    assert service.balance('run')['posted_micro_usd'] == 5_000_000
    from widemind.economy.reporting import audit_integrity
    assert audit_integrity(service, 'run')['ok']
    with service._read() as db:
        assert db.execute('SELECT COUNT(*) FROM runtime_dispatches').fetchone()[0] == 3
        assert db.execute('SELECT COUNT(*) FROM runtime_rearms').fetchone()[0] == 3


@pytest.mark.parametrize('subtotal', ['0', '100000'])
def test_not_performed_cannot_erase_provisional_usage_even_zero(setup, subtotal):
    settings, service, client = setup
    seed(setup)
    req = request()
    reservation = post(setup, 'authorize', req)['reservation']
    assert post(setup, 'verify', {'reservation': reservation, 'action': action(req), 'dispatch': True})['kind'] == 'verified'
    assert post(setup, 'settle', {'reservation': reservation, 'receipt': receipt(reservation, cost=subtotal, provisional=True)})['kind'] == 'unavailable'
    assert post(setup, 'not-performed', {'reservation': reservation, 'evidenceRef': 'contradictory-claim'})['kind'] == 'unavailable'
    assert service.reservation(reservation['reservationId'])['state'] == 'unknown'


def test_provider_event_reference_uses_full_foundation_2000_character_contract(setup):
    seed(setup)
    reservation = post(setup, 'authorize', request())['reservation']
    charge = receipt(reservation, event='provider-event-'+'x'*1985)
    assert len(charge['usage']['externalEventId']) == 2000
    assert post(setup, 'settle', {'reservation': reservation, 'receipt': charge}) == {'kind': 'settled'}
    assert post(setup, 'settle', {'reservation': reservation, 'receipt': charge}) == {'kind': 'settled'}


@pytest.mark.parametrize('change', ['account', 'provider', 'mode', 'removed'])
def test_retired_profiles_cannot_hide_known_old_liability_or_authorize_new_effects(setup, change):
    settings, service, client = setup
    seed(setup)
    req = request()
    reservation = post(setup, 'authorize', req)['reservation']
    assert post(setup, 'verify', {'reservation': reservation, 'action': action(req), 'dispatch': True})['kind'] == 'verified'
    profiles = copy.deepcopy(settings.runtime_tool_profiles)
    if change == 'removed':
        profiles.pop('fixture.tool')
    else:
        profiles['fixture.tool'][change] = {'account': 'replacement-account', 'provider': 'replacement-provider', 'mode': 'live'}[change]
    rotated = replace(settings, runtime_tool_profiles=profiles)
    with TestClient(create_app(rotated)) as restarted:
        auth = headers(rotated)
        def call(route, body):
            response = restarted.post('/v1/runtime/'+route, headers=auth, json=body)
            assert response.status_code == 200, response.text
            return response.json()
        assert call('authorize', req)['kind'] == 'unavailable'
        assert call('verify', {'reservation': reservation, 'action': action(req), 'dispatch': True})['kind'] == 'unavailable'
        read = call('verify', {'reservation': reservation, 'action': action(req), 'dispatch': False})
        assert read == {'kind': 'verified', 'profile': {'provider': 'fixture-provider', 'account': 'fixture-account', 'mode': 'sandbox'}}
        charge = receipt(reservation, cost='6000000', event='old-provider-final')
        assert call('settle', {'reservation': reservation, 'receipt': charge}) == {'kind': 'settled'}
        assert call('settle', {'reservation': reservation, 'receipt': charge}) == {'kind': 'settled'}
    assert service.balance('run')['posted_micro_usd'] == -1_000_000
    assert service.balance('run')['reserved_micro_usd'] == 0
    assert len(service.charges('run')) == 1


@pytest.mark.parametrize('remove', [False, True])
def test_definitive_not_performed_closes_retired_profile_instead_of_renewing(setup, remove):
    settings, service, client = setup
    seed(setup)
    req = request()
    reservation = post(setup, 'authorize', req)['reservation']
    assert post(setup, 'verify', {'reservation': reservation, 'action': action(req), 'dispatch': True})['kind'] == 'verified'
    profiles = copy.deepcopy(settings.runtime_tool_profiles)
    if remove:
        profiles.pop('fixture.tool')
    else:
        profiles['fixture.tool']['account'] = 'replacement'
    with TestClient(create_app(replace(settings, runtime_tool_profiles=profiles))) as retired:
        body = {'reservation': reservation, 'evidenceRef': 'old-provider-definitive-no-effect'}
        for _ in range(2):
            response = retired.post('/v1/runtime/not-performed', headers=headers(settings), json=body)
            assert response.status_code == 200 and response.json() == {'kind': 'closed'}
        response = retired.post('/v1/runtime/verify', headers=headers(settings),
                                json={'reservation': reservation, 'action': action(req), 'dispatch': True})
        assert response.json()['kind'] == 'unavailable'
    assert service.balance('run')['posted_micro_usd'] == 5_000_000
    assert service.balance('run')['reserved_micro_usd'] == 0
    assert service.reservation(reservation['reservationId'])['state'] == 'settled'
