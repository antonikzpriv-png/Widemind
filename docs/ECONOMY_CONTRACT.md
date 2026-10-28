# Economy integration contract v1

The fetched foundation is documentation-only (`fc6df21`). This task selects Python 3.12+, FastAPI, HTTPX and SQLite for the economy service only. It does not implement a worker or website. Money is an integer micro-USD, never a float; one run has a fixed `test` or `live` mode. Times are UTC. Identifiers follow ARCHITECTURE.md. All responses are JSON-compatible dictionaries.

## Trusted service interface

`EconomyService(database_path)` migrates the separate accounting database. Public methods:

- `create_run(run_id, mode='test')`: atomically seed exactly 5,000,000 once; replay with a different mode conflicts.
- `balance(run_id)`, `ledger(run_id)`, `reservations(run_id)`, `events(run_id, after=0)`, `payments(run_id)`, `reconciliations(run_id)`: private read projections.
- `reserve(run_id, action_id, amount_micro_usd, idempotency_key, provider, account, limits)`: atomic admission; store the bounded adapter limits. Reusing an action/key with different parameters conflicts.
- `start_reservation(reservation_id)`: persist dispatch before an external call; a repeat never authorizes redispatch.
- `settle(reservation_id, actual_micro_usd, provider, account, external_charge_id, usage=None, outcome='succeeded')`: actual integer cost atomically posts and releases unused reservation; `None` marks unknown and retains the reservation. A failed call with a known charge still posts. Explicit later trusted settlement reconciles unknown. Known overages remain visible even below zero. For a verified known subtotal with an unresolved outcome, submit `outcome=unknown` with the subtotal: it posts that charge and retains the unused original cap. Final reconciliation with the same charge ID supplies the total, posts the difference, and releases the remainder. A pure timeout submits `actual_micro_usd=None` and retains the full cap. A subtotal cannot decrease except through final reconciliation.
- `cancel_reservation(reservation_id)`: only an undispatched reservation can be released without provider reconciliation.
- `set_hold(run_id, hold_id, amount_micro_usd, kind, reason, idempotency_key)`: audited customer/refund exposure, may exceed balance.
- `record_funding(run_id, amount_micro_usd, category, reference, idempotency_key)`: operator-only owner funding/test funding; test funds prohibited in live runs.
- `record_subsidy(run_id, amount_micro_usd, reference, idempotency_key)`: noncash infrastructure support, does not increase available credit.
- `record_earned_revenue(run_id, amount_micro_usd, job_id, reference, idempotency_key)`: performance metric, does not increase cash.
- `correct_entry(run_id, entry_id, amount_micro_usd, reason, idempotency_key)`: operator-only append-only compensating correction.

## Payment adapter boundary

`register_payment(run_id, provider, account, payment_id, job_id, idempotency_key)` binds a provider payment to a run before accepting receipts. Provider account includes a separately enforced mode; the same external ID in another account is not the same object. Checkout stores its durable request before the network call and retries only with the same provider idempotency key.

`apply_payment_snapshot(snapshot, event_id=None, event_digest=None)` is a trusted adapter call, never exposed to the agent. Snapshot fields:

```
provider: str; account: str; mode: 'test'|'live'; payment_id: str;
run_id: str; job_id: str | null; status: str; expected_revision: int;
transactions: list[{
  transaction_id: str,
  kind: 'receipt'|'refund'|'chargeback'|'chargeback_reversal'|'adjustment',
  amount_micro_usd: int, fee_micro_usd: int, net_micro_usd: int,
  currency: 'USD', settled: bool,
  native: dict
}]
pending_liability_micro_usd: int (default 0)
```

Read `payment(...).revision` before fetching provider state and supply it as `expected_revision`. A concurrent change raises `stale_snapshot`; fetch current provider state again, with bounded retries. This also fences fulfillment/hold changes against stale snapshots across processes.

Each balance transaction is immutable and idempotent by provider/account/mode/transaction ID, independently of webhook-event IDs. `net = amount - fee`: ledger posts gross once and fee once. Gross and fee components post independently once each. Every negative component is an immediate liability, including known fees on a pending receipt. Positive receipt, chargeback reversal, adjustment and fee-rebate components require verified availability. A pending refund with a fee rebate therefore debits gross immediately and credits the rebate only on settlement. Original provider observations are preserved; changing availability/timestamps does not change immutable monetary amounts. Reconciliation records append subsequent native observations. Snapshots come from fresh authenticated provider reads, never webhook arrival order, model claims or metadata alone. Refund/chargeback events received before the receipt preserve the debit, and later receipt posting cannot erase it.

Webhook event IDs deduplicate per provider/account/mode. Same ID with a different digest conflicts. Unknown payment mapping fails closed for retry/reconciliation. Complete authoritative snapshots reconcile payment/hold projections. Positive receipt net automatically receives a customer hold; an operator fulfillment decision releases it via `release_payment_hold(provider, account, mode, payment_id, idempotency_key)`. Later refunds/disputes still post and may make the wallet negative. Pending exposure remains held. No money is granted by earned revenue, pending invoices, payment-link creation or checkout-completed events.

`payment(provider, account, mode, payment_id)` reads payment status. `record_checkout(run_id, provider, account, mode, request_id, job_id, amount_micro_usd, details)` durably claims a checkout request (same parameters on retry); `finish_checkout(..., request_id, payment_id, url)` persists result and registers the payment; `checkout(...)` reads it. The adapter uses the persisted request ID as its provider idempotency key and retrieves session state after uncertainty.

## Error and event shapes

`EconomyError(code, message, status=409)`; attributes `code`, `status`. Unknown IDs use 404; invalid integer/identity input uses 422; insufficient funds uses 409.

Every new mutation appends an event in the same SQLite transaction: `{sequence, event_id, version: 1, type, run_id, occurred_at, data}`. `sequence` supports cursor polling without paid inference. Idempotent replays return the original result without repeating financial mutations. Balances also expose noncash `metrics.earned_revenue_micro_usd` and `metrics.infrastructure_subsidy_micro_usd`. Agent reads are limited to its configured run; operator/gateway reads are trusted. Private projections omit provider secrets and raw webhook bodies. Public publication is a future separate allowlist projection.

## Authorization boundary

The economy database, service code and credentials stay outside the agent workspace. Separate constant-time bearer identities: operator (funding, corrections, fulfillment, checkout and reconciliation), gateway (reserve/start/settle for trusted provider adapters), agent (private balance/status reads and reserve only). Webhooks require raw-body signature verification. No HTTP route permits arbitrary journal writes. Single-host SQLite service owns all accounting writes, with WAL and BEGIN IMMEDIATE; multiple service processes coordinate through the same local database, not separate copies or NFS.
