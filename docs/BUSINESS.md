# Business and fulfillment module

## Implemented versus live

Python 3.11+ and SQLite implement the private business workflow, migrations, persistent event queue, effect journal, extensible service registry, operator API, memory, and redacted public activity. There are no runtime dependencies outside the standard library. The worker runs independently of the website.

The repository began with documentation only. This module establishes **version 1** host interfaces in `widemind/business/contracts.py`. The business package itself does not implement an economy ledger or provider gateway. The host now injects the separate implemented `widemind.economy`, `widemind.tools`, and `widemind.runtime` modules. A full arbitrary-browser/code/graphics toolkit remains future service work. Their absence fails closed; live commands never fall back to fixtures. Coordinate future economy/tools/runtime implementations against these interfaces rather than creating a second budget system.

The shipped research-brief service has a real scope validator, renderer, persistent job lifecycle, and delivery workflow. The reproducible demonstration uses explicitly labelled, offline test data for **all external dependencies**. No real customer, money, email, collection of research, or profitable live run is claimed.

## Reproduce

From the repository root:

```sh
python -m unittest discover -s tests -v
python -m widemind demo --directory /tmp/widemind-business-demo
python -m widemind demo --directory /tmp/widemind-business-demo
```

Both demo invocations return the same order, with `mode: demo`, `live_effects: false`, and a warning. Inspect `DEMO-research-brief.md` in that directory. Repeating the demonstration must not create another order or send operation. Use a fresh private directory for an independent run. Fixture addresses use `example.invalid`; fixtures never make network calls.

Optional packaging:

```sh
python -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/widemind demo --directory /tmp/widemind-packaged-demo
```

Windows uses `.venv\Scripts\python.exe`. Use `python -m widemind` directly if no install is needed.

## Business lifecycle

1. `Business.receive` durably records an inbound request, discovered opportunity, or targeted outbound lead, its source reference, scope, priority, and an identified customer if one exists. It enqueues selection without invoking a model. Discovery can be supplied by a configured browser/host workflow or operator; there is no continuous paid search loop.
2. Selection checks registered services, capabilities, scope, tool availability, pricing, opt-outs, and outbound policy. Missing facts become `needs_review`; missing customers are never fabricated. Assessments preserve expected costs and expected contribution **as estimates**, not actual profit.
3. Prioritization compares configured goal fit, priority, and expected contribution. It allocates a cumulative experiment allowance transactionally before proposing offers. This conservative allowance is not replenished automatically when an attempt fails; it is not spendable cash. The economy independently authorizes actual spending.
4. Quotes freeze scope, price in integer micro-USD, execution plan/cost ceiling, expiry, and turnaround. Offer mail is journaled. Offers promise fulfillment only after verified settlement and spending authorization. Acceptance requires the matching customer and evidence; it does not immediately create a funded deadline.
5. An accepted order requests a payment link **from economy**, communicates it through Gmail, and waits for a payment notification. Notifications contain identifiers only; payment amounts in an incoming message cannot establish settlement.
6. The worker queries an identity-checked, monotonic economy snapshot, requires full unrefunded settlement, and obtains a reservation before confirming a delivery deadline. A reservation denial leaves the job waiting, with no paid tool call or fabricated commitment.
7. Fulfillment sends the commitment, invokes the frozen plan through Tools with a reservation and enforceable cost ceiling, validates scope, persists a private artifact, and delivers through Gmail. Every external effect has a durable intent and stable action ID **before** invocation.
8. Only confirmed delivery permits notifying economy of fulfillment and completing the order. Economy owns earned revenue. Memory preserves results, sources, previous attempts, and lessons. Evaluations calculate contribution only from final economy-reported costs; provisional costs produce no profit conclusion.

The order stages are `accepted`, `awaiting_payment`, `funded`, `ready`, `delivered`, and `completed`. `payment_hold` and explicit `cancelled` recovery preserve financial exposure. An overdue event records an unfinished delivery or missing delivery confirmation; it does not mark cancelled/already-delivered work late or silently extend the deadline. A resumed funding round keeps the original deadline. If creation succeeded before an interruption, its result is rendered and preserved before entering a refund hold; resuming that completed work does not reserve or pay for creation again.

