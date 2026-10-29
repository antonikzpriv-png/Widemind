# Economy service setup

## What exists

The fetched foundation was documentation only. The economy module selects Python 3.12+, FastAPI, HTTPX and SQLite. It supplies the ledger, reservation/charge/hold APIs, Stripe-hosted Checkout URLs, verified webhooks and transaction reconciliation. It does not add the autonomous worker or a website. The [versioned interface contract](ECONOMY_CONTRACT.md) is the integration boundary for those components.

Provider-fixture tests use synthetic Stripe responses; passing them is **not evidence of a live or sandbox payment**. No Stripe account, business verification, signing secret, domain, hosting or real processor receipt is bundled.

## Install and run

From the repository root:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.lock -e .
python -m pytest -q
```

Generate three independent bearer secrets. Store them in an operator-owned secret manager or environment file, outside the agent workspace. Each token must contain at least 32 ASCII characters without whitespace; the service rejects identical role tokens.

```sh
python -c 'import secrets; print(secrets.token_urlsafe(48))'
```

Set all four required variables, using different generated token values:

```sh
export WIDEMIND_OPERATOR_TOKEN='<operator token>'
export WIDEMIND_GATEWAY_TOKEN='<trusted gateway token>'
export WIDEMIND_AGENT_TOKEN='<agent token>'
export WIDEMIND_AGENT_RUN_ID='sandbox-run-1'
export WIDEMIND_ECONOMY_DB='/operator-owned/widemind/economy.sqlite3'
widemind-economy --host 127.0.0.1 --port 8080
```

The equivalent command is `python -m widemind.economy.cli`. Environment files are not loaded automatically: configure environment injection in the process manager. `.env.example` documents names and contains no usable credentials. Migration runs automatically before the application accepts requests. Persist the database and its SQLite WAL on a local filesystem, not a network-mounted shared database. Back up with SQLite's backup API or `sqlite3 .backup`, not a lone copy of the main file while the service is writing. On recovery, keep outstanding reservations and reconcile them rather than recreating the run or releasing them.

`GET /health` reveals only service status and contract version. Other APIs require `Authorization: Bearer …`; webhooks instead require the Stripe raw-body signature. A production deployment must use HTTPS via a configured reverse proxy, suppress authorization/body logging, rate-limit the service, and restrict database/code/credentials to the trusted service OS identity. Bind loopback unless an authenticated private network/reverse proxy is configured. The agent must not be able to edit the code, environment, database, migrations or provider credentials. Database triggers protect append-only history against accidental update/delete; OS access separation is the boundary against a process that could replace the database or drop triggers.

## Basic accounting through the application

These examples use synthetic local accounting, not customer receipts:

```sh
curl --fail-with-body http://127.0.0.1:8080/v1/runs \
  -H "Authorization: Bearer $WIDEMIND_OPERATOR_TOKEN" -H 'Content-Type: application/json' \
  -d '{"run_id":"sandbox-run-1","mode":"test"}'
curl --fail-with-body http://127.0.0.1:8080/v1/runs/sandbox-run-1/balance \
  -H "Authorization: Bearer $WIDEMIND_AGENT_TOKEN"
