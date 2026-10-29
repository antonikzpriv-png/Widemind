# Durable economy operations

Economy package version 0.2.0 changes signed webhook responses to durable HTTP 202 admission and upgrades the SQLite schema to version 4; back up and upgrade both trusted processes together. The HTTP contract remains version 1, and explicit operator reconciliation retains HTTP 200.

This extends the [economy setup](ECONOMY_SETUP.md) and [integration contract](ECONOMY_CONTRACT.md). It adds a payment-reconciliation worker, not the autonomous agent runtime or website. The Python economy entry points are implemented here; an unmerged future TypeScript foundation requires a coordinator-owned bridge to these versioned HTTP contracts before claiming integration with its module loader. All local provider-fixture checks remain synthetic. No sandbox/live Stripe transaction is claimed without separately recorded provider verification.

## Run the HTTP service and reconciliation independently

Install the pinned dependencies, configure the operator-owned environment and use the same persistent local SQLite database for both processes:

```sh
python -m pip install -r requirements.lock -e .
widemind-economy --host 127.0.0.1 --port 8080
```

In a second supervised process with the same database and Stripe account/mode:

```sh
widemind-economy worker --run-id sandbox-run-1
```

The equivalent module entry point is `python -m widemind.economy.cli`. The worker requires the documented Stripe account/key/signing-secret/redirect settings and the normal configuration environment. It must run as a trusted service identity, outside the agent workspace. Do not supply its secrets to the agent. No browser or website is needed for the worker to run. A process supervisor must restart both services independently; starting the HTTP process does not silently create a second worker.

For cron, diagnostics or a bounded supervised execution:

```sh
widemind-economy worker --once --run-id sandbox-run-1 --batch-size 10
```

`--once` schedules due payment checks and processes at most one bounded batch; its JSON output counts processed/completed/retried/dead-letter attempts. It does not imply that no deferred retry remains. `--batch-size` is 1–100, `--poll-seconds` is 1–3600 (default 5), and `--scan-interval` is 60–86400 (default 300). Without `--run-id`, the configured `WIDEMIND_AGENT_RUN_ID` is used. Mode mismatch fails closed. A long-running worker sleeps between batches; it does not invoke a model. Provider reconciliation uses authenticated Stripe reads, never creates a purchase/payment and never performs a paid tool/model action.

## Webhook acknowledgement and accounting completion are different

`POST /v1/webhooks/stripe` verifies the raw-body HMAC, timestamp and configured account/mode before accepting anything. A valid event is durably queued before provider I/O and returns **HTTP 202** with its task ID. The queue stores only the event ID/type/object ID, configured scope and digest, rather than the raw customer webhook body. Repeating the same event returns the original task ID with `duplicate: true` without contacting Stripe; changing the verified payload under the same event ID conflicts.

HTTP 202 means the signed event is safely queued. It does **not** mean a receipt is posted or funds are spendable. The separate worker fetches authoritative provider state and applies existing ledger settlement rules. A queued event may remain pending while Stripe is unavailable or while a checkout's local binding is unresolved. It must not create credit from customer screenshots, event metadata or agent assertions. A receipt that remains processor-pending is not spendable; the worker periodically revisits known payments as availability changes. Reconciliation of settled payments also allows later refunds/disputes and pending positive fee/reversal components to be observed.

The original `POST /v1/payments/{cs_id}/reconcile` remains a synchronous operator endpoint with HTTP 200 semantics. Prefer the durable alternative when an operation should survive a provider timeout or HTTP-process restart:

```sh
curl --fail-with-body -X POST http://127.0.0.1:8080/v1/payments/cs_example/schedule \
  -H "Authorization: Bearer $WIDEMIND_OPERATOR_TOKEN"
```

The scheduled endpoint returns HTTP 202 and a task ID. Both endpoints require the operator identity; the agent cannot approve or dispatch reconciliation, post receipts or release customer/refund holds.

## Inspect retries and recover after interruption

```sh
curl --fail-with-body 'http://127.0.0.1:8080/v1/reconciliation/tasks?state=dead_letter&limit=100' \
  -H "Authorization: Bearer $WIDEMIND_OPERATOR_TOKEN"
```

Task reads expose bounded operational metadata and fixed error codes, not raw provider exceptions or customer payloads. Operators can filter `pending`, `running`, `completed` or `dead_letter`; API page size is 1–100. All task operations are limited to the configured provider account and mode. Agent and gateway identities cannot inspect or reset this operator queue.

