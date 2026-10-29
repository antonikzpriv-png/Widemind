"""Exact-string commerce RPC over the existing protected accounting authority.

Quote and delivery commitments use the existing append-only operations/events.
Only authenticated adapters post provider cash; this bridge never posts receipts.
"""
from __future__ import annotations

import json
import re
from typing import Annotated, Literal

from pydantic import Field, StrictStr, field_validator

from .runtime import Wire
from .service import EconomyError, MAX_MONEY, fingerprint, now

Id = Annotated[StrictStr, Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")]
QUOTE_SCOPE = 'runtime_commerce_quote'
FULFILLMENT_SCOPE = 'runtime_commerce_delivery'
MAX_COMPONENTS = 4000


class OrderRequest(Wire):
    runId: Id
    jobId: Id
    mode: Literal['sandbox', 'live']


class CheckoutRequest(OrderRequest):
    requestId: Id
    amountMicros: StrictStr
    currency: Literal['USD']
    description: Annotated[StrictStr, Field(min_length=1, max_length=200)]

    @field_validator('amountMicros')
    @classmethod
    def exact_amount(cls, value):
        if not re.fullmatch(r'[1-9][0-9]{0,18}', value) or int(value) > MAX_MONEY:
            raise ValueError('Expected canonical positive int64 micro-USD')
        amount = int(value)
        if amount < 500_000 or amount > 999_999_990_000 or amount % 10_000:
            raise ValueError('Expected a supported whole-cent USD Checkout price')
        return value

    @field_validator('description')
    @classmethod
    def safe_description(cls, value):
        if not value.strip() or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError('Expected a single-line description')
        return value


class FulfillmentRequest(OrderRequest):
    deliveryReference: Annotated[StrictStr, Field(min_length=1, max_length=2000)]
    idempotencyKey: Id

    @field_validator('deliveryReference')
    @classmethod
    def safe_reference(cls, value):
        if not value.strip() or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError('Expected actual delivery evidence reference')
        return value


def _job_key(job_id):
    return 'commerce:' + fingerprint(job_id)


def _saved(db, run_id, scope, job_id):
    row = db.execute('SELECT result FROM operations WHERE run_id=? AND scope=? AND idempotency_key=?',
                     (run_id, scope, _job_key(job_id))).fetchone()
    return json.loads(row['result']) if row else None


def _components(db, payment):
    fields = dict(receipts=0, refunds=0, chargebacks=0, reversals=0, fees=0, adjustments=0)
    if payment is None:
        return fields
    rows = db.execute('SELECT c.component,c.amount_micro_usd,t.kind FROM payment_components c '
                      'JOIN payment_transactions t ON c.provider=t.provider AND c.account=t.account '
                      'AND c.mode=t.mode AND c.transaction_id=t.transaction_id '
                      'WHERE t.provider=? AND t.account=? AND t.mode=? AND t.payment_id=? LIMIT ?',
                      (payment['provider'], payment['account'], payment['mode'], payment['payment_id'], MAX_COMPONENTS + 1)).fetchall()
    if len(rows) > MAX_COMPONENTS:
        raise EconomyError('commerce_projection_limit', 'Order accounting exceeds the bounded review limit', 503)
    names = {'receipt': ('receipts', 1), 'refund': ('refunds', -1), 'chargeback': ('chargebacks', -1),
             'chargeback_reversal': ('reversals', 1), 'adjustment': ('adjustments', 1)}
    for row in rows:
        if row['component'] == 'fee':
            fields['fees'] -= row['amount_micro_usd']
        else:
            name, sign = names[row['kind']]
            fields[name] += sign * row['amount_micro_usd']
    return fields


def _earned(db, run_id, job_id):
    rows = db.execute("SELECT metadata FROM ledger WHERE run_id=? AND category='earned_revenue' "
                      "AND json_extract(metadata,'$.job_id')=? LIMIT 2001", (run_id, job_id)).fetchall()
    if len(rows) > 2000:
        raise EconomyError('commerce_projection_limit', 'Order metric history requires operator review', 503)
    return sum(json.loads(row['metadata'])['reported_micro_usd'] for row in rows)


def reconcile_commerce_earned(service, db, payment):
    """Append metric deltas in the same transaction as verified payment changes."""
    job_id, run_id = payment['job_id'], payment['run_id']
    if not job_id:
        return
    quote = _saved(db, run_id, QUOTE_SCOPE, job_id)
    if quote is None:
        return  # Existing non-commerce accounting is unchanged.
    canonical = db.execute('SELECT payment_id FROM checkouts WHERE provider=? AND account=? AND mode=? AND request_id=?',
        (quote['provider'], quote['account'], quote['nativeMode'], quote['requestId'])).fetchone()
    if canonical is None or (payment['provider'], payment['account'], payment['mode'], payment['payment_id']) != (
            quote['provider'], quote['account'], quote['nativeMode'], canonical['payment_id']):
        return  # A different native payment cannot rewrite this order's earnings.
    delivery = _saved(db, run_id, FULFILLMENT_SCOPE, job_id)
    fields = _components(db, payment)
    principal = fields['receipts'] - fields['refunds'] - fields['chargebacks'] + fields['reversals']
    target = (min(int(quote['amountMicros']), max(0, principal - payment['pending_liability_micro_usd']))
              if delivery is not None and payment['status'] != 'disputed' else 0)
    delta = target - _earned(db, run_id, job_id)
    if delta:
        reference = 'commerce:' + fingerprint([job_id, payment['revision'], delivery])
        entry_id = service._entry(db, run_id, 'earned_revenue', 0, reference,
            {'reported_micro_usd': delta, 'job_id': job_id, 'commerce': True,
             'delivery_reference': delivery['deliveryReference'] if delivery else None,
             'payment_revision': payment['revision']})
        service._event(db, run_id, 'economy.earned_revenue',
            {'entry_id': entry_id, 'reported_micro_usd': delta, 'job_id': job_id})


class CommerceBridge:
    def __init__(self, service, *, account, mode, payments):
        self.service, self.account, self.mode, self.payments = service, account, mode, payments

    def _quote(self, db, body):
        run = self.service._run(db, body.runId)
        native_mode = 'test' if body.mode == 'sandbox' else 'live'
        if run['mode'] != native_mode or native_mode != self.mode:
            raise EconomyError('mode_mismatch', 'Commerce mode differs from its protected run/account')
        quote = _saved(db, body.runId, QUOTE_SCOPE, body.jobId)
        if quote is None:
            raise EconomyError('not_found', 'Commerce order has no immutable quote', 404)
        if (quote['provider'], quote['account'], quote['nativeMode']) != ('stripe', self.account, self.mode):
            raise EconomyError('commerce_profile_changed', 'Commerce order account identity changed')
        return quote

    def _payment(self, db, quote):
        checkout = db.execute('SELECT * FROM checkouts WHERE provider=? AND account=? AND mode=? AND request_id=?',
            ('stripe', quote['account'], quote['nativeMode'], quote['requestId'])).fetchone()
        if checkout is None or checkout['payment_id'] is None:
            return checkout, None
        payment = self.service._payment(db, 'stripe', quote['account'], quote['nativeMode'], checkout['payment_id'])
        if (payment['run_id'], payment['job_id']) != (quote['runId'], quote['jobId']):
            raise EconomyError('payment_conflict', 'Commerce payment binding changed')
        return checkout, payment

    def _project(self, db, body):
        quote = self._quote(db, body)
        checkout, payment = self._payment(db, quote)
        fields = _components(db, payment)
        delivery = _saved(db, body.runId, FULFILLMENT_SCOPE, body.jobId)
        held = 0
        if payment is not None:
            hold_id = self.service._payment_hold_id('stripe', quote['account'], quote['nativeMode'], payment['payment_id'])
            row = db.execute('SELECT amount_micro_usd FROM holds WHERE run_id=? AND hold_id=?', (body.runId, hold_id)).fetchone()
            held = row[0] if row else 0
        revision = db.execute('SELECT COALESCE(MAX(sequence),0) FROM events WHERE run_id=?', (body.runId,)).fetchone()[0]
        unresolved = db.execute("SELECT 1 FROM reservations WHERE run_id=? AND state IN ('reserved','running','unknown') LIMIT 1", (body.runId,)).fetchone()
        return dict(runId=body.runId, jobId=body.jobId, mode=body.mode, currency='USD',
            requestId=quote['requestId'], paymentId=payment['payment_id'] if payment else None,
            checkoutUrl=checkout['url'] if checkout else None, priceMicros=quote['amountMicros'],
            paymentStatus=payment['status'] if payment else 'pending', revision=str(revision),
            receiptsMicros=str(fields['receipts']), refundsMicros=str(fields['refunds']),
            chargebacksMicros=str(fields['chargebacks']), chargebackReversalsMicros=str(fields['reversals']),
            processorFeesMicros=str(fields['fees']), paymentAdjustmentsMicros=str(fields['adjustments']),
            netReceiptsMicros=str(sum((fields['receipts'], -fields['refunds'], -fields['chargebacks'], fields['reversals'], -fields['fees'], fields['adjustments']))),
            earnedRevenueMicros=str(_earned(db, body.runId, body.jobId)), heldMicros=str(held),
            pendingLiabilityMicros=str(payment['pending_liability_micro_usd'] if payment else 0),
            releasedMicros=str(payment['released_micro_usd'] if payment else 0), costsFinal=unresolved is None,
            deliveryObligation='fulfilled' if delivery else 'unfulfilled',
            deliveryReference=delivery['deliveryReference'] if delivery else None)

    def order(self, body):
        with self.service._read() as db:
            return self._project(db, body)

    def checkout(self, body):
        adapter = self.payments()
        if (adapter.account, adapter.mode) != (self.account, self.mode):
            raise EconomyError('commerce_profile_changed', 'Checkout adapter identity changed')
        native_mode = 'test' if body.mode == 'sandbox' else 'live'
        quote = dict(runId=body.runId, jobId=body.jobId, mode=body.mode, nativeMode=native_mode,
                     provider='stripe', account=self.account, requestId=body.requestId,
                     amountMicros=body.amountMicros, currency=body.currency, description=body.description)
        with self.service._tx() as db:
            if self.service._run(db, body.runId)['mode'] != native_mode or self.mode != native_mode:
                raise EconomyError('mode_mismatch', 'Checkout mode differs from its run/account')
            existing = db.execute('SELECT provider,account,mode,request_id,amount_micro_usd FROM checkouts WHERE run_id=? AND job_id=? LIMIT 2', (body.runId, body.jobId)).fetchall()
            expected = ('stripe', self.account, self.mode, body.requestId, int(body.amountMicros))
            if any(tuple(row) != expected for row in existing) or len(existing) > 1:
                raise EconomyError('commerce_quote_conflict', 'Order already has another durable Checkout intent')
            def claim():
                self.service._event(db, body.runId, 'economy.commerce_quote',
                    {'job_id': body.jobId, 'request_id': body.requestId, 'amount_micro_usd': int(body.amountMicros), 'currency': body.currency})
                return quote
            self.service._operation(db, body.runId, QUOTE_SCOPE, _job_key(body.jobId), quote, claim)
        adapter.create_checkout(body.runId, body.requestId, body.jobId, int(body.amountMicros), body.description)
        return self.order(body)

    def _release(self, db, body, payment, key):
        if payment['status'] in {'pending', 'disputed'} or payment['pending_liability_micro_usd']:
            raise EconomyError('payment_exposure', 'Reconcile payment/refund/dispute exposure before releasing funds')
        identity = (payment['provider'], payment['account'], payment['mode'], payment['payment_id'])
        def release():
            released = max(payment['released_micro_usd'], self.service._payment_posted_net(db, identity), 0)
            if released > MAX_MONEY:
                raise EconomyError('payment_projection_limit', 'Payment release exceeds supported projection', 422)
            db.execute('UPDATE payments SET released_micro_usd=?,revision=revision+1 WHERE provider=? AND account=? AND mode=? AND payment_id=?', (released, *identity))
            self.service._refresh_payment_hold(db, self.service._payment(db, *identity))
            self.service._event(db, body.runId, 'economy.customer_funds_released',
                {'provider': identity[0], 'account': identity[1], 'mode': identity[2], 'payment_id': identity[3],
                 'released_micro_usd': released, 'delivery_reference': body.deliveryReference})
            return {'released': True}
        return self.service._operation(db, body.runId, 'payment_hold_release', key, list(identity), release)

    def fulfillment(self, body):
        with self.service._tx() as db:
            quote = self._quote(db, body)
            _checkout, payment = self._payment(db, quote)
            if payment is None:
                raise EconomyError('payment_pending', 'Checkout must be registered before recording its delivery')
            prior_delivery = _saved(db, body.runId, FULFILLMENT_SCOPE, body.jobId)
            def record():
                delivery = dict(deliveryReference=body.deliveryReference, fulfilledAt=now())
                def once():
                    self.service._event(db, body.runId, 'economy.commerce_fulfilled',
                        {'job_id': body.jobId, 'delivery_reference': body.deliveryReference})
                    return delivery
                return self.service._operation(db, body.runId, FULFILLMENT_SCOPE, _job_key(body.jobId),
                                               [body.jobId, body.deliveryReference], once)
            self.service._operation(db, body.runId, 'runtime_commerce_fulfillment_request', body.idempotencyKey,
                                    [body.jobId, body.deliveryReference], record)
            if prior_delivery is None:
                key = (payment['provider'], payment['account'], payment['mode'], payment['payment_id'])
                db.execute('UPDATE payments SET revision=revision+1 WHERE provider=? AND account=? AND mode=? AND payment_id=?', key)
                payment = self.service._payment(db, *key)
            reconcile_commerce_earned(self.service, db, payment)
            # Actual delivery remains true when a refund/dispute precedes its ACK.
            # Unresolved exposure stays held; the separate release route can be
            # explicitly retried after authoritative reconciliation resolves it.
            if payment['status'] not in {'pending', 'disputed'} and not payment['pending_liability_micro_usd']:
                self._release(db, body, payment, 'commerce:' + fingerprint([body.jobId, body.idempotencyKey]))
            return self._project(db, body)

    def release_hold(self, body):
        with self.service._tx() as db:
            quote = self._quote(db, body)
            delivery = _saved(db, body.runId, FULFILLMENT_SCOPE, body.jobId)
            if delivery is None or delivery['deliveryReference'] != body.deliveryReference:
                raise EconomyError('fulfillment_required', 'Hold release requires the recorded actual delivery evidence')
            _checkout, payment = self._payment(db, quote)
            if payment is None:
                raise EconomyError('payment_pending', 'Payment remains unregistered')
            self._release(db, body, payment, body.idempotencyKey)
            return self._project(db, body)