```

Run creation seeds exactly `5_000_000` micro-USD. Repeating the same request does not seed again. A new run is an operator decision; the agent cannot create one. The agent token is scoped to `WIDEMIND_AGENT_RUN_ID` and can read that run and request reservations, but cannot post funding, corrections, settlement, hold release or receipts.

| Endpoint | Identity | Purpose |
| --- | --- | --- |
| `POST /v1/runs` | Operator | Create or idempotently reopen an existing run |
| `GET /v1/runs/{run_id}/balance` | Scoped agent, gateway, operator | Exact integer balance and funding categories |
| `GET /v1/runs/{run_id}/ledger` | Scoped agent, gateway, operator | Append-only private journal |
| `GET /v1/runs/{run_id}/events?after=0` | Scoped agent, gateway, operator | Durable versioned event cursor |
| `GET /v1/runs/{run_id}/reservations` | Scoped agent, gateway, operator | Spending status and unknown outcomes |
| `GET /v1/runs/{run_id}/payments` | Scoped agent, gateway, operator | Receipt/payment status |
| `GET /v1/runs/{run_id}/reconciliations` | Scoped agent, gateway, operator | Durable reconciliation records |
| `GET /v1/runs/{run_id}/charges` | Scoped agent, gateway, operator | Verified final charges and partial known liabilities |
| `GET /v1/runs/{run_id}/checkouts` | Scoped agent, gateway, operator | Durable checkout requests, including unknown creation outcomes |
| `POST /v1/runs/{run_id}/reservations` | Scoped agent, gateway, operator | Atomically reserve credit |
| `POST /v1/reservations/{id}/start` | Gateway, operator | Record dispatch; duplicate dispatch fails |
| `POST /v1/reservations/{id}/settle` | Gateway, operator | Post actual usage/charge, including charged failures |
| `POST /v1/reservations/{id}/cancel` | Gateway, operator | Release an undispatched reservation only |
| `POST /v1/runs/{run_id}/holds` | Operator | Audited customer/refund exposure |
| `POST /v1/runs/{run_id}/funding` | Operator | Explicit owner or test funding |
| `POST /v1/runs/{run_id}/subsidies` | Operator | Noncash infrastructure support |
| `POST /v1/runs/{run_id}/earned-revenue` | Operator | Fulfillment performance, no cash credit |
| `POST /v1/runs/{run_id}/corrections` | Operator | Compensating journal entry, never rewrite history |
| `POST /v1/runs/{run_id}/checkout` | Operator | Durable hosted Stripe Checkout request |
| `POST /v1/payments/{id}/reconcile` | Operator | Fresh processor transaction verification |
| `POST /v1/payments/{id}/release-hold` | Operator | Explicit customer fulfillment release |
| `POST /v1/provider/verify` | Operator | Verify configured account against Stripe |
| `POST /v1/webhooks/stripe` | Verified Stripe signature | Fetch and reconcile authoritative payment state |

All monetary request fields reject floats, booleans and numeric strings. Unknown fields are rejected. HTTP mutations have a 1 MiB body limit. There is no API for arbitrary customer-receipt posting or model-asserted settlement.

Before paid execution the trusted gateway must reserve a bounded cost, store and enforce adapter/provider limits, mark dispatch, execute once, and submit actual provider usage. Do not hand the agent raw billing credentials. A reservation is not itself a provider token/runtime limit. There is no billable autonomous runtime in this module; every future billable adapter must integrate this boundary before enabling live work. The `PaidGateway` helper in `widemind.economy.gateway` integrates `EconomyService` with trusted `BoundedAdapter` instances: the adapter quotes a deterministic upper bound, the gateway verifies sufficient reservation, durably dispatches once, then settles its authoritative `UsageReceipt`. Register adapters from operator-owned application code, never from model input. `PaidGateway.execute(reservation_id)` performs the start and settlement itself; do not call `/start` first when using this helper. A remote trusted gateway can instead use the corresponding HTTP steps and must enforce the same provider limits.

An unknown outcome uses `actual_micro_usd: null` and retains the reservation until authenticated usage reconciliation. Do not retry uncertain external work blindly. A failed call with a known charge must settle that actual charge, not cancel. Unexpected overages remain liabilities even below zero and block new spending.

## Real Stripe sandbox verification

1. Create or select a dedicated Stripe account and sandbox. Obtain its `acct_…` ID, a test API key, and a webhook signing secret. Use a separate database and test-mode run. Do not mix sandbox transactions with a live experiment.
2. Set **all** Stripe variables from `.env.example`: account ID, `WIDEMIND_STRIPE_MODE=test`, `sk_test_…` (or an appropriately permissioned `rk_test_…`) key, `whsec_…` secret, and success/cancel URLs. The hosted Checkout success page is a return URL, not proof of payment. HTTPS is required except for loopback redirects in test mode.
3. Make `POST /v1/webhooks/stripe` reachable by Stripe through HTTPS, or use the official Stripe CLI's sandbox forwarding to `http://127.0.0.1:8080/v1/webhooks/stripe`. Use the signing secret generated for that exact endpoint/CLI session; dashboard and CLI endpoint secrets differ. Keep the proxy's raw request body and `Stripe-Signature` header intact. Do not log card data or full webhook bodies.
Subscribe the endpoint to the supported `checkout.session.*`, `payment_intent.*`, `charge.*` (including refund/dispute events) and `refund.*` events. `balance.available` does not identify a checkout; periodically reconcile known pending payments through the operator endpoint when their availability changes. The adapter pins Stripe API version `2024-06-20`. A restricted key needs account retrieval, Checkout session create/read/list, payment-intent/charge/balance-transaction/refund/dispute read/list permissions. Reconciliation reads the complete paginated charge history, not only the latest attempt. Only USD settlement is supported; other currencies fail closed without invented FX rates. Refund creation is performed in Stripe Dashboard; this adapter reconciles its accounting and does not expose a refund-creation endpoint.

