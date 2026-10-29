"""Exact-decimal RPC bridge for the foundation's Economy contract.

This service authenticates gateway calls independently of model instructions.
Opaque proofs bind immutable persisted accounting identities; they are never grants.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, field_validator, model_validator

from .service import EconomyError, EconomyService, encode, fingerprint, ident, now

INT64_MAX = 9_223_372_036_854_775_807
SAFE_INTEGER = 9_007_199_254_740_991
Id = Annotated[StrictStr, Field(min_length=1, max_length=200)]
Evidence = Annotated[StrictStr, Field(min_length=1, max_length=2000)]


class Wire(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class WireMoney(Wire):
    currency: Literal['USD']
    micros: StrictStr

    @field_validator('micros')
    @classmethod
    def canonical_micros(cls, value):
        if len(value) > 19 or not re.fullmatch(r'0|[1-9][0-9]*', value) or int(value) > INT64_MAX:
            raise ValueError('Expected canonical nonnegative int64 decimal micro-USD')
        return value


class BudgetLimit(Wire):
    maximum: WireMoney
    maxTokens: Annotated[StrictInt, Field(ge=1, le=SAFE_INTEGER)] | None = None
    maxDurationMs: Annotated[StrictInt, Field(ge=1, le=300000)]
    maxOutputBytes: Annotated[StrictInt, Field(ge=1, le=1048576)]

    @model_validator(mode='before')
    @classmethod
    def reject_null_optional_limit(cls, value):
        if isinstance(value, dict) and 'maxTokens' in value and value['maxTokens'] is None:
            raise ValueError('Omit absent maxTokens rather than sending null')
        return value


class RunRequest(Wire):
    runId: Id


class SeedRequest(RunRequest):
    mode: Literal['sandbox', 'live']


class AuthorizationRequest(RunRequest):
    actionId: Id
    tool: Id
    limit: BudgetLimit


class Reservation(Wire):
    reservationId: Id
    actionId: Id
    runId: Id
    limit: BudgetLimit
    authorization: Annotated[StrictStr, Field(min_length=1, max_length=256)]


class ToolAction(AuthorizationRequest):
    version: Annotated[StrictInt, Field(ge=1, le=1)]
    workKey: Id
    input: Any
    jobId: Id | None = None
    opportunityId: Id | None = None


class VerificationRequest(Wire):
    reservation: Reservation
    action: ToolAction
    dispatch: StrictBool = False


class NotPerformedRequest(Wire):
    reservation: Reservation
    evidenceRef: Annotated[StrictStr, Field(min_length=1, max_length=2048)]


class NativeAmount(Wire):
    currency: Annotated[StrictStr, Field(min_length=1, max_length=20)]
    minorUnits: Annotated[StrictStr, Field(min_length=1, max_length=128)]
    conversionRef: Evidence
    rounding: Evidence

    @field_validator('minorUnits')
    @classmethod
    def canonical_native(cls, value):
        if not re.fullmatch(r'0|-?[1-9][0-9]*', value):
            raise ValueError('Native amount requires canonical decimal minor units')
        return value


class Usage(Wire):
    actualCharge: WireMoney
    provider: Id
    accountRef: Id
    externalEventId: Evidence
    billableFailure: StrictBool
    provisional: StrictBool
    evidenceRef: Evidence
    nativeAmount: NativeAmount | None = None


class ToolReceipt(Wire):
    version: Annotated[StrictInt, Field(ge=1, le=1)]
    actionId: Id
    status: Literal['succeeded', 'failed']
    # Results are diagnostic data; accounting never parses claims from them.
    result: Any
    usage: Usage
    externalRef: Any = None


class SettlementRequest(Wire):
    reservation: Reservation
    receipt: ToolReceipt


def validate_profiles(profiles):
    if not isinstance(profiles, dict) or len(profiles) > 100:
        raise ValueError('Runtime tool profiles must be an object with at most 100 configured tools')
    result = {}
    for tool, profile in profiles.items():
        try:
            ident(tool)
            if not isinstance(profile, dict) or set(profile) != {'provider', 'account', 'mode'}:
                raise ValueError('Invalid runtime tool profile fields')
            ident(profile['provider']); ident(profile['account'])
            mode = {'sandbox': 'test', 'test': 'test', 'live': 'live'}.get(profile['mode'])
            if mode is None:
                raise ValueError('Invalid runtime tool mode')
        except (EconomyError, TypeError, KeyError):
            raise ValueError('Invalid runtime tool profile') from None
        result[tool] = dict(provider=profile['provider'], account=profile['account'], mode=mode)
    return result


class RuntimeBridge:
    def __init__(self, service: EconomyService, *, secret: str, profiles: dict):
        if not isinstance(secret, str) or not 32 <= len(secret) <= 512 or not secret.isascii() or any(c.isspace() for c in secret):
            raise ValueError('Runtime authorization secret must contain 32–512 ASCII characters without whitespace')
        self.service, self._secret = service, secret.encode()
        self.profiles = validate_profiles(profiles)

    def summary(self, run_id):
        try:
            balance = self.service.balance(run_id)
        except EconomyError as error:
            if error.status == 404:
                return None
            raise
        totals = balance['totals']
        known = {'opening_grant', 'owner_funding', 'test_funding', 'customer_receipt', 'operating_charge',
                 'processor_fee', 'refund', 'chargeback', 'chargeback_reversal', 'payment_adjustment', 'correction',
                 'earned_revenue', 'infrastructure_subsidy'}
        values = {
            'openingCreditMicros': totals.get('opening_grant', 0),
            'postedMicros': balance['posted_micro_usd'],
            'reservedMicros': balance['reserved_micro_usd'], 'holdsMicros': balance['held_micro_usd'],
            'spendableMicros': balance['available_micro_usd'],
            'customerReceiptsMicros': totals.get('customer_receipt', 0),
            'earnedRevenueMicros': balance['metrics']['earned_revenue_micro_usd'],
            'operatingChargesMicros': -totals.get('operating_charge', 0),
            'ownerFundingMicros': totals.get('owner_funding', 0),
            'subsidiesMicros': balance['metrics']['infrastructure_subsidy_micro_usd'],
            'processorFeesMicros': -totals.get('processor_fee', 0),
            'refundsMicros': -totals.get('refund', 0), 'chargebacksMicros': -totals.get('chargeback', 0),
            'chargebackReversalsMicros': totals.get('chargeback_reversal', 0),
            'paymentAdjustmentsMicros': totals.get('payment_adjustment', 0),
            'correctionsMicros': totals.get('correction', 0), 'testFundingMicros': totals.get('test_funding', 0),
            'otherCashMicros': sum(amount for category, amount in totals.items() if category not in known),
        }
        unsigned = {'openingCreditMicros', 'reservedMicros', 'holdsMicros', 'customerReceiptsMicros',
                    'operatingChargesMicros', 'ownerFundingMicros', 'testFundingMicros', 'refundsMicros', 'chargebacksMicros'}
        if any(values[key] < 0 for key in unsigned) or any(len(str(abs(value))) > 128 for value in values.values()):
            raise EconomyError('invalid_projection', 'Runtime financial projection is inconsistent', 503)
        return dict(currency='USD', **{key: str(value) for key, value in values.items()},
                    mode='sandbox' if balance['mode'] == 'test' else 'live', asOf=now().replace('+00:00', 'Z'))

    def seed(self, request: SeedRequest):
        self.service.create_run(request.runId, 'test' if request.mode == 'sandbox' else 'live')
        return self.summary(request.runId)

    @staticmethod
    def _limits(limit: BudgetLimit):
        result = {'max_duration_ms': limit.maxDurationMs, 'max_storage_bytes': limit.maxOutputBytes}
        if limit.maxTokens is not None:
            result['max_tokens'] = limit.maxTokens
        return result

    def _profile(self, tool):
        profile = self.profiles.get(tool)
        if profile is None:
            raise EconomyError('tool_unavailable', 'No trusted accounting profile is configured for this tool', 503)
        return profile

    @staticmethod
    def _request_fingerprint(request):
        return fingerprint(request.model_dump(exclude_none=True))

    def _proof(self, binding):
        data = {key: binding[key] for key in ('reservation_id', 'run_id', 'action_id', 'tool', 'limit_json',
                                              'provider', 'account', 'mode', 'fingerprint', 'created_at')}
        return 'wm1.' + hmac.new(self._secret, encode(data).encode(), hashlib.sha256).hexdigest()

    def _reservation(self, binding):
        return dict(reservationId=binding['reservation_id'], actionId=binding['action_id'], runId=binding['run_id'],
                    limit=json.loads(binding['limit_json']), authorization=self._proof(binding))

    def authorize(self, request: AuthorizationRequest):
        try:
            profile = self._profile(request.tool)
            with self.service._tx() as db:
                run = self.service._run(db, request.runId)
                if run['mode'] != profile['mode']:
                    raise EconomyError('mode_mismatch', 'Tool profile differs from the funded run mode')
                old = db.execute('SELECT * FROM runtime_authorizations WHERE run_id=? AND action_id=?',
                                 (request.runId, request.actionId)).fetchone()
                if old:
                    binding = dict(old)
                    if binding['fingerprint'] != self._request_fingerprint(request) or any(binding[k] != profile[k] for k in ('provider', 'account', 'mode')):
                        raise EconomyError('authorization_mismatch', 'Action already has another bound authorization')
                    row = self.service._reservation(db, binding['reservation_id'])
                else:
                    key = 'runtime:' + fingerprint([request.runId, request.actionId])
                    row = self.service._reserve(db, request.runId, request.actionId, int(request.limit.maximum.micros),
                                                key, profile['provider'], profile['account'], self._limits(request.limit))
                    binding = dict(reservation_id=row['reservation_id'], run_id=request.runId, action_id=request.actionId,
                                   tool=request.tool, limit_json=encode(request.limit.model_dump(exclude_none=True)),
                                   **profile, fingerprint=self._request_fingerprint(request), created_at=now())
                    db.execute('INSERT INTO runtime_authorizations VALUES(?,?,?,?,?,?,?,?,?,?)', tuple(binding.values()))
                if row['state'] != 'reserved':
                    raise EconomyError('authorization_closed', 'Action was dispatched or closed; reconcile its existing outcome')
                if row['amount_micro_usd'] and self.service._balance(db, request.runId)['available_micro_usd'] < 0:
                    raise EconomyError('insufficient_funds', 'New liabilities invalidate paid authorization')
                return {'kind': 'authorized', 'reservation': self._reservation(binding)}
        except EconomyError as error:
            return {'kind': 'insufficient' if error.code == 'insufficient_funds' else 'unavailable', 'reason': error.code}

    def _bound(self, reservation: Reservation, action: ToolAction | None = None, *, require_current_profile=False):
        with self.service._read() as db:
            raw = db.execute('SELECT * FROM runtime_authorizations WHERE reservation_id=?', (reservation.reservationId,)).fetchone()
            if raw is None:
                raise EconomyError('authorization_unknown', 'Unknown runtime authorization')
            binding = dict(raw)
            expected = self._reservation(binding)
            supplied = reservation.model_dump(exclude_none=True)
            if not reservation.authorization.isascii() or not hmac.compare_digest(reservation.authorization, expected['authorization']) or any(supplied[k] != expected[k] for k in ('runId', 'actionId', 'reservationId', 'limit')):
                raise EconomyError('authorization_mismatch', 'Authorization does not match the persisted action')
            if require_current_profile:
                profile = self._profile(binding['tool'])
                if any(binding[k] != profile[k] for k in ('provider', 'account', 'mode')):
                    raise EconomyError('profile_changed', 'Tool accounting profile changed; no new execution is authorized')
            row = self.service._reservation(db, binding['reservation_id'])
            if action is not None:
                if (action.runId, action.actionId, action.tool, action.limit.model_dump(exclude_none=True)) != (
                        binding['run_id'], binding['action_id'], binding['tool'], json.loads(binding['limit_json'])):
                    raise EconomyError('authorization_mismatch', 'Tool action differs from its persisted authorization')
            if row['state'] == 'cancelled':
                raise EconomyError('authorization_closed', 'Authorization was cancelled before dispatch')
            return binding, row

    def verify(self, request: VerificationRequest):
        try:
            binding, row = self._bound(request.reservation, request.action, require_current_profile=request.dispatch)
            if request.dispatch:
                with self.service._tx() as db:
                    self.service._start_reservation(db, binding['reservation_id'])
            return {'kind': 'verified', 'profile': {'provider': binding['provider'], 'account': binding['account'],
                                                  'mode': 'sandbox' if binding['mode'] == 'test' else 'live'}}
        except EconomyError as error:
            return {'kind': 'unavailable', 'reason': error.code}

    def confirm_not_performed(self, request: NotPerformedRequest):
        try:
            binding, _ = self._bound(request.reservation)
            current = self.profiles.get(binding['tool'])
            profile_current = current is not None and all(binding[k] == current[k] for k in ('provider', 'account', 'mode'))
            with self.service._tx() as db:
                row = self.service._reservation(db, binding['reservation_id'])
                latest = db.execute('SELECT MAX(ordinal) FROM runtime_dispatches WHERE reservation_id=?', (binding['reservation_id'],)).fetchone()[0]
                if latest is None:
                    raise EconomyError('not_dispatched', 'No dispatched attempt requires recovery')
                previous = db.execute('SELECT * FROM runtime_rearms WHERE reservation_id=? AND evidence_ref=?',
                                      (binding['reservation_id'], request.evidenceRef)).fetchone()
                charge = db.execute('SELECT * FROM charges WHERE reservation_id=?', (binding['reservation_id'],)).fetchone()
                latest_rearm = db.execute('SELECT * FROM runtime_rearms WHERE reservation_id=? AND dispatch_ordinal=?',
                                          (binding['reservation_id'], latest)).fetchone()
                if latest_rearm and row['state'] == 'settled' and charge and charge['amount_micro_usd'] == 0:
                    usage = json.loads(charge['usage'])
                    if usage.get('source') == 'runtime-not-performed' and usage.get('evidenceRef') == latest_rearm['evidence_ref']:
                        return {'kind': 'closed'}
                if charge or db.execute('SELECT 1 FROM provisional_charges WHERE reservation_id=?', (binding['reservation_id'],)).fetchone():
                    raise EconomyError('known_usage_requires_reconciliation', 'A known charge or subtotal cannot be declared not performed')
                if latest_rearm and row['state'] == 'reserved':
                    if not profile_current:
                        raise EconomyError('profile_changed', 'A retired tool profile cannot renew execution')
                    # A prior confirmation already renewed this exact dispatch.
                    # Its HTTP response may have been lost and a fresh lookup can
                    # have a new evidence reference. Acknowledge existing state;
                    # do not reset a counter or create another authorization.
                    return {'kind': 'renewed'}
                if previous:
                    raise EconomyError('stale_not_performed_evidence', 'This evidence belongs to an earlier dispatch')
                if row['state'] not in ('running', 'unknown'):
                    raise EconomyError('authorization_closed', 'Only an unresolved dispatch can be reconciled as not performed')
                digest = fingerprint([binding['reservation_id'], latest, request.evidenceRef])
                db.execute('INSERT INTO runtime_rearms VALUES(?,?,?,?,?)',
                           (binding['reservation_id'], latest, request.evidenceRef, digest, now()))
                event_data = {'reservation_id': binding['reservation_id'], 'dispatch_ordinal': latest,
                              'evidence_ref': request.evidenceRef}
                if latest >= 3 or not profile_current:
                    event_data['closure_reason'] = 'dispatch_limit' if profile_current else 'profile_retired'
                    self.service._settle(db, binding['reservation_id'], 0, binding['provider'], binding['account'],
                                         'rt:' + binding['reservation_id'],
                                         usage={'source': 'runtime-not-performed', 'evidenceRef': request.evidenceRef,
                                                'dispatch_ordinal': latest}, outcome='failed')
                    self.service._event(db, binding['run_id'], 'economy.runtime_not_performed_closed', event_data)
                    return {'kind': 'closed'}
                db.execute("UPDATE reservations SET state='reserved',updated_at=? WHERE reservation_id=?", (now(), binding['reservation_id']))
                self.service._event(db, binding['run_id'], 'economy.runtime_not_performed', event_data)
                return {'kind': 'renewed'}
        except EconomyError as error:
            return {'kind': 'unavailable', 'reason': error.code}

    def settle(self, request: SettlementRequest):
        try:
            binding, row = self._bound(request.reservation)
            receipt, usage = request.receipt, request.receipt.usage
            if receipt.actionId != binding['action_id'] or (usage.provider, usage.accountRef) != (binding['provider'], binding['account']):
                raise EconomyError('receipt_mismatch', 'Usage does not belong to the authorized provider/action')
            details = {'evidenceRef': usage.evidenceRef, 'billableFailure': usage.billableFailure,
                       'provisional': usage.provisional, 'source': 'runtime-v1'}
            if usage.nativeAmount is not None:
                details['nativeAmount'] = usage.nativeAmount.model_dump()
            # Result/externalRef are not financial evidence. Omitting those optional
            # diagnostics avoids dropping a verified actual charge for a large result.
            details['externalEventId'] = usage.externalEventId
            event_fingerprint = fingerprint({'reservation_id': binding['reservation_id'],
                                             'actualCharge': usage.actualCharge.model_dump(),
                                             'status': receipt.status, 'usage': details})
            result = ({'kind': 'unavailable', 'reason': 'provisional_charge_requires_reconciliation'}
                      if usage.provisional else {'kind': 'settled'})
            with self.service._tx() as db:
                previous = db.execute('SELECT * FROM runtime_receipts WHERE provider=? AND account=? AND mode=? AND event_id=?',
                                      (binding['provider'], binding['account'], binding['mode'], usage.externalEventId)).fetchone()
                if previous:
                    if previous['reservation_id'] != binding['reservation_id'] or previous['fingerprint'] != event_fingerprint:
                        raise EconomyError('receipt_event_conflict', 'Provider event is already bound to another immutable receipt')
                    return json.loads(previous['result_json'])
                # Provider event IDs identify observations; a later final observation
                # can legitimately use a different ID. The native charge identity is
                # stable for the attempt so provisional-to-final posting is a delta.
                native = db.execute('SELECT * FROM charges WHERE reservation_id=?', (binding['reservation_id'],)).fetchone()
                if (native and not usage.provisional and native['amount_micro_usd'] == int(usage.actualCharge.micros)
                        and native['outcome'] == receipt.status):
                    # New provider observations of the same final economic charge
                    # remain auditable without posting the already settled cost again.
                    self.service._event(db, binding['run_id'], 'economy.runtime_receipt_observed',
                                        {'reservation_id': binding['reservation_id'], 'external_event_id': usage.externalEventId,
                                         'actual_micro_usd': int(usage.actualCharge.micros), 'outcome': receipt.status,
                                         'usage': details})
                else:
                    self.service._settle(db, binding['reservation_id'], int(usage.actualCharge.micros), usage.provider,
                                         usage.accountRef, 'rt:' + binding['reservation_id'], usage=details,
                                         outcome='unknown' if usage.provisional else receipt.status)
                db.execute('INSERT INTO runtime_receipts VALUES(?,?,?,?,?,?,?,?)',
                           (binding['provider'], binding['account'], binding['mode'], usage.externalEventId,
                            binding['reservation_id'], event_fingerprint, encode(result), now()))
                return result
        except EconomyError as error:
            return {'kind': 'unavailable', 'reason': error.code}
