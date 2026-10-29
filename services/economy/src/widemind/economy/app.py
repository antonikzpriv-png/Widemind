"""Versioned authenticated economy HTTP entry points.

This control-plane process, its database and operator/gateway tokens must remain
outside the model's writable workspace. Agent identity is limited to one run.
"""
from __future__ import annotations

import hmac
from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr
from starlette.concurrency import run_in_threadpool

from .config import Settings
from .service import EconomyError, EconomyService
from .reconciliation import ReconciliationQueue

MAX_MONEY = 9_000_000_000_000_000
Money = Annotated[StrictInt, Field(ge=0, le=MAX_MONEY)]
PositiveMoney = Annotated[StrictInt, Field(gt=0, le=MAX_MONEY)]
SignedMoney = Annotated[StrictInt, Field(ge=-MAX_MONEY, le=MAX_MONEY)]
Identifier = Annotated[StrictStr, Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")]
ProviderPaymentId = Annotated[StrictStr, Field(min_length=4, max_length=200, pattern=r"^(?:cs_[A-Za-z0-9_]+|[A-Z0-9_-]{6,100})$")]
Note = Annotated[StrictStr, Field(min_length=1, max_length=200)]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RunInput(Input):
    run_id: Identifier
    mode: Literal["test", "live"] = "test"
    funding_reference: Note | None = None


class ReserveInput(Input):
    action_id: Identifier
    amount_micro_usd: Money
    idempotency_key: Identifier
    provider: Identifier
    account: Identifier
    limits: Annotated[dict[Identifier, Money], Field(min_length=1, max_length=32)]


class SettleInput(Input):
    actual_micro_usd: Money | None
    provider: Identifier
    account: Identifier
    external_charge_id: Identifier
    usage: Annotated[dict[Identifier, StrictInt | StrictStr], Field(max_length=64)] | None = None
    outcome: Literal["succeeded", "failed", "unknown"] = "succeeded"


class HoldInput(Input):
    hold_id: Identifier
    amount_micro_usd: Money
    kind: Literal["customer", "refund"]
    reason: Note
    idempotency_key: Identifier


class FundingInput(Input):
    amount_micro_usd: PositiveMoney
    category: Literal["owner_funding", "test_funding"]
    reference: Note
    idempotency_key: Identifier


class ReferenceInput(Input):
    amount_micro_usd: SignedMoney
    reference: Note
    idempotency_key: Identifier


class EarnedInput(ReferenceInput):
    job_id: Identifier


class CorrectionInput(Input):
    entry_id: Identifier
    amount_micro_usd: SignedMoney
    reason: Note
    idempotency_key: Identifier


class CheckoutInput(Input):
    request_id: Identifier
    job_id: Identifier
    amount_micro_usd: PositiveMoney
    description: Annotated[StrictStr, Field(min_length=1, max_length=200)] = "WideMind order"


class RefundInput(Input):
    request_id: Identifier
    amount_micro_usd: PositiveMoney


class IdempotencyInput(Input):
    idempotency_key: Identifier


class RetryInput(Input):
    reason: Note
    idempotency_key: Identifier


class BoundedRoute(APIRoute):
    """Bound raw and JSON request bodies before parsing or storing them."""
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def bounded(request: Request):
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 1_048_576:
                    return JSONResponse({"error": {"code": "body_too_large", "message": "Request body exceeds 1 MiB"}}, status_code=413)
            request._body = bytes(body)
            return await handler(request)

        return bounded


def create_app(settings: Settings | None = None, *, service: EconomyService | None = None,
               payments: Any = None) -> FastAPI:
    settings = settings or Settings.from_env()
    economy = service or EconomyService(settings.database_path)
    if payments is None:
        payments = settings.create_payments(economy)
    queue = ReconciliationQueue(economy)
    @asynccontextmanager
    async def lifespan(_app):
        try:
            yield
        finally:
            if payments is not None:
                await run_in_threadpool(payments.close)

    app = FastAPI(title="WideMind economy", version="1", docs_url=None, redoc_url=None,
                  openapi_url=None, lifespan=lifespan)
    app.router.route_class = BoundedRoute
    app.state.economy = economy
    app.state.payments = payments
    app.state.reconciliation_queue = queue

    @app.exception_handler(EconomyError)
    async def economy_error(_request: Request, exc: EconomyError):
        return JSONResponse({"error": {"code": exc.code, "message": str(exc)}}, status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def input_error(_request: Request, _exc: RequestValidationError):
        # Pydantic's default response includes submitted values, potentially secrets.
        return JSONResponse({"error": {"code": "invalid_input", "message": "Request does not match the versioned economy contract"}}, status_code=422)

    def identify(authorization: str | None) -> str:
        if authorization is None or not authorization.startswith("Bearer "):
            raise EconomyError("unauthorized", "A bearer identity is required", 401)
        value = authorization[7:]
        if not value.isascii() or len(value) > 512:
            raise EconomyError("unauthorized", "Invalid bearer identity", 401)
        matches = [role for role, expected in (("operator", settings.operator_token), ("gateway", settings.gateway_token), ("agent", settings.agent_token))
                   if hmac.compare_digest(value, expected)]
        if not matches:
            raise EconomyError("unauthorized", "Invalid bearer identity", 401)
        return matches[0]

    @app.middleware("http")
    async def authenticate_before_body(request: Request, call_next):
        # Reject missing/ambiguous credentials before JSON/body parsing. Private
        # requests cannot consume the full body budget before authentication.
        if request.url.path.startswith("/v1/") and request.url.path not in ("/v1/webhooks/stripe","/v1/webhooks/paypal"):
            values = request.headers.getlist("authorization")
            try:
                if len(values) != 1:
                    raise EconomyError("unauthorized", "A single bearer identity is required", 401)
                request.state.economy_role = identify(values[0])
            except EconomyError as exc:
                return JSONResponse({"error": {"code": exc.code, "message": str(exc)}},
                                    status_code=exc.status, headers={"WWW-Authenticate": "Bearer"})
        return await call_next(request)

    def identity(request: Request) -> str:
        return request.state.economy_role

    def authorize(role: str, allowed: set[str], run_id: Identifier | None = None) -> None:
        if role not in allowed:
            raise EconomyError("forbidden", "This identity cannot perform that operation", 403)
        if role == "agent" and run_id != settings.agent_run_id:
            raise EconomyError("forbidden", "Agent identity is not authorized for this run", 403)

    def scope():
        adapter=app.state.payments
        return (getattr(adapter,'provider',settings.payment_provider),adapter.account,adapter.mode) if adapter is not None else (settings.payment_provider,settings.paypal_account if settings.payment_provider=='paypal' else settings.stripe_account,settings.paypal_mode if settings.payment_provider=='paypal' else settings.stripe_mode)

    def stripe():
        if app.state.payments is None:
            raise EconomyError("provider_not_configured", "Configured payment provider account, mode, credentials and checkout redirects are required", 503)
        return app.state.payments

    @app.get("/health")
    def health():
        return {"status": "ok", "service": "widemind-economy", "contract_version": 1}

    @app.post("/v1/runs")
    def create_run(body: RunInput, role: str = Depends(identity)):
        authorize(role, {"operator"})
        return economy.create_run(**body.model_dump())

    @app.get("/v1/runs/{run_id}/balance")
    def balance(run_id: Identifier, role: str = Depends(identity)):
        authorize(role, {"operator", "gateway", "agent"}, run_id)
        return economy.balance(run_id)

    @app.get("/v1/runs/{run_id}/runtime-summary")
    def runtime_summary(run_id: Identifier, role: str = Depends(identity)):
        authorize(role, {"operator", "gateway"})
        from .service import now
        balance=economy.balance(run_id);totals=balance['totals'];metrics=balance['metrics']
        return {'currency':'USD','openingCreditMicros':str(totals.get('opening_grant',0)),'postedMicros':str(balance['posted_micro_usd']),'reservedMicros':str(balance['reserved_micro_usd']),'holdsMicros':str(balance['held_micro_usd']),'spendableMicros':str(balance['available_micro_usd']),'customerReceiptsMicros':str(totals.get('customer_receipt',0)),'earnedRevenueMicros':str(metrics['earned_revenue_micro_usd']),'operatingChargesMicros':str(-totals.get('operating_charge',0)),'processorFeesMicros':str(-totals.get('processor_fee',0)),'refundsMicros':str(-totals.get('refund',0)-totals.get('chargeback',0)),'adjustmentsMicros':str(sum(value for category,value in totals.items() if category not in {'opening_grant','owner_funding','test_funding','customer_receipt','operating_charge','processor_fee','refund','chargeback'})),'ownerFundingMicros':str(totals.get('owner_funding',0)+totals.get('test_funding',0)),'subsidiesMicros':str(metrics['infrastructure_subsidy_micro_usd']),'mode':'live' if balance['mode']=='live' else 'sandbox','asOf':now()}

    @app.get("/v1/runs/{run_id}/ledger")
    def ledger(run_id: Identifier, role: str = Depends(identity)):
        authorize(role, {"operator", "gateway", "agent"}, run_id)
        return economy.ledger(run_id)

    @app.get("/v1/runs/{run_id}/reservations")
    def reservations(run_id: Identifier, role: str = Depends(identity)):
        authorize(role, {"operator", "gateway", "agent"}, run_id)
        return economy.reservations(run_id)

    @app.get("/v1/runs/{run_id}/events")
    def events(run_id: Identifier, after: Annotated[int, Query(ge=0, le=9_223_372_036_854_775_807)] = 0, role: str = Depends(identity)):
        authorize(role, {"operator", "gateway", "agent"}, run_id)
        return economy.events(run_id, after=after)

    @app.get("/v1/runs/{run_id}/payments")
    def payments_read(run_id: Identifier, role: str = Depends(identity)):
        authorize(role, {"operator", "gateway", "agent"}, run_id)
        return economy.payments(run_id)

    @app.get("/v1/runs/{run_id}/reconciliations")
    def reconciliations(run_id: Identifier, role: str = Depends(identity)):
        authorize(role, {"operator", "gateway", "agent"}, run_id)
        return economy.reconciliations(run_id)

    @app.get("/v1/runs/{run_id}/charges")
    def charges(run_id: Identifier, role: str = Depends(identity)):
        authorize(role, {"operator", "gateway", "agent"}, run_id)
        return economy.charges(run_id)

    @app.get("/v1/runs/{run_id}/checkouts")
    def checkouts(run_id: Identifier, role: str = Depends(identity)):
        authorize(role, {"operator", "gateway", "agent"}, run_id)
        return economy.checkouts(run_id)

    @app.post("/v1/runs/{run_id}/reservations")
    def reserve(run_id: Identifier, body: ReserveInput, role: str = Depends(identity)):
        authorize(role, {"operator", "gateway", "agent"}, run_id)
        return economy.reserve(run_id, **body.model_dump())

    @app.get("/v1/reservations/{reservation_id}")
    def reservation(reservation_id: Identifier, role: str = Depends(identity)):
        authorize(role, {"operator", "gateway"})
        return economy.reservation(reservation_id)

    @app.post("/v1/reservations/{reservation_id}/start")
    def start(reservation_id: Identifier, role: str = Depends(identity)):
        authorize(role, {"operator", "gateway"})
        return economy.start_reservation(reservation_id)

    @app.post("/v1/reservations/{reservation_id}/settle")
    def settle(reservation_id: Identifier, body: SettleInput, role: str = Depends(identity)):
        authorize(role, {"operator", "gateway"})
        return economy.settle(reservation_id, **body.model_dump())

    @app.post("/v1/reservations/{reservation_id}/cancel")
    def cancel(reservation_id: Identifier, role: str = Depends(identity)):
        authorize(role, {"operator", "gateway"})
        return economy.cancel_reservation(reservation_id)

    @app.post("/v1/runs/{run_id}/holds")
    def hold(run_id: Identifier, body: HoldInput, role: str = Depends(identity)):
        authorize(role, {"operator"})
        return economy.set_hold(run_id, **body.model_dump())

    @app.post("/v1/runs/{run_id}/funding")
    def fund(run_id: Identifier, body: FundingInput, role: str = Depends(identity)):
        authorize(role, {"operator"})
        return economy.record_funding(run_id, **body.model_dump())

    @app.post("/v1/runs/{run_id}/subsidies")
    def subsidy(run_id: Identifier, body: ReferenceInput, role: str = Depends(identity)):
        authorize(role, {"operator"})
        return economy.record_subsidy(run_id, **body.model_dump())

    @app.post("/v1/runs/{run_id}/earned-revenue")
    def earned(run_id: Identifier, body: EarnedInput, role: str = Depends(identity)):
        authorize(role, {"operator"})
        return economy.record_earned_revenue(run_id, **body.model_dump())

    @app.post("/v1/runs/{run_id}/corrections")
    def correction(run_id: Identifier, body: CorrectionInput, role: str = Depends(identity)):
        authorize(role, {"operator"})
        return economy.correct_entry(run_id, **body.model_dump())

    @app.post("/v1/runs/{run_id}/checkout")
    def checkout(run_id: Identifier, body: CheckoutInput, role: str = Depends(identity)):
        authorize(role, {"operator"})
        return stripe().create_checkout(run_id, **body.model_dump())

    @app.get("/v1/payments/{payment_id}/snapshot")
    def payment_snapshot(payment_id: ProviderPaymentId, reservation_id: Identifier | None = None, role: str = Depends(identity)):
        authorize(role, {"operator", "gateway"})
        return economy.payment_snapshot(*scope(),payment_id,reservation_id)

    @app.post("/v1/payments/{payment_id}/reconcile")
    def reconcile(payment_id: ProviderPaymentId, role: str = Depends(identity)):
        authorize(role, {"operator"})
        return stripe().reconcile(payment_id)

    @app.post("/v1/payments/{payment_id}/schedule", status_code=202)
    def schedule_payment(payment_id: ProviderPaymentId, role: str = Depends(identity)):
        authorize(role, {"operator"})
        stripe()
        return queue.enqueue_payment(*scope(), payment_id)

    @app.get("/v1/reconciliation/tasks")
    def reconciliation_tasks(state: Literal["pending", "running", "completed", "dead_letter"] | None = None,
                             limit: Annotated[int, Query(ge=1, le=100)] = 100,
                             role: str = Depends(identity)):
        authorize(role, {"operator"})
        stripe()
        return queue.list_tasks(*scope(), state=state, limit=limit)

    @app.post("/v1/reconciliation/tasks/{task_id}/retry")
    def retry_reconciliation(task_id: Identifier, body: RetryInput, role: str = Depends(identity)):
        authorize(role, {"operator"})
        stripe()
        # Scope task selection to this configured account/mode before mutation.
        # The queue itself enforces this scope through the explicit task check.
        task = queue.task(task_id)
        if (task["provider"], task["account"], task["mode"]) != scope():
            raise EconomyError("not_found", "Reconciliation task not found", 404)
        return queue.retry(task_id, body.reason, body.idempotency_key)

    @app.post("/v1/checkouts/{request_id}/recover")
    def recover_checkout(request_id: Identifier, role: str = Depends(identity)):
        authorize(role, {"operator"})
        return stripe().recover_checkout(request_id)

    @app.get("/v1/runs/{run_id}/financial-summary")
    def financial_summary(run_id: Identifier, role: str = Depends(identity)):
        authorize(role, {"operator"})
        from .reporting import public_summary
        return public_summary(economy, run_id)

    @app.get("/v1/runs/{run_id}/integrity")
    def integrity(run_id: Identifier, role: str = Depends(identity)):
        authorize(role, {"operator"})
        from .reporting import audit_integrity
        return audit_integrity(economy, run_id)

    @app.post("/v1/payments/{payment_id}/release-hold")
    def release_hold(payment_id: ProviderPaymentId, body: IdempotencyInput, role: str = Depends(identity)):
        authorize(role, {"operator"})
        stripe()
        return economy.release_payment_hold(*scope(),payment_id,body.idempotency_key)

    @app.post("/v1/payments/{payment_id}/capture")
    def capture(payment_id:ProviderPaymentId,body:IdempotencyInput,role:str=Depends(identity)):
        authorize(role,{"operator"})
        adapter=stripe()
        if not hasattr(adapter,'capture'):raise EconomyError('provider_operation_unavailable','Capture is not exposed by this provider',503)
        return adapter.capture(payment_id,body.idempotency_key)

    @app.post("/v1/payments/{payment_id}/refund")
    def refund(payment_id:ProviderPaymentId,body:RefundInput,role:str=Depends(identity)):
        authorize(role,{"operator"})
        adapter=stripe()
        if not hasattr(adapter,'create_refund'):raise EconomyError('provider_operation_unavailable','Refund initiation is not exposed by this provider',503)
        return adapter.create_refund(payment_id,body.request_id,body.amount_micro_usd)

    @app.post("/v1/webhooks/paypal",status_code=202)
    async def paypal_webhook(request:Request):
        adapter=stripe()
        if getattr(adapter,'provider','stripe')!='paypal':raise EconomyError('provider_not_configured','PayPal is not the selected provider',503)
        raw=await request.body();event,digest=await run_in_threadpool(adapter.verify_webhook,raw,dict(request.headers))
        return queue.enqueue_verified_event(event,digest,*scope())

    @app.post("/v1/provider/verify")
    def verify_provider(role: str = Depends(identity)):
        authorize(role, {"operator"})
        return stripe().verify_setup()

    @app.post("/v1/webhooks/stripe", status_code=202)
    async def webhook(request: Request, stripe_signature: Annotated[str | None, Header()] = None):
        if stripe_signature is None:
            raise EconomyError("invalid_signature", "Stripe signature is required", 400)
        raw_body = await request.body()
        adapter = stripe()
        if getattr(adapter,"provider","stripe")!="stripe":raise EconomyError("provider_not_configured","Stripe is not the selected provider",503)
        event, digest = adapter.verify_webhook(raw_body, stripe_signature)
        return await run_in_threadpool(queue.enqueue_verified_event, event, digest,
                                       "stripe", settings.stripe_account, settings.stripe_mode)

    return app