4. Restart with this configuration. Call `POST /v1/provider/verify` with the operator token to verify the expected account; account mismatch is an error. Do not infer working credentials from environment variable presence.
5. Create a checkout for a test run and a durable job identifier:

```sh
curl --fail-with-body http://127.0.0.1:8080/v1/runs/sandbox-run-1/checkout \
  -H "Authorization: Bearer $WIDEMIND_OPERATOR_TOKEN" -H 'Content-Type: application/json' \
  -d '{"request_id":"sandbox-order-1","job_id":"sandbox-job-1","amount_micro_usd":1000000,"description":"Sandbox verification order"}'
```

6. Open the returned Stripe-hosted Checkout URL and complete a test card payment using Stripe's documented sandbox card, such as `4242 4242 4242 4242`, with a future expiry and a test CVC. Never enter real card details into a fixture/test flow. The amount must be whole USD cents (`10_000` micro-USD), because this adapter does not invent a fractional-cent conversion or FX rate.
7. Observe the signed event and call `POST /v1/payments/{cs_…}/reconcile`. Run `widemind-economy worker --run-id sandbox-run-1` alongside the HTTP service to process queued events; signed webhook admission now returns HTTP 202 before provider reads. The adapter fetches real provider objects; checkout completion and an invoice alone do not increase spendable credit. Only an available/settled USD balance transaction credits a receipt. Stripe may leave a transaction pending until its availability date, so preserve that state and repeat reconciliation later. A scheduler can poll inexpensive reconciliation without invoking a model.
8. Confirm the gross receipt, processor fee and net reconcile once. New receipt net is held for unfinished work. After validating fulfillment, POST `{"idempotency_key":"fulfillment-1"}` to `/v1/payments/{cs_…}/release-hold`; this operator decision releases customer funds, without creating another receipt or automatically marking earned revenue. Record earned revenue independently against the job when appropriate.
9. Create a sandbox refund or dispute through Stripe's dashboard/test tools, reconcile, and verify the liability and fee behavior. Re-send signed test events and reconcile again; the ledger must not duplicate the transaction. Inspect the ledger and `/events` cursor before and after restarting the service to confirm recovery. A refund event can precede receipt settlement; the debit remains visible. Retain redacted provider object IDs, mode, amounts, availability state and timestamps as verification evidence—never keys, cards, customer details or raw webhooks.

For webhook delivery errors, preserve Stripe retry/reconciliation paths; a failed unknown payment mapping must not create credit from metadata. Checkout requests are durable and use a provider idempotency key; retry with the same request ID after a timeout within the 23-hour safety window. An unresolved request older than that requires operator inspection and recovery against Stripe rather than another blind POST, because provider idempotency keys may expire. Contact the operator to inspect the persisted request and processor session; do not create a new request ID to bypass this guard. Do not change amount/job under that ID or repeat with a fresh ID to hide an uncertain external effect. Only session IDs created and durably bound by this service are eligible for reconciliation.

## Financial policy and remaining setup

- The opening $5 is an explicit run grant, not earned revenue. Owner top-ups, sandbox/test funds and infrastructure subsidies stay separately labeled. Noncash infrastructure support and earned-revenue metrics do not increase cash.
- Customer net receipts stay held until an operator records fulfillment. This first implementation therefore requires enough opening/earned operating credit to fulfill work without spending unfinished customer funds. Do not accept jobs whose delivery cost cannot be funded under that policy.
- Refunds, chargebacks and fees debit actual liabilities; a negative balance is preserved and blocks further spending. Corrections append a linked compensating entry. Never modify or delete original accounting rows.
- Hosting, compute, storage, model/tool invoices, subscriptions and shared prepaid services must be allocated and posted by trusted adapters/operator reconciliation. This module does not discover missing provider invoices. Document the allocation/reference and record noncash subsidies rather than presenting unmeasured infrastructure as free profitable operation.
- Run IDs are durable: exhaustion does not erase a run. Reconciliation and explicit owner recovery remain available. Any owner top-up or new experiment must be visible, rather than silently resetting the balance.
- Live payment acceptance still needs the owner's verified business/account, enabled live payment method/capabilities, correct country/tax/refund policies, live key and endpoint signing secret, deployed HTTPS ingress, reachable `api.stripe.com`, and valid return URLs. Use separate live service/database/run/credentials. The cloud development network may need explicit Stripe API access; fixture tests do not require it. Running on another machine does not require the development sandbox's network policy.
- No website, model runtime, mailbox, customer delivery authorization, public projection or business tax/revenue recognition compliance is provided by the economy module. These need their assigned modules. Keep private ledger data behind authentication; a future public feed must explicitly allowlist non-private totals/events.

