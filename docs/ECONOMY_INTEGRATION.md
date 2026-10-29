# Integrated economy and PayPal Business

The Node runtime now uses the existing audited Python economy as an independent accounting authority. Source and inherited tests live in `services/economy/`; this is not a second wallet. The economy database, service code and credentials belong to the trusted control-plane identity, outside the agent workspace. Python services must have separate virtual environments because their packages share the `widemind` namespace.

## Install and start

The root service setup/launcher can install and run this service. Individually:

```sh
python3 -m venv services/economy/.venv
services/economy/.venv/bin/python -m pip install -r services/economy/requirements.lock -e services/economy
services/economy/.venv/bin/python -m widemind.economy.cli --host 127.0.0.1 --port 8080
```

Its environment requires distinct random `WIDEMIND_OPERATOR_TOKEN`, `WIDEMIND_GATEWAY_TOKEN`, `WIDEMIND_AGENT_TOKEN`, a single scoped `WIDEMIND_AGENT_RUN_ID`, and a private persistent `WIDEMIND_ECONOMY_DB`. The Node module instead reads `WIDEMIND_ECONOMY_URL` and the prefixed `WIDEMIND_ECONOMY_OPERATOR_TOKEN`, `WIDEMIND_ECONOMY_GATEWAY_TOKEN`, `WIDEMIND_ECONOMY_AGENT_TOKEN`; the root launcher maps these to each process. Never commit tokens or copy them into model prompts.

With no configuration, economy remains unavailable. In the explicit `demo` profile this module registers no provider, preserving the original zero-cost local demonstration. Constructors perform no provider requests.

## Opening and spending

Creating a runtime run does not create money. An authenticated operator explicitly opens its wallet through `/api/modules/economy/open` with `{runId,mode:'sandbox',confirmOpeningCredit:true}`. This creates exactly $5 of **sandbox accounting credit**, idempotently. A live opening additionally needs `WIDEMIND_ENABLE_LIVE_EFFECTS=true` and an operator `fundingReference` for real backing; it is recorded as operator-attested credit, not an independently verified bank transfer. Claims, invoices and provider approval redirects never credit a wallet.

Money in Node contracts is canonical integer micro-USD text. The HTTP integer bridge rejects amounts outside `0..9000000000000000` rather than rounding; exact financial summaries return decimal strings even for aggregate balances beyond JavaScript's safe integer range. Limits bind the tool, dedicated account, action and reservation. Authorizations are HMAC-bound and also inspected against current authoritative reservation records. Zero-cost actions obtain actual durable reservations without reserving fictitious spend. Only the trusted tools service calls `/start`; duplicate dispatch is refused.

Final charges and charged failures settle idempotently. Provisional usage posts a known subtotal with `outcome=unknown`, retains the remaining reservation and returns unavailable until final provider reconciliation. Missing or uncertain outcomes never release funds as if execution had been free. Migration 5 adds zero-cost authorizations while preserving prior reservations, charges, relationships and immutable identity triggers. Stop services before upgrading; database downgrade is refused.

## PayPal Business configuration

`WIDEMIND_PAYMENT_PROVIDER=paypal` is the default active provider. Existing Stripe code and variables are retained for an explicit `stripe` deployment and are not repurposed as PayPal credentials.

Configure dedicated `WIDEMIND_PAYPAL_CLIENT_ID`, `WIDEMIND_PAYPAL_CLIENT_SECRET`, `WIDEMIND_PAYPAL_MERCHANT_ID`, `WIDEMIND_PAYPAL_WEBHOOK_ID`, `WIDEMIND_PAYPAL_MODE=test` (or `live`), and checkout return/cancel URLs through `WIDEMIND_CHECKOUT_SUCCESS_URL` / `WIDEMIND_CHECKOUT_CANCEL_URL`. A live PayPal deployment additionally requires explicit live-effects permission. Live URLs require HTTPS; loopback return URLs are accepted only in test mode.

The service uses OAuth2 at PayPal's fixed sandbox/live API origins. Hosted Orders checkout stores its request before sending and uses a stable `PayPal-Request-Id`. The approval URL is only a customer approval step. `/v1/payments/{order_id}/capture` accepts `{idempotency_key}` after approval; an authenticated GET of order/capture data establishes actual receipts, fees and net. The configured merchant and capture's related order identity must match. Customer receipts are held until explicit verified fulfillment; earned revenue remains a separate performance record.

PayPal webhooks post to `/api/modules/economy/webhook` on the public web host (forwarded with exact body and PayPal signature headers), or `/v1/webhooks/paypal` on the economy host. The adapter calls PayPal's authenticated webhook-signature verification API using the configured webhook ID, then enqueues the verified event durably. Reconciliation uses fresh provider reads, not event amounts or arrival order. The model-free reconciliation worker is:

