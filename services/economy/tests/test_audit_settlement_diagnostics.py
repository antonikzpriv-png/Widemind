"""Optional provider reports cannot suppress verified charges through HTTP/core APIs."""
import json

import pytest
from fastapi.testclient import TestClient

from widemind.economy.app import create_app
from widemind.economy.config import Settings
from widemind.economy.reporting import audit_integrity
from widemind.economy.service import EconomyError, EconomyService, encode


def reserved(service):
    service.create_run('run')
    row = service.reserve('run', 'action', 1_000_000, 'reserve', 'fixture', 'sandbox', {'max_tokens': 1})
    service.start_reservation(row['reservation_id'])
    return row['reservation_id']


@pytest.mark.parametrize('length', [65_450, 70_000])
@pytest.mark.parametrize('outcome', ['failed', 'succeeded', 'unknown'])
def test_http_known_overage_posts_despite_diagnostics_envelope(tmp_path, length, outcome):
    settings = Settings(str(tmp_path/'economy.sqlite3'), 'operator-'+'o'*48,
                        'gateway-'+'g'*48, 'agent-'+'a'*48, 'run')
    app = create_app(settings)
    service = app.state.economy
    rid = reserved(service)
    payload = {'actual_micro_usd': 6_000_000, 'provider': 'fixture', 'account': 'sandbox',
               'external_charge_id': 'verified-charge', 'usage': {'diagnostic': 'x'*length}, 'outcome': outcome}
    with TestClient(app) as client:
        for _duplicate in range(2):
            response = client.post(f'/v1/reservations/{rid}/settle', json=payload,
                                   headers={'Authorization': 'Bearer '+settings.gateway_token})
            assert response.status_code == 200, response.text
    restarted = EconomyService(service.path)
    balance = restarted.balance('run')
    assert balance['posted_micro_usd'] == balance['available_micro_usd'] == -1_000_000
    assert balance['reserved_micro_usd'] == 0
    assert restarted.reservation(rid)['state'] == ('unknown' if outcome == 'unknown' else 'settled')
    charge = restarted.charges('run')[0]
    assert charge['amount_micro_usd'] == 6_000_000
    diagnostics = (restarted.events('run')[-1]['data']['usage'] if outcome == 'unknown' else charge['usage'])
    assert diagnostics['usage_omitted'] is True
    assert 'x'*1000 not in json.dumps(diagnostics)
    with pytest.raises(EconomyError):
        restarted.reserve('run', 'next', 1, 'next', 'fixture', 'sandbox', {'max_tokens': 1})
    if outcome == 'unknown':
        # A provider's final total still applies the delta, without replaying work.
        restarted.settle(rid, 5_500_000, 'fixture', 'sandbox', 'verified-charge', usage={'final_units': 55})
        assert restarted.balance('run')['posted_micro_usd'] == -500_000
    assert audit_integrity(restarted, 'run')['ok'] is True


@pytest.mark.parametrize('usage', [float('nan'), {'measurement': float('nan')}, {'report': 'x'*70_000}])
def test_core_known_bill_survives_invalid_optional_usage(tmp_path, usage):
    service = EconomyService(tmp_path/'economy.sqlite3')
    rid = reserved(service)
    assert service.settle(rid, 6_000_000, 'fixture', 'sandbox', 'charge', usage=usage)['state'] == 'settled'
    assert service.balance('run')['posted_micro_usd'] == -1_000_000
    assert service.charges('run')[0]['usage']['usage_omitted'] is True


@pytest.mark.parametrize('usage', [{'nested': {'units': 1}}, ['optional-report'],
                                  {f'unit_{i}': i for i in range(65)}])
def test_http_optional_report_shape_cannot_reject_a_known_failed_bill(tmp_path, usage):
    settings = Settings(str(tmp_path/'economy.sqlite3'), 'operator-'+'o'*48,
                        'gateway-'+'g'*48, 'agent-'+'a'*48, 'run')
    app = create_app(settings)
    rid = reserved(app.state.economy)
    with TestClient(app) as client:
        response = client.post(f'/v1/reservations/{rid}/settle',
                               headers={'Authorization': 'Bearer '+settings.gateway_token},
                               json={'actual_micro_usd': 6_000_000, 'provider': 'fixture', 'account': 'sandbox',
                                     'external_charge_id': 'charge', 'usage': usage, 'outcome': 'failed'})
        assert response.status_code == 200, response.text
    assert app.state.economy.balance('run')['available_micro_usd'] == -1_000_000
    assert app.state.economy.reservation(rid)['state'] == 'settled'
    assert app.state.economy.charges('run')[0]['amount_micro_usd'] == 6_000_000
    assert audit_integrity(app.state.economy, 'run')['ok'] is True


def test_existing_larger_receipt_still_replays_without_rewriting_history(tmp_path):
    service = EconomyService(tmp_path/'economy.sqlite3')
    rid = reserved(service)
    original_usage = {'diagnostic': 'legacy-report-'*4500}
    # Persist a previous-version receipt fixture once, under the same append-only
    # triggers, without passing through the newly bounded diagnostics admission.
    with service._tx() as db:
        db.execute('INSERT INTO charges VALUES(?,?,?,?,?,?,?,?)',
                   ('fixture', 'sandbox', 'test', 'charge', rid, 100_000, encode(original_usage), 'succeeded'))
        entry = service._entry(db, 'run', 'operating_charge', -100_000, 'charge',
                              {'provider': 'fixture', 'account': 'sandbox', 'reservation_id': rid,
                               'usage': original_usage, 'outcome': 'succeeded'})
        db.execute("UPDATE reservations SET state='settled',actual_micro_usd=100000 WHERE reservation_id=?", (rid,))
        service._event(db, 'run', 'economy.charge_settled', {'entry_id': entry, 'reservation_id': rid,
                       'actual_micro_usd': 100_000, 'overage_micro_usd': 0})
    ledger_before = service.ledger('run')
    assert service.settle(rid, 100_000, 'fixture', 'sandbox', 'charge', usage=original_usage)['state'] == 'settled'
    assert service.ledger('run') == ledger_before
    assert service.charges('run')[0]['usage'] == original_usage
    assert service.balance('run')['posted_micro_usd'] == 4_900_000