### Research scope

```json
{
  "topic": "SQLite for a small application",
  "sections": ["Overview", "Risks"],
  "min_sources": 2
}
```

`research.collect` must return unique HTTPS sources with titles and excerpts, and exactly the agreed sections with nonempty summaries and citations pointing to those sources. Missing sections, unknown citations, duplicate sources, empty content, and unsafe filenames fail validation. The module renders a private Markdown attachment with source excerpts and citations. Citation consistency is checked locally; factual verification and licensed collection remain requirements of the research adapter and any configured human quality review. The fixture does not claim to perform either live operation.

### Other services

Implement `Service.name`, `capability`, `plan(scope, allowance) -> Plan`, and `deliverable(scope, result) -> artifact`, then inject instances via `Business(..., services=[...])`. A plan selects a registered gateway action, structured arguments, and a maximum cost. The adapter can coordinate multiple internal steps within that enforced limit. A validated artifact supplies `filename`, `media_type`, text/encoded `content`, and scope `checks`. Binary transport needs a documented gateway encoding or authenticated artifact reference extension; it is not supplied in this version.

There is no occupation switch in the engine. Tests register a writing service and complete the same lifecycle. Graphics, software, and publishing can use the same boundaries when configured, with service-specific validators (such as rendering checks or sandboxed software tests). They are not falsely advertised as already implemented.

## Policy

`Policy` is operator-owned and persisted per run. It includes business goals, enabled capabilities, service prices, maximum job cost, cumulative experiment allowance, quote validity, turnaround, open-order capacity, outbound permission, and prioritization weights. Defaults allow research and disable outbound. Prices/costs are exact integer micro-USD. The default demonstration offer costs $2 with a $0.25 execution ceiling, not a profitability claim or permanent product restriction.

The worker reloads policy at every event. A configured capability disabled before execution blocks further work. The gateway must also enforce capability and runtime changes at dispatch and cancel supported running work. Customer mail and web content cannot modify policy or grant credentials.

Unquoted opportunities can be reconsidered after clarification or configuration changes; revisions preserve previous scope and evidence. Quoted terms are immutable. Changed commercial terms require a new opportunity/quote and explicit acceptance.

## Host integration contracts (version 1)

Use a factory returning a fresh `Business` with a thread-local `Store`, the **same** run ID and database for worker/API, and your existing economy, tools, and runtime clients. The database pins each run to its explicit `live`, `demo`, or `test` mode; a later factory cannot reinterpret that run in another mode. Keep enforcement and secrets outside the business process's writable account/workspace. This Python protocol is a logical service boundary; it is not itself an OS security sandbox.

```python
# your_host.py — skeleton: these imported clients must be implemented/configured.
from widemind.business.engine import Business
from widemind.business.store import Store
from your_control_plane import economy_client, tools_client, runtime_client

def business_factory():
    return Business(
        Store("/private/widemind/business.sqlite3"),
        run_id="your-funded-run-id",
        economy=economy_client(), tools=tools_client(), runtime=runtime_client(),
        mode="live",
    )
```

Set `WIDEMIND_HOST_FACTORY=your_host:business_factory` and a separate `WIDEMIND_OPERATOR_TOKEN` containing at least 32 random characters. Provision the token through your secret manager; never put it in Git or public pages. Then run these as separate services:

```sh
python -m widemind worker
python -m widemind api --port 8080
```

The API binds loopback. Deploy behind TLS and operator authentication/rate limits. Use `python -m widemind worker --once` for an external scheduler. The built-in scheduler checks SQLite at intervals up to 30 seconds; idle checks invoke **no** model or external adapter. An OS file lock permits one worker per database. Run API instances with separate connections. SQLite storage must be local (not an untested network filesystem). The small WSGI server is for local integration; use a production WSGI server for a live deployment.

### Runtime

