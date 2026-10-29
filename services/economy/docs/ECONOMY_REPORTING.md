# Redacted economy reporting

`widemind.economy.reporting` provides two read-only functions. They do not create a website, authenticate callers, contact a payment provider, change balances, or repair accounting. The application must authorize the selected run. The economy API exposes these reads to the operator; publishing an approved summary elsewhere is a separate integration decision.

```python
from widemind.economy.reporting import audit_integrity, public_summary

summary = public_summary(service, run_id)
report = audit_integrity(service, run_id)
```

## Public financial projection

`public_summary` calls the existing atomic `balance` read once and constructs a new dictionary from an explicit allowlist. It never copies a private response wholesale. Its version 1 schema is:

| Field | Meaning |
| --- | --- |
| `version`, `currency`, `unit` | `1`, `USD`, `micro-USD` |
| `mode` | Explicit `test` or `live`; test transactions remain distinguishable |
| `verification_scope` | Always `local_accounting_only` |
| `posted_micro_usd` | Signed sum of the cash journal |
| `reserved_micro_usd` | Unused authorizations still reserved for paid execution |
| `held_micro_usd` | Customer/refund exposure held separately from reservations |
| `available_micro_usd` | Posted minus reserved minus held; negative liabilities remain visible |
| `exhausted` | Available credit is zero or negative |
| `cash_categories` | Fixed signed totals for opening grant, owner funding, test funding, customer receipt, processor fee, operating charge, refund, chargeback, chargeback reversal, payment adjustment and correction |
| `unclassified_cash_micro_usd` | Numeric total of any future category names; names are never published automatically |
| `noncash_metrics` | Separate `earned_revenue_micro_usd` and `infrastructure_subsidy_micro_usd` amounts; these do not replenish cash |

`posted_micro_usd = sum(cash_categories.values()) + unclassified_cash_micro_usd`. Fees are already signed journal entries; do not subtract them again. Every monetary value remains an integer, including metrics and liabilities. Consumers must preserve exact integer JSON values rather than convert large totals to floating point.

The projection contains no run, customer, job, action, provider, account, payment, transaction or event identifiers. It contains no dynamic category names, references, timestamps, URLs, private metadata, excerpts or raw events. Future fields in private balance responses cannot silently enter this projection.

`live` identifies the run's configured mode. It does not certify independent customer revenue, delivery, bank settlement, full cost allocation, or profit. Opening grants, owner support and test funds are separately labeled. Earned revenue is an operator-recorded performance measure. Missing infrastructure invoices are not discovered by this report. Provider sandbox verification and live account requirements remain in [setup](ECONOMY_SETUP.md).

## Local integrity report

`audit_integrity(service, run_id, max_records=100_000)` opens the existing database with SQLite `mode=ro`, sets `query_only`, and holds one read transaction. Its accounting checks therefore use a consistent snapshot during concurrent writes. It does not instantiate a new service, run migrations, acknowledge events or append an audit event.

The result contains `version`, `verification_scope=local_accounting_consistency`, `complete`, `ok`, `database_user_version`, `mode`, aggregate `inspected` counts and `checks`. Each check contains a fixed `code`, `ok` and `violations` count. It omits private record values and identifiers, including on failure. `ok` is true only when the audit completed and every applicable check passed.

Checks cover:

- Exactly one $5 opening grant and its opening event.
- Integer monetary storage, structured accounting records and noncash metric separation.
- Current hold and reservation projections against their recorded transitions, including the original immutable authorization amount, provider/account, action and limits; known provisional charges against subtotal events. Payment release/pending amounts and revisions must be nonnegative integers, and payment holds must reconcile with posted components and recorded releases. Cumulative released funds must match authenticated fulfillment events, pending exposure must match the latest reconciliation summary, and revisions must match reconciliation plus fulfillment transitions. A normal refresh of a corrupted projection does not establish authorization.
- Gross/fee/net identities, payment bindings, component-to-journal amounts and attribution, positive-component settlement observations, missing known liabilities, missing settled components, and reconciliation event coverage.
- Final/partial charge totals against reservations and operating journal entries, with event coverage.
- Linked correction chains, including previously persisted corrections to still-active provisional charges that require operator investigation.
- In service-v1 schema 6, immutable research child links and their allocation events, shared run/account, allowed providers, non-nested topology, original job-cap bounds, child resource limits, zero direct parent cost, and the transferred parent/child hold formula. Closed children still count their original quote against the job cap; unknown children keep their own unused holds after parent settlement.
- SQLite foreign-key consistency across the accounting database. This last check can report a database problem outside the selected run without exposing another run's records.

The record limit bounds materialized records across the selected run's journal, reservations, holds, charges, payments, transactions, components, events, reconciliations and research allocation links touching either endpoint's run. SQLite query traversal and the database-wide foreign-key check are not a wall-clock limit. Limits must be integers from 1 to 1,000,000. Exceeding the limit or encountering an unsupported schema returns `complete=false, ok=false`; it never presents a partial scan as a successful audit. Service accounting schemas 1 through 6 are supported. An unknown run returns the normal 404 error; unreadable storage returns a sanitized 503 error.

New fulfillment events identify provider, account and mode explicitly. Legacy events without those fields are attributed only when exactly one payment in the selected run has that payment ID. Multiple candidates produce a failed `legacy_release_scope` check; history is never rewritten or attributed by guesswork.

This is a consistency verifier for the implemented relationships. It is not a full database corruption scan, a cryptographic tamper proof, an independent provider/bank reconciliation, an audit of privileged administrators, or proof of operating profitability. It does not check the worker's commitments or the newly added reconciliation scheduling tables. Investigate a failed report using authenticated private ledger and provider records; use supported append-only corrections rather than editing historical entries. Legacy corrections to active partial charges require particular care before final reconciliation to prevent duplicate credits.

## Verification

```sh
PYTHONPATH=src python -m pytest -q tests/test_reporting.py
```

Tests use synthetic accounting records. They cover exact public/private total reconciliation, redaction canaries in private identifiers and metadata, unknown future category names, negative balances, noncash metrics, restart recovery, concurrent writers, read-only behavior, supported schema versions, limits, and injected inconsistencies in opening grants, component journals, projections, charge totals, events, immutable authorizations, malformed numeric payment projections and legacy corrections. Privileged corruption fixtures explicitly remove new projection guards to model damage already present in older databases; normal database admission remains protected. These results are local fixture verification, not real sandbox or live payments.