```sh
services/economy/.venv/bin/python -m widemind.economy.cli worker
```

`/v1/payments/{order_id}/refund` accepts `{request_id,amount_micro_usd}`. It records the request/hold before the external request, uses stable provider idempotency, verifies the returned refund against its capture and amount, persists its identity and reconciles actual liabilities. Replays of confirmed requests perform GET recovery, not another POST. Unknown unbound creations older than the conservative five-hour retry window require manual provider-side identification; the service refuses a blind repeated request. Already accepted external requests cannot be recalled by a local timeout.

Do not enable the live pilot without dedicated account onboarding, required PayPal permissions, merchant/webhook verification, HTTPS hosting and an approved network policy for `api-m.sandbox.paypal.com` / `api-m.paypal.com`. Those dependencies were not configured or exercised during implementation.

## Shared helpers and APIs

`src/modules/economy/client.ts` exports `createEconomyClient(env)`, `createEconomyAdapter(client)`, exact monetary conversion, scoped HTTP requests, reservation authorization/settlement and raw verified-webhook forwarding. Operator-only Node routes expose status, explicit opening, owner/test funding, checkout, capture, refund, reconciliation, hold release and provider verification. They inherit the web application's session, Origin and CSRF checks. There is no customer-receipt posting endpoint exposed to the agent.

The Python `/v1/payments/{id}/snapshot?reservation_id=<id>` returns a job-scoped verified accounting projection for business fulfillment; a reservation from another run is rejected. Without an execution reservation it does not claim final operating costs. Public financial publication comes from fixed summary fields rather than raw journals/customer details.

## Verified scope

The inherited accounting/payment suite tests concurrent reservations, unknown outcomes, idempotent entries, refunds/chargebacks, migration and ledger consistency. Added tests use controlled PayPal HTTP records for OAuth, checkout, capture, verified webhook delivery, duplicate events, refund recovery, wrong-merchant rejection and live custom-endpoint refusal. A separate Node test starts the real Python HTTP service and verifies explicit sandbox opening, concurrent admission, zero-cost dispatch, charged failures and provisional/final settlement.

All provider records in those checks are clearly labelled fixtures. No live PayPal or Stripe payment, customer, bank transfer or profitable survival result is claimed.

For full-stack tests only, `WIDEMIND_PAYPAL_API_BASE` may select a loopback test server if **both** `WIDEMIND_PAYPAL_MODE=test` and `WIDEMIND_PAYPAL_ALLOW_TEST_ENDPOINT=true`. Live mode rejects custom origins. This flag does not turn fixtures into real sandbox evidence and must stay unset in production.

### Reconciliation and precision details

The runtime summary also publishes exact `processorFeesMicros`, gross `refundsMicros` (including chargebacks), and signed `adjustmentsMicros`. Posted cash reconciles as opening + owner/test funding + customer receipts − operating charges − processor fees − refunds + adjustments. Noncash earned/subsidy metrics are not included in posted cash. The business snapshot's `revision` is the run's monotonic accounting event sequence read in the same transaction as costs and earnings; `provider_revision` separately identifies the payment-state revision. Callers must pass their own stored execution reservation when requesting final cost metrics; only run ownership can be checked by the economy service without the business module's job store.

Positive PayPal receipts require an exact verified gross/fee/net breakdown. Refunds are bound to their authenticated capture parent. Pending refunds retain uncertainty; a subsequently failed pending refund receives one idempotent reversal of the previously recorded liability. Completed-refund fee rebates are separate verified fee components, not a second gross receipt. Refund webhooks without an order ID resolve it through the authenticated capture parent. Confirmed refund IDs are persisted so later recovery reads them rather than repeating the external action.

Current local validation: **382 Python tests**, including **12 PayPal integration cases**, and **3 Node economy bridge tests** pass. The Node bridge starts the actual isolated HTTP ledger service when its venv is installed, preferring root `.venvs/economy` and otherwise `services/economy/.venv`; absent Python installation is explicitly reported as a skipped integration, never a live-success claim. Generic numeric JSON containing unsafe integers fails closed; no silently rounded financial value is accepted.

The audit adds durable-queue capture/refund tests: only a resource ID survives queue minimization, then the adapter retrieves capture/refund/parent bindings with authenticated GETs. It never relies on retained webhook amounts or customer data. Unsupported event types remain unresolved; automatic PayPal dispute/chargeback ingestion is unavailable and requires operator review.