`state(run_id)` returns `active`, `waiting`, `paused`, `stopped`, `exhausted`, or `failed`. Only active/waiting permit new work. Pause/stop do not consume queued business events. Reconciliation events remain available while stopped and only inspect existing effects. Payment-change notifications can also refresh verified financial snapshots while paused/stopped, queuing further work for resume without invoking creation, mail sends, or new reservations. The control plane is responsible for cancellation of supported active actions; the business module cannot force cancellation in another provider. Gateway calls must have enforced time/resource bounds. Do not run the demo runtime as a live control plane.

### Economy

Implement the protocol's idempotent `reserve`, `payment_link`, `release`, `record_fulfillment`, and `reconcile`, plus read-only `payment` snapshots. Every effect receives `run_id`, `job_id`, and a globally stable `action_id`. Successful reserve responses contain `reservation_id`; successful payment-link responses contain a configured processor HTTPS `url`. Payment links cannot credit money.

A payment snapshot has this shape (numbers below are schematic, **not evidence of a transaction**):

```json
{
  "run_id": "your-funded-run-id",
  "job_id": "the-order-id",
  "revision": 1,
  "mode": "live",
  "status": "pending",
  "currency": "USD",
  "receipts_micro_usd": 0,
  "refunds_micro_usd": 0,
  "fees_micro_usd": 0,
  "earned_micro_usd": 0,
  "operating_cost_micro_usd": 0,
  "costs_final": false
}
```

Status is `pending`, `partial`, `settled`, `refunded`, or `disputed`. Revisions increase on changes; the same revision must represent the same snapshot. Replayed older notifications only cause a fresh query. Stale/conflicting snapshots, mismatched identities/currency/mode, fractional money, negative values, and earnings before delivery fail closed. Snapshot sourcing and settlement verification belong to economy. Currency conversion, fees, holds, refunds, chargebacks, accounting corrections, and real charge reconciliation remain economy responsibilities under [ECONOMY.md](ECONOMY.md).

`record_fulfillment` consumes a durable confirmed delivery reference. It must not credit cash; it may release the configured performance/hold conditions and update earned revenue. Receipts and earned revenue remain separate, including after refunds. `release` must be idempotent and safe for an already settled reservation. Cash for unfinished work remains subject to economy's customer/refund holds; a settled receipt alone is not permission to spend it.

### Tools and Gmail

`available(action)` checks configured capability readiness without paid execution. `execute` receives run/job/action IDs, action name, structured arguments, reservation ID, and integer `maximum_micro_usd`. The gateway must:

- Enforce capability, runtime state, dedicated account identity, quotas, and provider/runtime/token limits corresponding to that maximum.
- Require and validate a reservation for every billable action; charge actual usage including failures; settle/reconcile the reservation with economy. Gmail calls here have a zero incremental-cost ceiling. If your provider charges for them, add explicit cost planning/reservations rather than bypassing enforcement.
- Persist action identity and results independently; never issue an external action twice for the same action ID.
- Return `Outcome("succeeded", data)`, `Outcome("failed", reason=...)`, or `Outcome("unknown", reason=...)`. A confirmed failure is terminal for that attempt, including its charges. `reconcile(action_id)` must inspect durable/provider evidence without repeating the original action.
- Enforce network/workspace isolation, safe content handling, credential secrecy, source rights, and safe handling of untrusted instructions in emails/webpages. Do not let research or code tools reach accounting enforcement files or unrelated accounts.

Required research actions: `research.collect`, `gmail.read`, `gmail.send`. The dedicated Gmail HTTP/OAuth implementation lives in `widemind.tools`, separately from business.

`gmail.send` arguments contain `to`, `subject`, `body`, and optionally a private `attachment`. Results contain `message_id`, `thread_id`, and `delivery_state`. **Gmail API acceptance or a Sent-folder entry alone is not confirmation of final deliverable receipt.** Offers, payment requests, and commitment notices can progress after transport acceptance; final attachment delivery requires verified customer acknowledgment. Attachment messages include a structured acknowledgment command; normal authenticated replies can also prove receipt. The gateway emits independently verified delivery updates that wake the existing business job. Return `accepted`/unknown until reliable delivery evidence or an authenticated customer acknowledgment exists. The adapter's `reconcile` supplies that evidence and `delivery_state: confirmed`, or a confirmed failure such as a bounce. The business module does not infer customer receipt/read status. In production, this means uncertain messages can remain blocked pending reconciliation or operator follow-up; they are never blindly resent.

