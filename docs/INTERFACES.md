# Integration contract v1

Authoritative TypeScript definitions: [`src/contracts/index.ts`](../src/contracts/index.ts). These interfaces establish the foundation for economy, tools, business and website implementations. The implementations under `src/modules` bridge to the separately installed services; see [INTEGRATED_SETUP.md](INTEGRATED_SETUP.md) and the per-service integration guides.

## Ownership and registration

- Foundation owns root configuration/commands, `src/contracts`, `src/db/migrations/core.ts`, `src/auth`, `src/runtime`, `src/bootstrap.ts`, and the minimal HTTP/screen shell.
- Each subsequent module owns `src/modules/<id>/` and its tests. Export a `definition: ModuleDefinition` from `module.ts`. It declares its ID, contract version, append-only migrations and `create({db,config})` factory.
- The loader discovers module directories automatically, in deterministic order; the compiled build discovers `module.js`. No root registry or migration list changes are required. Migration SQL runs **before** service construction. Exactly one provider per role is permitted; collisions fail startup instead of silently selecting one.
- `demo` is a special opt-in development module; it is never loaded in the default foundation profile. Disable it before installing other providers.
- Optional `decorate(services, context)` runs after all factories register. Trusted composition may wrap an existing provider (business adds its journaled `business.step` capability to tools); it must retain existing authorization/reconciliation behavior and must not perform external effects at construction.
- Factories implement any of `economy`, `tools`, `business`, `website`; a single module can implement multiple roles. Missing roles get explicit unavailable implementations. Do not add fake success fallbacks.

Example shape:

```ts
import type { ModuleDefinition } from '../registry.ts';
export const definition: ModuleDefinition = {
  id: 'example', contractVersion: 1,
  migrations: [{ version: 1, sql: 'CREATE TABLE example_records(id TEXT PRIMARY KEY);' }],
  create({ db, config }) { return { /* typed role implementation */ }; },
};
```

Keep module IDs simple lowercase ASCII identifiers. Prefix all owned tables/indexes with the module ID. Versions are positive, increasing integers within a namespace. Applied SQL checksums cannot change; append another migration for corrections. Migrations run with `BEGIN IMMEDIATE`, are recorded with UTC epoch milliseconds, and must not contain their own transaction statements or perform network operations. Module constructors must not execute paid calls or external effects. SQL modules are trusted control-plane code; namespace conventions are not a security sandbox.

## IDs, time, values

`runId`: funded experiment identity, although foundation runs are explicitly unfunded until verified by economy. `actionId`: durable UUID created before authorization, also the outbox/provider idempotency key. `workKey`: stable business intent unique within a run; selecting the same key cannot create another action. An intentionally different attempt requires an explicit new business key after inspecting prior outcomes. `jobId`, `opportunityId`, `reservationId` and provider/account-scoped `externalEventId` link commitments, charges and effects.

JSON contracts use camelCase; core SQLite uses snake_case. Events/actions have `version: 1`. Times are ISO UTC at module/API boundaries and integer Unix milliseconds in core storage. Tool input/result must be JSON data, not executable authority. Money uses **canonical integer decimal strings in micro-USD** (`1 USD = 1000000`); `money()` validates signed-64-bit nonnegative amounts. Perform arithmetic with bigint or exact SQL integers, never floating-point balances. Summary posted/spendable amounts may be negative when recording unexpected liability. Preserve native currency/conversion/rounding evidence in Usage. No foundation command credits $5 automatically.

## Economy provider

`summary(runId)` returns reconciled financial totals, or null when unavailable. It distinguishes grants, owner funds, subsidies, settled customer receipts, earned revenue, charges, reservations and refund/customer holds. Live and sandbox are distinct. Public totals are an explicit projection of the same authoritative summary, not a second ledger.

`authorize({runId,actionId,tool,limit})` must atomically reserve and return the **same reservation identity** for a retry of that action. Confirm it is still usable on subsequent calls. Denial is `insufficient` or `unavailable`; neither authorizes execution. The authorization must bind the exact run/action/tool/limits and be verifiable by the gateway; the opaque authorization is never public or sent into a model prompt. Even free tool actions use this interface. Paid planning is a normal proposed tool action and receives the same checks.

`settle(reservation,receipt)` is idempotent by action and provider/account/event, posts actual charges including failed calls and provisional charges, and releases only known unused credit atomically. A later correction is append-only. Never discard an actual over-limit charge: post the liability, report it, and refuse new paid spend. The runtime enters exhausted on an over-limit receipt. Holds for uncertain effects must not auto-expire into spendable funds. Payment webhook/event ordering, fees, refunds, verified opening funding and revenue crediting belong to this module; models cannot post credit. The runtime has no direct ledger writes.

All economy control methods must be free, bounded local/control-plane operations. Any billable provider inspection must itself be a separately authorized action, not an invisible side effect of summary/authorization/settlement.

## Tools provider

