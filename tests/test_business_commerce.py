"""Protected commerce integration with synthetic Stripe HTTP records only."""
import httpx
import pytest
from fastapi.testclient import TestClient

from widemind.economy.app import create_app
from widemind.economy.config import Settings
from widemind.economy.service import EconomyService, EconomyError
from widemind.economy.stripe import StripePayments
from test_stripe_payments import ACCOUNT, SECRET, StripeFixture


@pytest.fixture
def commerce(tmp_path):
    settings = Settings(str(tmp_path / 'commerce.sqlite3'), 'operator-' + 'o' * 48,
        'gateway-' + 'g' * 48, 'agent-' + 'a' * 48, 'run', stripe_api_key='sk_test_fixture',
        stripe_webhook_secret=SECRET, stripe_account=ACCOUNT, stripe_mode='test',
        checkout_success_url='http://localhost:8000/success', checkout_cancel_url='http://localhost:8000/cancel')
    service, provider = EconomyService(settings.database_path), StripeFixture()
    service.create_run('run')
    provider.records['/checkout/sessions/cs_created'] = dict(provider.records['/checkout/sessions/cs_fixture'], id='cs_created')
    with httpx.Client(transport=httpx.MockTransport(provider)) as http:
        payments = StripePayments(service, api_key='sk_test_fixture', account=ACCOUNT, mode='test', webhook_secret=SECRET,
            success_url=settings.checkout_success_url, cancel_url=settings.checkout_cancel_url, client=http)
        with TestClient(create_app(settings, service=service, payments=payments)) as client:
            yield client, service, provider, payments, settings


def request(client, settings, operation, body, role='operator'):
    return client.post('/v1/runtime/commerce/' + operation, headers={
        'Authorization': 'Bearer ' + getattr(settings, role + '_token')}, json=body)


def quote():
    return dict(runId='run', jobId='job', mode='sandbox', requestId='checkout-action',
                amountMicros='10000000', currency='USD', description='Synthetic order')


def order():
    return dict(runId='run', jobId='job', mode='sandbox')


def fulfillment():
    return dict(**order(), deliveryReference='authenticated-gmail-recipient-reply:fixture', idempotencyKey='fulfill-action')


def test_immutable_checkout_quote_and_roles(commerce):
    client, service, provider, _payments, settings = commerce
    for role in ['gateway', 'agent']:
        assert request(client, settings, 'checkout', quote(), role).status_code == 403
    first = request(client, settings, 'checkout', quote())
    assert first.status_code == 200, first.text
    value = first.json()
    assert value['priceMicros'] == '10000000' and value['mode'] == 'sandbox'
    assert value['paymentId'] == 'cs_created' and value['paymentStatus'] == 'pending'
    assert value['receiptsMicros'] == value['earnedRevenueMicros'] == '0'
    assert request(client, settings, 'checkout', quote()).json() == value
    assert len(provider.checkout_keys) == 1
    for changes in [dict(requestId='other-action'), dict(amountMicros='20000000'), dict(description='Changed scope')]:
        assert request(client, settings, 'checkout', {**quote(), **changes}).status_code == 409
    assert len(provider.checkout_keys) == 1
    assert request(client, settings, 'order', order(), 'gateway').json() == value
    assert request(client, settings, 'order', order(), 'agent').status_code == 403
    assert request(client, settings, 'order', {**order(), 'mode': 'live'}).status_code == 409
    assert service.balance('run')['posted_micro_usd'] == 5_000_000


@pytest.mark.parametrize('amount', [10000000, True, '010000000', '1e7', '10000000.0', '-1', '0'])
def test_commerce_money_never_accepts_noncanonical_or_numeric_values(commerce, amount):
    client, _service, provider, _payments, settings = commerce
    assert request(client, settings, 'checkout', {**quote(), 'amountMicros': amount}).status_code == 422
    assert not provider.checkout_keys