`gmail.read` takes a durable cursor and returns at most 100 messages plus optional `next_cursor`. A normalized message includes:

```json
{
  "message_id": "provider-id",
  "thread_id": "provider-thread",
  "from": "actual-customer-address",
  "sender_verified": true,
  "command": {"type": "request", "service": "research-brief", "scope": {"topic": "A topic", "sections": ["Overview"], "min_sources": 2}}
}
```

Other commands are `{"type":"accept","quote_id":"..."}` and `{"type":"opt_out"}`. The gateway must verify sender identity appropriately (not trust a forged From header) before setting `sender_verified`. Offer acceptance must match the stored quote customer. Arbitrary free-form mail is recorded for clarification rather than guessed into binding scope or acceptance. A future parser/model can propose structured scope through the same intake boundary; it cannot autonomously rewrite permissions. Gmail pushes enqueue `gmail.sync`; a cursor continuation is finite, bounded work. If push is unavailable, schedule an explicit low-frequency sync externally without paid reasoning.

Targeted outbound/discovered opportunities require a real HTTPS relevance reference, identified recipient, enabled outbound policy, and no recorded opt-out. Message/provider IDs and logical effects deduplicate sends and replies. Opt-outs block new messages, including automated delivery, leaving an explicit obligation for operator handling. No real outreach was sent during development.

## API

`/`, `/console`, authored static assets, `GET /api/v1/public`, and `GET /api/v1/activity` are public shells/projections; private data requires bearer authentication. The Stripe webhook authenticates via its verified signature instead of an operator token. The activity endpoint returns mode plus selected event kind, registered service label, and UTC timestamp. It contains no job IDs, customer identities, inbox contents, artifacts, or financial detail.

Every other route requires `Authorization: Bearer <operator-token>`. These are operator/adapter routes, **not customer authentication**. Do not hand that token to customers. Private delivery in this version is through the customer-specific Gmail adapter; a customer download portal must add its own ownership enforcement before exposing artifacts.

| Route | Request/result |
| --- | --- |
| `GET /api/v1/business` | Private run/event/opportunity/order diagnostics |
| `GET /api/v1/orders/{id}` | Private order, frozen scope/plan, payment snapshot, artifact, delivery obligation and unrefunded receipts |
| `GET /api/v1/memory` | Bounded private history; hosts can also call `recall(customer_id)` |
| `POST /api/v1/opportunities` | `receive` parameters: source, external_id, service, scope, optional customer_email/origin/source_reference/priority |
| `POST /api/v1/accept` | quote_id, customer_email, evidence_reference |
| `POST /api/v1/events` | source, external_id, kind, payload, optional due_at |
| `POST /api/v1/policy` | A validated operator Policy document |
| `POST /api/v1/reconsider` | opportunity_id, evidence_reference, optional scope/customer_email; only before quoting |
| `POST /api/v1/opt-out` | email, reference |
| `POST /api/v1/effects/{id}/retry` | evidence_reference; confirmed failure only, at most two retries |
| `POST /api/v1/resolve-hold` | order_id, decision (`resume`/`cancel`), evidence_reference |

Events use `source` plus `external_id` scoped to a run as deduplication identity. Scope source names to the adapter/provider **and account**. Reusing an identity with changed content is rejected. `payment.changed`/`advance` payloads contain the actual order `id`; `offer` contains opportunity `id`; `reconcile` contains effect/action `id`. A `gmail.sync` payload can contain `cursor`. `select` and `deadline` are also supported. There is no public endpoint that directly invokes arbitrary tools or posts money.

## Recovery and migrations

Migrations are ordered SQL files owned by this module. Migration 005 marks pre-audit runs `unclassified` because their original configuration did not persist a mode. They fail closed until the operator reviews their effects/history and classifies them explicitly. Known payment snapshot modes must match the chosen mode; a classified run cannot be promoted or switched.