The queue coordinates workers with durable leases. A killed process leaves a running lease; another worker reclaims it after expiry. Ledger transaction IDs and webhook IDs remain idempotent, so a crash after accounting commits but before queue completion does not duplicate the money. A lease can be reclaimed even after the original five-minute signature window, because the signature was verified before durable admission; the worker dispatches the saved minimal verified descriptor, rather than accepting a newly unsigned HTTP claim.

Failures use bounded exponential backoff and a finite attempt budget, then enter `dead_letter`. Automatic polling must not bypass a dead-letter payment's attempt budget. Preserve it for operator investigation. Fix the specific account/network/binding/provider-record problem before requesting an audited reset:

```sh
curl --fail-with-body -X POST http://127.0.0.1:8080/v1/reconciliation/tasks/TASK_ID/retry \
  -H "Authorization: Bearer $WIDEMIND_OPERATOR_TOKEN" -H 'Content-Type: application/json' \
  -d '{"reason":"operator verified account and repaired mapping","idempotency_key":"retry-investigation-1"}'
```

Only dead-letter tasks accept this operation. The idempotency key and reason are retained; a replay does not reset an active task again. Do not log the bearer token or substitute a customer claim for verification. Back up the entire accounting database with SQLite's backup API; the queue and append-only attempt history share that database and its migration lifecycle. Do not clear leases, drop queue tables or recreate runs to recover from restart.

SIGTERM/SIGINT stop the worker after the active bounded batch. A forced kill can interrupt provider reads; durable leases and accounting idempotency handle recovery. The worker does not release unresolved spending reservations. Paid-call usage reconciliation remains a trusted gateway/operator responsibility, distinct from customer payment reconciliation.

## Recover an uncertain Stripe Checkout creation

A lost checkout-create response is persisted before the original provider request. Within the safe retry window, using the same request ID preserves the provider idempotency key. An older unknown request must be inspected instead of blindly creating another session:

```sh
widemind-economy recover-checkout REQUEST_ID
```

The operator HTTP equivalent is:

```sh
curl --fail-with-body -X POST http://127.0.0.1:8080/v1/checkouts/REQUEST_ID/recover \
  -H "Authorization: Bearer $WIDEMIND_OPERATOR_TOKEN"
```

This recovery scans existing provider sessions, validates the durable request/account/mode/amount/reference and binds only an unambiguous match. It does not POST a new session or grant credit. No match, ambiguous matches, inconsistent fields or incomplete pagination fail closed for operator review. A recovered completed/expired session may have no usable checkout URL; its durable binding still allows verified transaction reconciliation. Do not create a new request ID to disguise an unresolved original effect.

HTTP identifiers exclude `/`, because a value containing a slash cannot safely address the normal `{id}` API routes. Invalid IDs are rejected before a provider request. Existing unusual IDs introduced through trusted in-process code require the corresponding trusted code/CLI path, rather than rewriting financial history.

## Financial reporting and integrity

The following operator-only reads do not fetch a provider, mutate records or grant funds:

- `GET /v1/runs/{run_id}/financial-summary` returns the reporting module's explicit allowlist projection for eventual publication. It does not automatically publish a webpage or expose a public unauthenticated endpoint.
- `GET /v1/runs/{run_id}/integrity` performs local ledger/projection consistency checks. Passing local integrity is not proof that every external invoice, fee, tax obligation or provider transaction has been imported.

Use these alongside reconciliation task status, the private ledger and preserved provider references. Receipts, earned revenue, owner/test funding, noncash infrastructure subsidies and liabilities remain distinguishable. Only the existing verified settlement rules replenish operating credit. The worker cannot turn an unpaid invoice, a queued event or an agent's asserted outcome into money.

## Remaining account and deployment requirements

Provide a verified dedicated Stripe account, matching test/live credentials and endpoint secret, HTTPS webhook ingress, provider network access and valid return URLs. Configure rate limiting/request timeouts at ingress, supervisor restart policy, backup/restore monitoring and dead-letter alerts. Keep code, migrations, provider keys and the database inaccessible to the agent OS identity. Sandbox and live must use separate runs/databases/keys. The fixture suite proves local behavior only; it does not provision these accounts or establish real provider acceptance.
