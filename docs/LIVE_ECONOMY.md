# Protected economy and Stripe integration

This module implements the host-side economy contract consumed by the business module. It is **not** a model-controlled wallet. The private economy database, credentials, operator funding functions, and provider billing enforcement must be outside the agent's writable/executable workspace. The Python classes establish a logical boundary; deployment must enforce that boundary with separate OS identities/service access where the agent can run code.

## Setup

```python
from widemind.economy import EconomyService
from widemind.economy.stripe import StripeClient

stripe = StripeClient(
    api_key=secret_manager_stripe_key,
    webhook_secret=secret_manager_webhook_secret,
    mode="live",
    success_url="https://your-host/payment/complete",
    cancel_url="https://your-host/payment/cancelled",
    # account_id="acct_...",  # only for an explicitly configured connected account
)
economy = EconomyService("/protected/widemind/economy.sqlite3", mode="live", stripe=stripe)
```

Constructor calls create schema only, never funds. Before creating a funded run, the operator must verify actual available backing and run identity, then explicitly initialize it:

```python
economy.create_run("your-funded-run", opening_micro_usd=5_000_000,
                   evidence_reference="operator-funding-record-id")
```

`initialize_run` is an alias with the same signature. An identical repeat is idempotent; another opening amount or mode is rejected. The opening grant is labelled owner support, never customer revenue. A configured bank/Stripe cash account and actual funding must back a live run; a local opening record does not move money at a payment processor.

Create one request/worker-local `EconomyService` connection. Close it when done. Use private local SQLite storage, its WAL/shm files, consistent SQLite backups, OS resource limits, and a supervisor. Do not copy a live WAL database's main file alone. Mode is pinned at the database and run level. A `StripeClient(mode="test")` can only fund an `EconomyService(mode="test")`; test receipts are never live credit. Stripe secret/restricted credential prefixes must match the configured live/test mode before a network write is possible. Nonstandard fictional keys are accepted only with an explicitly injected offline transport; a contradictory standard key is always rejected. Demo fixtures remain a separate explicit module.

## Exact financial behavior

Internal USD amounts use integer micro-USD (`1 USD = 1_000_000`). Stripe Checkout requires whole native USD cents (`10_000` micro-USD); unrepresentable quotes fail instead of rounding silently. Foreign currencies are rejected pending a separately specified conversion policy.

The cash ledger is append-only; SQLite triggers reject row updates and deletes. Corrections append a separately evidenced compensating entry. `balance(run_id=...)` reports posted cash, active reservation liability, customer/refund holds, spendable credit, customer receipts, earned revenue, fees, operating charges, opening grants, additional owner funding, and subsidies separately. Public projections must use these aggregates rather than customer records or raw provider payloads.

`spendable = posted - active reservations - customer/refund holds`. Customer receipts for unfinished work remain held, so a customer payment cannot be treated as both free spending money and an unrecorded delivery obligation. Confirmed fulfillment releases that performance hold and recognizes earned revenue up to the agreed order price, less refunds/pending-refund exposure. Disputed receipts earn zero until resolved; verified cash debits and exposures remain visible. Actual fees are subtracted once. Full refunds, partial refunds, lost disputes, and recoveries append differences instead of rewriting prior receipts.

Owner-only `owner_funding` labels additional support or subsidies. `correct_operating_charge` references an actual charge and a unique evidenced correction event; it cannot invent negative operating expenses. These methods must not be exposed through public or model tool APIs.

## Gateway authorization contract

The existing `reserve`, `release`, `payment_link`, `payment`, `record_fulfillment`, and `reconcile` methods implement the business module's `Economy` protocol. Real tool execution additionally uses:

```python
economy.begin_execution(
    run_id=run_id, job_id=job_id,
    reservation_id=reservation_id, action_id=action_id,
    maximum_micro_usd=maximum_micro_usd,
)
economy.settle_execution(
    reservation_id=reservation_id, action_id=action_id,
    actual_micro_usd=verified_total_charge,
    evidence_reference=provider_usage_reference,
    provisional=False,
)
```

The reserve action is atomic and idempotent. `begin_execution` checks the reservation's run/job identities, exclusive execution owner, wallet coverage, and a frozen dispatch ceiling. Another action cannot consume it, and the same action cannot expand its ceiling later. A refund that makes reservation coverage negative blocks a dispatch not yet started. Settled or released reservations cannot fund another call.