For an **existing, reviewed fixture** database only:

```sh
python -m widemind adopt-run --database /private/demo/business.sqlite3 --run-id demo-run --mode demo --evidence-reference reviewed-offline-demo-history
```

Use a new run/database for live operations rather than converting demo data. For a legacy live run, review all external effects and provider evidence before using `adopt-run --mode live`; the command does not authenticate old transactions or make a fixture real. New runs are pinned automatically and need no adoption step.

 Startup applies each atomically with a migration version row. Preserve the private database, WAL, and adapter journals together with consistent backups. Use SQLite's online backup mechanism or quiesce all writers; do not copy only the main file of a running WAL database. Store and back up customer data according to your retention policy. Database files are created owner-only; deployment directories, backups, WAL/shm files, fixture output, logs, and reverse proxies must also remain private.

The event queue claims a lease before work. An interrupted event is reclaimable after 120 seconds; an existing intent is reconciled rather than reinvoked. Normal blocked work does not retry on a timer. Diagnostics preserve the blocking cause. Fix the dependency, reconcile the relevant action, and enqueue a **new uniquely identified** advance/offer event. A reconciliation event updates evidence only; it does not itself start more fulfillment while stopped. Execution and reconciliation use the same result/schema limits. Confirmed terminal success is retained; only unresolved outcomes or accepted-but-unconfirmed mail may evolve through ordinary reconciliation.

A confirmed failed nonbillable action or economy authorization request may receive an explicit retry authorization. The new attempt has a distinct action ID, preserves the prior audit record, and is capped at three total attempts. **Paid tool failures cannot use this generic retry:** their reservation may already be settled and their charge remains real. They require a newly approved plan with fresh economy authorization; automated paid rework is not implemented in this slice. Enqueue the appropriate advance/offer event after authorizing it. Unknown effects cannot be retried. This avoids spending repeatedly on an unproductive or uncertain action.

Payment/refund/dispute changes before fulfillment place a funded order on hold after resolving any unknown effects and releasing the appropriate reservation. Confirmed delivery is recorded even if a refund arrived before its acknowledgment. Resuming a hold requires verified settlement and explicit operator evidence; unfinished creation receives a fresh reservation. Cancelling preserves unrefunded receipts and the refund exposure for economy/operator resolution. A cancellation is **not** a refund and does not erase historical commitments or costs. Payment notifications continue to update cancelled/completed orders.

## Live-operation prerequisites and limits

Before a controlled live pilot:

- Implement/configure the production host clients above, including authenticated payment verification, external budget enforcement, provider-enforced limits, actual charges, holds/refunds, and durable reconciliation. The bundled real-provider host fails closed when required configuration is absent; see OPERATIONS.md.
- Provision WideMind's dedicated Gmail identity/OAuth and provider permissions. Establish sender verification and an actual delivery-confirmation mechanism. Use controlled recipients first.
- Configure a bounded research tool and quality/rights review; the sample citations alone do not prove facts, freshness, or a sellable report. Register other service adapters/validators before enabling their capabilities.
- Configure runtime run funding/state/cancellation and an operator autonomy profile explicitly authorizing live effects. Isolate credentials/accounting from the agent workspace.
- Deploy the worker and authenticated API separately with private local storage, backups, TLS, appropriate access control, resource bounds, notification/webhook delivery, and operator monitoring of blocked work and missed deadlines.
- Define payment/refund terms and the full operating-cost allocation. Development costs, hosting and any subsidies are not magically included in the fixture's measured cost. Do not publish fixture financial totals as live revenue.

Local automated verification covers the full request-to-delivery flow, writing-service extensibility, insufficient funds and bounded retries, unavailable/disabled tools, pause/stop, interrupted effects, duplicate events, uncertain delivery, refunds/disputes before and after delivery, stale payment revisions, deadlines, quote identity/expiry, outbound opt-outs, API authorization, private/public separation, migration concurrency, and an idempotent demonstration. Provider live qualification and an independent paying customer still require real external evidence. The implemented public website and operator console are documented in UI.md; all run wallet totals come from economy and become unavailable if that authoritative read fails.
