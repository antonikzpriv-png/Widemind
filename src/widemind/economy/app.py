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

MAX_MONEY = 9_000_000_000_000_000
Money = Annotated[StrictInt, Field(ge=0, le=MAX_MONEY)]
PositiveMoney = Annotated[StrictInt, Field(gt=0, le=MAX_MONEY)]
SignedMoney = Annotated[StrictInt, Field(ge=-MAX_MONEY, le=MAX_MONEY)]
Identifier = Annotated[StrictStr, Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$")]
Note = Annotated[StrictStr, Field(min_length=1, max_length=200)]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RunInput(Input):
    run_id: Identifier
    mode: Literal["test", "live"] = "test"


class ReserveInput(Input):
    action_id: Identifier
    amount_micro_usd: PositiveMoney
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
    amount_micro_usd: PositiveMoney
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


class IdempotencyInput(Input):
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
    if payments is None and settings.stripe_enabled:
        from .stripe import StripePayments
        payments = StripePayments(
            economy, api_key=settings.stripe_api_key, webhook_secret=settings.stripe_webhook_secret,
            account=settings.stripe_account, mode=settings.stripe_mode,
            success_url=settings.checkout_success_url, cancel_url=settings.checkout_cancel_url,
        )
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

    @app.exception_handler(EconomyError)
    async def economy_error(_request: Request, exc: EconomyError):
        return JSONResponse({"error": {"code": exc.code, "message": str(exc)}}, status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def input_error(_request: Request, _exc: RequestValidationError):
        # Pydantic's default response includes submitted values, potentially secrets.
        return JSONResponse({"error": {"code": "invalid_input", "message": "Request does not match the versioned economy contract"}}, status_code=422)

    def identity(authorization: Annotated[str | None, Header()] = None) -> str:
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

    def authorize(role: str, allowed: set[str], run_id: str | None = None) -> None:
        if role not in allowed:
            raise EconomyError("forbidden", "This identity cannot perform that operation", 403)
        if role == "agent" and run_id != settings.agent_run_id:
            raise EconomyError("forbidden", "Agent identity is not authorized for this run", 403)

    def stripe():
        if app.state.payments is None:
            raise EconomyError("provider_not_configured", "Stripe account, mode, credentials and checkout redirects must be configured", 503)
        return app.state.payments

    @app.get("/health")
    def health():
        return {"status": "ok", "service": "widemind-economy", "contract_version": 1}

    @app.post("/v1/runs")
    def create_run(body: RunInput, role: str = Depends(identity)):
        authorize(role, {"operator"})
        return economy.create_run(**body.model_dump())

    @app.get("/v1/runs/{run_id}/balance")
    def balance(run_id: str, role: str = Depends(identity)):
        authorize(role, {"operator", "gateway", "agent"}, run_id)
        return economy.balance(run_id)

    @app.get("/v1/runs/{run_id}/ledger")
    def ledger(run_id: str, role: str = Depends(identity)):
        authorize(role, {"operator", "gateway", "agent"}, run_id)
        return economy.ledger(run_id)

    @app.get("/v1/runs/{run_id}/reservations")
    def reservations(run_id: str, role: str = Depends(identity)):
        authorize(role, {"operator", "gateway", "agent"}, run_id)
        return economy.reservations(run_id)

    @app.get("/v1/runs/{run_id}/events")
    def events(run_id: str, after: Annotated[int, Query(ge=0, le=9_223_372_036_854_775_807)] = 0, role: str = Depends(identity)):
        authorize(role, {"operator", "gateway", "agent"}, run_id)
        return economy.events(run_id, after=after)

    @app.get("/v1/runs/{run_id}/payments")
    def payments_read(run_id: str, role: str = Depends(identity)):
        authorize(role, {"operator", "gateway", "agent"}, run_id)
        return economy.payments(run_id)

    @app.get("/v1/runs/{run_id}/reconciliations")
    def reconciliations(run_id: str, role: str = Depends(identity)):
        authorize(role, {"operator", "gateway", "agent"}, run_id)
        return economy.reconciliations(run_id)

    @app.post("/v1/runs/{run_id}/reservations")
    def reserve(run_id: str, body: ReserveInput, role: str = Depends(identity)):
        authorize(role, {"operator", "gateway", "agent"}, run_id)
        return economy.reserve(run_id, **body.model_dump())

    @app.post("/v1/reservations/{reservation_id}/start")
    def start(reservation_id: str, role: str = Depends(identity)):
        authorize(role, {"operator", "gateway"})
        return economy.start_reservation(reservation_id)

    @app.post("/v1/reservations/{reservation_id}/settle")
    def settle(reservation_id: str, body: SettleInput, role: str = Depends(identity)):
        authorize(role, {"operator", "gateway"})
        return economy.settle(reservation_id, **body.model_dump())

    @app.post("/v1/reservations/{reservation_id}/cancel")
    def cancel(reservation_id: str, role: str = Depends(identity)):
        authorize(role, {"operator", "gateway"})
        return economy.cancel_reservation(reservation_id)

    @app.post("/v1/runs/{run_id}/holds")
    def hold(run_id: str, body: HoldInput, role: str = Depends(identity)):
        authorize(role, {"operator"})
        return economy.set_hold(run_id, **body.model_dump())

    @app.post("/v1/runs/{run_id}/funding")
    def fund(run_id: str, body: FundingInput, role: str = Depends(identity)):
        authorize(role, {"operator"})
        return economy.record_funding(run_id, **body.model_dump())

    @app.post("/v1/runs/{run_id}/subsidies")
    def subsidy(run_id: str, body: ReferenceInput, role: str = Depends(identity)):
        authorize(role, {"operator"})
        return economy.record_subsidy(run_id, **body.model_dump())

    @app.post("/v1/runs/{run_id}/earned-revenue")
    def earned(run_id: str, body: EarnedInput, role: str = Depends(identity)):
        authorize(role, {"operator"})
        return economy.record_earned_revenue(run_id, **body.model_dump())

    @app.post("/v1/runs/{run_id}/corrections")
    def correction(run_id: str, body: CorrectionInput, role: str = Depends(identity)):
        authorize(role, {"operator"})
        return economy.correct_entry(run_id, **body.model_dump())

    @app.post("/v1/runs/{run_id}/checkout")
    def checkout(run_id: str, body: CheckoutInput, role: str = Depends(identity)):
        authorize(role, {"operator"})
        return stripe().create_checkout(run_id, **body.model_dump())

    @app.post("/v1/payments/{payment_id}/reconcile")
    def reconcile(payment_id: str, role: str = Depends(identity)):
        authorize(role, {"operator"})
        return stripe().reconcile(payment_id)

    @app.post("/v1/payments/{payment_id}/release-hold")
    def release_hold(payment_id: str, body: IdempotencyInput, role: str = Depends(identity)):
        authorize(role, {"operator"})
        stripe()
        return economy.release_payment_hold("stripe", settings.stripe_account, settings.stripe_mode,
                                            payment_id, body.idempotency_key)

    @app.post("/v1/provider/verify")
    def verify_provider(role: str = Depends(identity)):
        authorize(role, {"operator"})
        return stripe().verify_setup()

    @app.post("/v1/webhooks/stripe")
    async def webhook(request: Request, stripe_signature: Annotated[str | None, Header()] = None):
        if stripe_signature is None:
            raise EconomyError("invalid_signature", "Stripe signature is required", 400)
        raw_body = await request.body()
        return await run_in_threadpool(stripe().handle_webhook, raw_body, stripe_signature)

    return app