`capabilities()` lists actual availability. Optional free `refresh()` updates the capability snapshot before selection and outstanding recovery; unavailable refreshes must not produce optimistic capabilities. `execute(action,reservation,signal)` verifies authorization, enforces maximum provider charge/token/runtime/output limits and account scope **before** sending, and uses `actionId` for external idempotency. It must not accept unrestricted billing credentials from agent input. A bound is enforceable, not just a cost estimate. A gateway must refuse execution when the provider cannot enforce the requested limits. Keep exceptions/URLs/credentials out of public diagnostics.

Return a confirmed `ToolReceipt` with version, exact action ID, result, status, actual usage and supporting evidence. `failed` is a confirmed outcome and can be billable. A timeout, transport error, or cancellation after submission is `unknown`, never an assumed free failure. The runtime records the receipt durably **before** settlement. It records the outbox as inflight before `execute` is called.

`reconcile(action,reservation)` is **free, observational**, and never sends mail/publishes/repeats the effect. Return the confirmed receipt, unknown/unavailable, or definitive `not_performed` evidence. An eventually consistent “not found” is not definitive proof. Only a confirmed not-performed result permits another execute attempt, with the original action ID, renewed authorization, and a three-attempt ceiling. Unknown state retains the reservation and outbox. Exactly-once external effects ultimately require provider idempotency or definitive reconciliation; SQLite fencing alone cannot cancel a provider request accepted by a dead worker.

The stop/deadline signal must abort supported execution; usage must still be reconciled. Irrevocable actions already accepted may complete. A replacement worker reconciles an expired worker's running action rather than blind resubmission. Gateways must ensure one action ID cannot execute twice even if lease ownership changes during a slow provider request.

## Business provider

`selectWork(snapshot)` receives balance, capabilities and durable action/receipt history. It is free, local selection and receives the bounded pending `events` snapshot. It returns a bounded Candidate, a wait reason/optional future wakeup, or unavailable. It can discover opportunities through separately proposed authorized research/planning actions; the runtime does not restrict it to an order queue or profession. To use a model, propose the model tool action first, then inspect its stored result on the next selection. Business selection and module constructors must never make paid calls directly.

Opportunity and Order contracts include source/time, cost/revenue estimates, scope, quoted price, allowance, due date, status and payment timing. Estimates and agent interpretations are not verified receipts. Modules persist their own commitments with migrations and deduplicate by their IDs/workKey. Immutable completed outcomes are available in every snapshot; no fragile post-execution callback is required. If no work is ready, return wait with no wakeup to sleep until an operator/external event. An ingress module may use `Store.control(runId,'wake')` **only after its own authentication/event deduplication**; public routes do not directly wake or invoke tools.

## Website provider and HTTP API

`publicContent()` returns deliberately curated offerings/activity/availability. This is a projection contract for integrating the full headquarters later. The foundation serves these through its public page/API. An optional trusted `renderPublic(data)` can supply the public HTML shell using only the public projection; the common CSP remains in force. Customer purchase/delivery endpoints require a separately authenticated implementation and must never expose operator APIs.

- `GET /` and `GET /api/public`: additive `financialRunId` identifies the selected financial run; configured `RuntimeConfig.publicRunId` comes from `WIDEMIND_COMMERCE_RUN_ID`, with latest-run selection retained only when it is absent. Otherwise these return allowlisted run metadata, authoritative summary or null, curated content. No raw receipts, action inputs, messages, reasons or secrets.
- `GET /operator`: login or authenticated run/status screens.
- `POST /api/operator/login`: `{password}`; exact Origin header required; sets HttpOnly/SameSite=Strict cookie and returns CSRF token.
- `GET /api/operator/status`: session required; private runs/actions/events/module health and CSRF token. Reservation authorization is omitted even here.
- `POST /api/operator/runs`: create a paused run in the configured profile.
- `POST /api/operator/runs/<uuid>/{resume,pause,stop,wake}`: durable controls.
- `POST /api/operator/logout`: revoke the session.
- Mutations after login require the session cookie, exact Origin, and `X-CSRF-Token` header (or a `csrf` form field). The same routes without `/api` support ordinary HTML forms and redirect after success. Bodies are bounded to 16 KiB.

Sessions last eight hours, persist across web restarts, and are revoked on logout/password-hash rotation. Only keyed session-token hashes are stored. Sign-in attempts are durably limited to ten per remote address per fifteen-minute window; configure reverse-proxy access controls as appropriate because forwarded addresses are intentionally untrusted. Never put passwords in URLs. The web server cannot execute tools directly; it only records authenticated operator intents.

The integrated shell adds public `/commerce`, private `/operator/integrations`, and self-hosted `/commerce.js`. CSP permits scripts/connections only from the same origin; provider checkout opens an allowlisted PayPal HTTPS page. Service/customer API paths are documented in [BUSINESS_INTEGRATION.md](BUSINESS_INTEGRATION.md).

### Namespaced module routes