## Service isolation example

[deploy/widemind-economy.service](../deploy/widemind-economy.service) is an example systemd unit, not a deployed service. Build/install the wheel in a root-owned `/opt/widemind-economy/.venv`, keep `/etc/widemind/economy.env` root-owned mode `0600`, and install the units as administrator. First create a dedicated system account/group named `widemind-economy` with no interactive login; never use the agent account. Separate dynamic identities for the HTTP and worker processes would prevent them from sharing a `0600` database, so these templates deliberately share one static service identity. The HTTP and [worker unit](../deploy/widemind-economy-worker.service) use the same dedicated `widemind-economy` user/group and `0700` state directory. This shared trusted identity keeps the accounting database separate from an autonomous agent running under a different unprivileged OS identity. The service has no capabilities and a read-only system view. Configure HTTPS ingress separately. Do not run the future agent with root privileges, the economy service identity, operator/gateway tokens, or write access to `/opt/widemind-economy` or `/etc/widemind`.

Development tests verify API authorization and SQL append-only enforcement; they do not claim that this example unit has been deployed, or that a future agent sandbox has already been built. Establish that filesystem/process boundary before live agent execution.

`requirements.lock` records the exact Python dependency versions used for local validation and CI, including test dependencies. The package manifest retains compatible dependency ranges for embedding the module in a future application. CI runs provider fixtures without account secrets or external payment calls.

## Upgrade and operational recovery

The service applies additive migrations through schema version 5 in one locked transaction. Stop old writers and make a SQLite backup before upgrading an existing version-1 through version-4 database. Financial history and existing run/reservation IDs remain intact; no opening grant is re-seeded. A migration failure rolls back, and a newer unsupported schema fails closed rather than being downgraded. Old application versions cannot run against the upgraded database; restore a verified backup if an operator needs to roll back the application. Do not change the schema version manually.

Start both the HTTP and worker services with the same configured Stripe account/mode and database. Create the run before starting its periodic worker. Monitor pending/dead-letter tasks; HTTP 202 is not evidence of receipt settlement. [Economy operations](ECONOMY_OPERATIONS.md) documents the independent worker, durable retries, checkout recovery and local integrity endpoints. [Financial reporting](ECONOMY_REPORTING.md) describes the redacted projection and limits of consistency checks. [Checkout recovery](ECONOMY_CHECKOUT_RECOVERY.md) replaces the previous manual-only recovery gap after the safe idempotency window.

If the earlier dynamic-user deployment example was installed, stop it and back up its database before moving to the shared static service identity. Restore into the new service-owned state directory with restrictive ownership; do not expose the accounting files or control-plane environment to the agent while migrating. These unit files are examples, not a claim of deployment verification in this development environment.

## Integrated runtime

The TypeScript foundation and Python economy are now connected through the registered economy module. Follow [runtime economy setup](RUNTIME_ECONOMY.md) and [the exact RPC contract](ECONOMY_RUNTIME_BRIDGE.md). The bridge does not automatically seed money during summary/authorization, enable unconfigured tools, or create a second ledger. A protected operator route explicitly opens an existing runtime run with exactly $5 once. Keep the authorization-signing secret stable across restarts. JavaScript consumers must use the string-valued runtime RPC: native Python control endpoints retain JSON integer money, whose aggregates can exceed JavaScript's safe Number range.

Package 0.3.0/schema 5 permits genuine zero-cost reservations and persists bound runtime permits, provider observations and no-effect recovery. Its projection-table migration copies existing rows under the migration lock, checks foreign-key integrity before commit and preserves the append-only ledger. A confirmed no-effect attempt can be reauthorized at most three times; unknown outcomes and provisional charges do not qualify for a free reset. Retiring tool profiles blocks new execution while the stable signing key keeps old receipts reconcilable, including with an empty profile map.