def test_verified_settlement_fulfillment_refund_and_recovery_share_one_ledger(commerce):
    client, service, provider, payments, settings = commerce
    assert request(client, settings, 'checkout', quote()).status_code == 200
    payments.reconcile('cs_created')
    value = request(client, settings, 'order', order()).json()
    assert value['receiptsMicros'] == '10000000' and value['processorFeesMicros'] == '590000'
    assert value['heldMicros'] == '9410000' and value['earnedRevenueMicros'] == '0'
    before = len(service.ledger('run'))
    assert request(client, settings, 'order', order()).status_code == 200
    assert len(service.ledger('run')) == before  # Reads cannot recognize delivery/revenue.
    assert request(client, settings, 'release-hold', fulfillment()).status_code == 409
    for role in ['agent', 'gateway']:
        assert request(client, settings, 'fulfillment', fulfillment(), role).status_code == 403
    completed = request(client, settings, 'fulfillment', fulfillment())
    assert completed.status_code == 200, completed.text
    assert completed.json()['deliveryObligation'] == 'fulfilled'
    assert completed.json()['earnedRevenueMicros'] == '10000000'
    assert completed.json()['heldMicros'] == '0'
    assert service.balance('run')['metrics']['earned_revenue_micro_usd'] == 10_000_000
    before = len(service.ledger('run'))
    request(client, settings, 'fulfillment', fulfillment())
    assert len(service.ledger('run')) == before
    assert request(client, settings, 'fulfillment', {**fulfillment(), 'deliveryReference': 'another-claim'}).status_code == 409
    provider.refunds = [{'id': 're_fixture', 'charge': 'ch_fixture', 'amount': 1000,
        'status': 'succeeded', 'balance_transaction': 'txn_refund'}]
    provider.records['/balance_transactions/txn_refund'] = dict(provider.tx('txn_refund', -1000), source='re_fixture', type='refund')
    payments.reconcile('cs_created')
    refunded = request(client, settings, 'order', order()).json()
    assert refunded['refundsMicros'] == '10000000' and refunded['earnedRevenueMicros'] == '0'
    assert refunded['deliveryObligation'] == 'fulfilled'
    assert service.balance('run')['posted_micro_usd'] == 4_410_000
    assert service.balance('run')['metrics']['earned_revenue_micro_usd'] == 0
    before = len(service.ledger('run'))
    payments.reconcile('cs_created')
    assert len(service.ledger('run')) == before


def test_disputed_delivery_retains_exposure_then_win_restores_earned_metric(commerce):
    client, service, _provider, payments, settings = commerce
    request(client, settings, 'checkout', quote())
    payments.reconcile('cs_created')
    request(client, settings, 'fulfillment', fulfillment())
    payment = service.payment('stripe', ACCOUNT, 'test', 'cs_created')
    snapshot = dict(provider='stripe', account=ACCOUNT, mode='test', payment_id='cs_created', run_id='run', job_id='job',
                    status='disputed', expected_revision=payment['revision'], transactions=[], pending_liability_micro_usd=10_000_000)
    service.apply_payment_snapshot(snapshot)
    disputed = request(client, settings, 'order', order()).json()
    assert disputed['earnedRevenueMicros'] == '0' and disputed['heldMicros'] == '10000000'
    assert service.balance('run')['metrics']['earned_revenue_micro_usd'] == 0
    assert request(client, settings, 'release-hold', {**fulfillment(), 'idempotencyKey': 'new-release'}).status_code == 409
    snapshot.update(status='settled', pending_liability_micro_usd=0,
                    expected_revision=service.payment('stripe', ACCOUNT, 'test', 'cs_created')['revision'])
    service.apply_payment_snapshot(snapshot)
    assert request(client, settings, 'order', order()).json()['earnedRevenueMicros'] == '10000000'
    assert service.balance('run')['metrics']['earned_revenue_micro_usd'] == 10_000_000


def test_existing_checkout_in_another_account_cannot_be_replaced_by_commerce(commerce):
    client, service, provider, _payments, settings = commerce
    service.record_checkout('run', 'stripe', 'acct_another', 'test', 'checkout-action', 'job', 10_000_000, {})
    assert request(client, settings, 'checkout', quote()).status_code == 409
    assert not provider.checkout_keys


def test_unrelated_native_payment_cannot_erase_canonical_order_earnings(commerce):
    client, service, _provider, payments, settings = commerce
    request(client, settings, 'checkout', quote())
    payments.reconcile('cs_created')
    request(client, settings, 'fulfillment', fulfillment())
    service.register_payment('run', 'stripe', ACCOUNT, 'cs_other', 'job', 'other-binding')
    service.apply_payment_snapshot(dict(provider='stripe', account=ACCOUNT, mode='test', payment_id='cs_other',
        run_id='run', job_id='job', status='pending', expected_revision=0, transactions=[]))
    assert request(client, settings, 'order', order()).json()['earnedRevenueMicros'] == '10000000'
    assert service.balance('run')['metrics']['earned_revenue_micro_usd'] == 10_000_000


def test_delivery_before_settlement_fences_an_older_provider_snapshot(commerce):
    client, service, _provider, _payments, settings = commerce
    request(client, settings, 'checkout', quote())
    revision = service.payment('stripe', ACCOUNT, 'test', 'cs_created')['revision']
    response = request(client, settings, 'fulfillment', fulfillment())
    assert response.status_code == 200
    assert response.json()['earnedRevenueMicros'] == '0'
    assert response.json()['deliveryObligation'] == 'fulfilled'
    assert service.payment('stripe', ACCOUNT, 'test', 'cs_created')['revision'] > revision
    with pytest.raises(EconomyError) as error:
        service.apply_payment_snapshot(dict(provider='stripe', account=ACCOUNT, mode='test', payment_id='cs_created',
            run_id='run', job_id='job', status='pending', expected_revision=revision, transactions=[]))
    assert error.value.code == 'stale_snapshot'