A module factory can also return `routes: ModuleRoute[]`. Each exact route is below `/api/modules/<module-id>/...`, uses GET or POST, and returns a JSON body/status. Duplicate or out-of-namespace routes fail startup. This allows commerce/provider APIs to integrate without editing the root router. It does not permit replacing core auth/control routes.

`access: 'operator'` receives core session authentication, plus exact-origin and CSRF enforcement for POST. `access: 'public'` is deliberately unauthenticated by the foundation: the module **must** verify its own customer ownership, webhook signature/event deduplication, or other appropriate authority before reading private data or accepting effects. Public module handlers must never expose raw database records or act as arbitrary tool dispatch. A public route does not inherit operator privilege even when a browser has an operator cookie. Optional `remoteAddress` comes from the actual socket for ingress rate limiting; forwarded headers are untrusted. Query/body/headers are request data; the `operator` flag is server-derived and always false for public routes. No server configuration secrets are included. Root handlers bound bodies to 16 KiB; use separately authorized storage URLs for larger artifacts. The baseline website renderer is server-rendered and needs no JavaScript; a richer frontend must explicitly document/review its CSP needs.

## Inbox and worker status extension

These additions retain contract version 1 and preserve saved actions/receipts. `BusinessSnapshot.events` contains at most 100 `RuntimeEvent` records (`eventId`, authenticated ingress source/account, provider-scoped external ID, kind, private JSON payload, UTC received time). `WorkSelection.acknowledgeEventIds` is optional; existing selectors may omit it, leaving events durable. Only IDs from the inspected snapshot may be acknowledged. Acknowledgments commit atomically with an accepted plan or waiting decision. A newer event/control revision fences stale acknowledgments; untouched backlog beyond the snapshot is scheduled after acknowledged batches. This does not authorize a new paid call or auto-resolve payments.

A trusted, authenticated ingress adapter can call `Store.deliverEvent(runId,event)`. Derive `source`/`accountRef` from verified adapter configuration, not external task data. Deduplication scopes `externalEventId` by source and account, including after consumption/restart. Canonical JSON field ordering is accepted; different run/kind/payload with the same identity is a conflict. The payload limit is 64 KiB UTF-8; HTTP ingress retains the smaller 16 KiB request bound. The core does not provide unauthenticated provider webhook verification, credit funds from event names, or turn raw messages into operator controls.

`GET /api/operator/runtime` and the `runtime` field in operator status add private queue/worker observations. `pendingEvents` is a bounded private run preview and `pendingEventCount` is its full count. `npm run status` uses the configured database/profile and exits 2 when no fresh worker is observed. Core session cookies, the core CSRF header and authenticated form CSRF fields are stripped before forwarding to namespaced module handlers; customer-specific cookies remain available. Modules receive the server-derived `operator` boolean instead of core bearer credentials.

Free asynchronous module calls are deadline-bounded by optional `RuntimeConfig.dependencyMs` (default 5000). Timeouts do not prove that idempotent authorization or settlement did not happen: implementations must retain the existing action/event idempotency semantics. Tool exceptions after dispatch are unknown effects and remain on the reconciliation queue. Synchronous module code must remain bounded; timer races cannot preempt CPU-blocking code. A queue lease now includes an independent unique token, preventing an old lease from matching a deleted/recreated idle job even when its numeric fence resets.

Public module POST handlers additionally receive optional `rawBody` bytes, bounded by the same 16 KiB request cap, for exact provider-signature verification. Verify these bytes before accepting an event; re-serializing parsed JSON is not equivalent. Operator routes do not receive raw bodies, so their core form CSRF values cannot bypass the forwarding filter. The foundation supplies no provider secret or generic “verified payment” flag; each ingress implementation owns verification, account scope and provider-event deduplication.

### Integrated orchestration recovery

Business bridge recovery may confirm a durable zero-cost handoff while `eventState` is still queued/running. `processed` is false and `recoveredOrchestration` true in that receipt. This confirms orchestration ownership, not provider success, final usage or customer fulfillment. It leaves core leases and uncertain inner effects untouched. The next separately authorized step can inspect due core work; reconciliation never calls the business tick itself. Missing invocation evidence yields `not_performed` with a journal reference and the same action ID is retained for the bounded retry.

Tool reconciliation binds the complete saved action and reservation ID before returning a cached receipt or invoking an adapter. A matching action ID alone cannot authorize changed inputs or a receipt from another run.

## Implemented economy bridge

The economy role is now implemented by `src/modules/economy/module.ts`, backed by the protected Python service. See [setup](RUNTIME_ECONOMY.md) and [RPC details](ECONOMY_RUNTIME_BRIDGE.md). Module factories may receive optional private `context.env` when bootstrap is given an explicit environment; it is never included in public RuntimeConfig or projections. Constructors still perform no external effects.

`Economy.confirmNotPerformed` is an optional free control operation for a trusted tool's definitive no-effect observation. It returns renewed, closed (verified zero-charge attempt ceiling), or unavailable; uncertainty retains the cap and prevents execution. Known provisional usage remains unresolved until observational reconciliation supplies the final charge. Optional result failures never discard a valid known bill.
