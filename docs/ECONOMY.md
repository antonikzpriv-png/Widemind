# WideMind economy

## Requirements and status

Each survival run starts with $5 of operating credit. Real earnings replenish its operating budget; paid activity reduces it.

The accounting system enforces this independently of the agent. Generated invoices, promised payments, and agent statements cannot create credit.

The rules below govern the typed economy interface now consumed by the persistent runtime. The foundation implements reservation/settlement wiring and explicit unavailable behavior, not the funded ledger or payment integration. No opening credit or customer revenue is invented. See [INTERFACES.md](INTERFACES.md).

## Units

Use integer micro-USD internally:

- `1 USD = 1,000,000 micro-USD`.
- Opening grant: `5,000,000 micro-USD`.

Never use floating-point arithmetic for balances. Preserve provider-native currency, amounts, conversion rates, and rounding details. Display readable USD amounts in the interface.

## Ledger

Use append-only financial entries with unique event identifiers and supporting-record references.

Suggested entry types:
- Opening grant.
- Additional owner funding.
- Customer payment.
- Processor fee.
- Operating charge.
- Refund or chargeback.
- Reconciliation adjustment.

Corrections use compensating entries rather than rewriting history.

Payment webhooks, polling, tool callbacks, and retries must be idempotent. Scope external event IDs by provider/account. Processing the same event twice must not change the balance twice.

Maintain spending reservations and customer/refund holds with auditable state transitions.

## Spendable credit

`spendable = posted balance - active spending reservations - customer/refund holds`

`posted balance = grants + settled receipts - fees - operating charges - refunds/chargebacks +/- adjustments`

Do not subtract fees twice when a processor supplies a net amount. Preserve the gross receipt, fees, and net calculation.

Customer funds for unfinished work may require a hold. Define when holds are released and how refund/dispute exposure is reserved.

A settled receipt is a cash event. Earned revenue is a performance measure tied to fulfilled work. Report both without conflating them.

## Spending lifecycle

1. Estimate a bounded cost for an action.
2. Reserve that amount atomically.
3. Enforce the corresponding provider/tool limit.
4. Execute.
5. Post actual charges and reconcile the reservation atomically.
6. Release unused reservation.

Concurrent actions cannot reserve the same credit.

Choose enforceable token, runtime, storage, and tool limits. A reservation alone does not prevent an unbounded provider request from producing a larger bill.

If an action times out, retain an appropriate reservation and reconcile its charge and external effect before retrying. Failure is not proof of free execution.

Record billable failures, retries, partial work, and abandoned attempts.

Where exact charges arrive later, identify provisional costs and reconcile them against provider usage or invoices. Unexpected liability remains visible even if it creates a negative balance; stop new paid execution and report the discrepancy.

## Revenue lifecycle

Track pending, settled, refunded, and disputed payments.

Only authenticated processor events or verified settlement reconciliation can post money. A customer screenshot or model assertion is insufficient.

Associate receipts, fees, refunds, and chargebacks with the original job or product sale.

Use the configured business payment infrastructure. WideMind can inspect payment status through adapters; settlement and crediting rules belong to the economy service.

Before accepting a job, ensure the combination of available credit, payment timing, and allowed customer-fund release can fund fulfillment. Do not accept an obligation on the assumption that unrelated owner money will cover it.

## Cost boundary

Track:
- Model inference and paid tools.
- Graphics/media generation.
- Hosting, compute, storage, and network.
- Subscriptions and allocated shared services.
- Payment fees.
- Other configured business expenses.

Document shared/prepaid cost allocation. Free access and owner subsidies remain visible so apparent survival does not hide external support.

Keep development expenses separate from run operations and disclose them in experiment reporting. If a pilot excludes a cost from its spendable wallet, report that exclusion explicitly.

## Waiting and exhaustion

Use event delivery or inexpensive scheduling while idle. Hosting may still incur cost.

When credit cannot fund a proposed action, WideMind waits, chooses an affordable alternative, or enters an exhausted state. It cannot draw from unrelated funds or silently obtain owner top-ups.

Keep reconciliation and operator recovery available. Previously earned funds may settle after exhaustion. Record reactivation and its funding source.

Additional owner funding is labelled separately from autonomous earnings. Decide run-ending and reactivation rules before a live experiment.

## Required demonstrations

- Two concurrent actions cannot overspend the same credit.
- Duplicate/out-of-order payment events produce correct balances.
- Failed paid calls remain charged.
- Reservations recover correctly after restart.
- Refunds and chargebacks reduce available funds correctly.
- Claimed earnings without settlement create no credit.
- WideMind cannot edit its balance or enforcement rules.
- Public financial totals reconcile with the private ledger.