The gateway must enforce provider token/runtime/tool limits corresponding to the ceiling; a bookkeeping reservation cannot cap an unconstrained provider request. It must persist its execution intent before dispatch and never repeat an unknown external action. A confirmed billable failure still settles its observed charge. Unknown usage/effects retain the reservation; do not settle an invented zero or release it on a timeout.

`actual_micro_usd` is the **cumulative actual total**, not a delta. `provisional=True` records a known minimum charge while retaining remaining liability. Final reconciliation appends only the additional observed cost and releases unused reservation. Exact duplicate final settlement is idempotent. Unexpected over-limit charges are posted even if they make the wallet negative, and an unresolved cost-limit breach blocks further paid work. Operator review and a new properly authorized run are required for recovery from such a breach in this version; an owner top-up alone does not hide it.

`costs_final` means no business job reservation remains unresolved for the reported record. It does not claim all hosting/subscription/shared development costs have been invoiced. The operator must define and post/allocate those costs through a verified control-plane accounting process before making a full profitability claim.

## Verified Stripe payments

The adapter creates Checkout sessions with stable idempotency keys and run/job/action metadata. Session creation is journaled locally **before** HTTP. A lost response remains unknown and is reconciled using bounded GET-only provider reads; no blind replacement payment link is created.

Configure a real HTTPS Stripe webhook endpoint in the integration host. Pass the **unaltered raw body** and `Stripe-Signature` header to:

```python
job_ids = economy.handle_webhook(raw_body, signature_header)
```

The host enqueues `payment.changed` events for those known order IDs using Stripe/account-scoped event identities. Webhook verification checks raw-body HMAC, timestamp tolerance, mode, and configured connected-account identity. Duplicate events are idempotent and conflicting reuse of an event identity is rejected. The event body is only an authenticated notification: current Stripe sessions/payment intents/charges/balance transactions/refunds/disputes are queried before accounting changes.

A successful charge or completed Checkout alone is not settled spendable credit. Receipts post only once the corresponding Stripe balance transaction is **available**. This is Stripe available-balance settlement, not a claim that a bank payout completed. Waiting transactions remain pending. `balance.available` notifications are authenticated/deduplicated but do not synchronously scan historical sessions. The host must schedule `reconcile_payments(run_id=..., limit=5, cursor=...)` batches for pending/partial/disputed sessions, resuming `next_cursor` until `complete` and then waiting for the next low-cost scheduled wakeup. Each batch shares a 10-second/20-request Stripe budget; individual webhook routing is likewise bounded and never loops through all historical orders. No model loop is needed. Payment reads query provider authority; transient/unavailable evidence fails closed rather than reporting stale settlement as current.

Native currency/cents, sanitized processor object IDs, gross/fee/refund/dispute references, and settlement basis are retained. No card details, customer email, raw customer payment payload, API credential, or signature secret enters the ledger metadata. API keys stay in the host secret store and never in model-visible configuration. Default urllib keeps inherited proxy settings and TLS trust; redirects are rejected and operation/body/request/page/time limits are enforced. Required egress includes `api.stripe.com`; no proxy bypass is implemented.

## Verification and remaining live prerequisites

Run `python -m unittest discover -s tests -p 'test_economy*.py' -v`.

All automated provider checks use explicitly identified offline transport fixtures; no real customer, bank transfer, payment, refund, card charge, or revenue was created in development. Tests cover concurrent exhaustion, cost failures, cross-run reservation swaps, ceiling changes, uncertain restart, duplicate settlement/events, out-of-order notification/current-state reads, stale concurrent reads, pending/partial/full refunds, disputes, signing/replay/mode/account checks, append-only corrections, owner support, Checkout uncertainty, response bounds, and credential-safe failures.

Before live operation, provide a correctly onboarded and funded Stripe account, scoped credentials, webhook secret and HTTPS endpoint, owned return URLs, permitted network egress, actual operating funding, explicit runtime autonomy, private storage, sandbox isolation, and a declared cost/refund policy. First qualify a separate **test-mode** service with Stripe sandbox credentials and controlled recipients. Then independently authorize a real live run. A fixture demonstration and a passing unit suite do not establish live settlement or profitable operation.
