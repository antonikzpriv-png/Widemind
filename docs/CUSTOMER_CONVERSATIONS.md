# Natural customer conversations

This extends the default integrated **PayPal** business service in `services/business`, its existing Gmail tools service, `/commerce`, and the private `/operator/integrations` console. The separately configured Stripe brief-business deployment is preserved; its accounts, database, scheduler and tools are not repurposed.

## Behavior

Customers can start with an ordinary message, ask questions, clarify requirements, negotiate, request changes, report a problem or opt out. The model interprets one bounded customer turn using the existing `ai.generate` structured-output adapter. Application code validates the output and owns every transition. It uses fixed, factual response templates around validated scope and terms; model-authored text cannot promise arbitrary work, grant permissions, change policy or mark money paid.

State is private and durable per customer/thread/request. The model sees bounded excerpts from that thread's incoming messages and selected scope/quote facts. It receives no other customer's history, service credentials, outbound portal-access tokens or financial authority. Exact full messages and quote terms remain in the private business database; large Unicode scope does not silently replace the original terms with its prompt excerpt. Truncated transport content requires clarification/operator attention rather than a quote based on an incomplete prefix.

An incomplete request receives concrete questions. Complete supported requests produce a proposal with exact USD price, deliverables, exclusions, turnaround after verified funding/authorization, revision allowance, expiry, quote ID, version and digest. Negotiation is bounded by the operator's floor and standard price. Scope/price changes create a new version and supersede the old one. Questions preserve negotiated terms. Unsupported capability, capacity and cost limits are explained without repeatedly asking for already supplied requirements.

Explicit acceptance takes place through the token-authenticated private customer portal and supplies the exact current quote ID/version/digest. A generic “yes”, conditional acceptance, old version, changed terms, another customer's token or pending unassessed message cannot create an order. Final validation, policy/capacity checks and order creation share one write transaction. Existing structured quotes import as version 1 without rewriting their terms. A revised historical quote uses an internal linked opportunity when necessary to preserve the original unique core quote/order.

Payment remains economy-owned: “I paid”, screenshots, invoices and model assertions change no payment status or available credit. Actual checkout, verified PayPal transactions, holds and fulfillment continue through the existing services. No extra conversation ledger is created.

Complaints, conflicts and changes to accepted commitments latch an operator hold. Later messages/model output cannot clear it. The protected operator view shows conversation state, exact proposals, private messages, uncertain effects and intervention reasons. An authenticated operator can resolve a reviewed case with evidence; resolution requests a fresh assessment and cannot accept old terms, rewrite an existing order or resubscribe an opted-out customer. Reusing old resolution evidence cannot clear a later hold. Post-acceptance changes/revisions require this review; the default included revision allowance is zero.

## Setup

1. Follow [INTEGRATED_SETUP.md](INTEGRATED_SETUP.md) and keep separate economy/tools/business virtual environments and service identities. PayPal remains the selected integrated payment provider. Set up a funded test run and the normal persistent Node worker. No browser session is required to keep work running.
2. Enable the dedicated `ai.generate` adapter in the **integrated tools service** owner configuration. Follow [AI provider setup](../services/tools/docs/AI-PROVIDERS.md): configure the selected model, private secret reference, exact owner-verified integer tariffs, documented context ceiling, input/output bounds and allowed external effects. Installations and fixture results do not verify an account or price. The adapter's enforceable quote must fit the configured conversation cap; increasing a model's context/tariff may require explicitly reviewing that cap.
3. Configure conversation policy through the existing authenticated business policy screen/API. These fields extend the full policy document; submit the other existing fields as well:

```json
{
  "conversation_enabled": true,
  "conversation_model_max_micro_usd": 100000,
  "conversation_run_budget_micro_usd": 1000000,
  "conversation_max_turns": 20,
  "conversation_model_max_output_tokens": 2000,
  "conversation_price_floors": {"svg-graphic": 1500000},
  "conversation_revision_allowance": 0,
  "conversation_followups": 2,
  "conversation_followup_seconds": 86400,
  "conversation_quiet_start_hour": 20,
  "conversation_quiet_end_hour": 8
}
```

The floor example requires `svg-graphic` in the existing `prices` map with a standard price at least 1,500,000 micro-USD. Floors are whole USD cents; omitted floors equal standard prices. Service availability, `capabilities`, `max_job_micro_usd`, `max_open_orders`, experiment allowance and actual operating balance still constrain work. Conversation allocations form a separate explicit cumulative model ceiling, conservatively counted at reserved maxima rather than refunded by model claims. They never increase cash. Each turn has at most one model intent; dispatch uses the smaller model/job cap and exact durable action identity. Actual usage, failed calls and provisional/unknown charges remain in the existing economy/gateway. Policy is rechecked before spending admission and before a commitment.

4. Portal-only conversations need no Gmail credential. For Gmail, complete [CONVERSATION_TRANSPORT.md](CONVERSATION_TRANSPORT.md), the dedicated OAuth/recipient configuration and the explicit receiving-header trust selection. From text alone is not verified identity; forwarded/ambiguous/HTML-only mail may need portal/operator clarification. Header evidence supports routine triage; it never authorizes commercial acceptance.
5. Set owner-controlled `WIDEMIND_PUBLIC_ORIGIN` to the exact HTTPS public origin (loopback HTTP for development). A Gmail prospect receives a private, revocable portal link in the first quoted response. Access is atomically claimed with the exact journaled outbound body, uses a URL fragment, and is not reminted by routine questions. A later portal message joins the same authorized thread but replies through the portal. Without a configured origin or valid reply identity, the case remains available for intervention.
6. Optional `WIDEMIND_CONVERSATION_GMAIL_POLL_SECONDS=300` enables free inbox scheduling through the existing worker; default **0** disables it. Allowed values are 0 or 60–86400. No second worker or paid idle model loop is introduced. Blocked/unknown mailbox work prevents fresh scan IDs until recovery. The Stripe `business:sync-inbox` command remains separate.

Quiet hours are **UTC** and apply to unsolicited follow-ups, not replies to a new customer message. Equal start/end hours disable quiet hours. Follow-ups use fixed text, no inference, an overall per-thread count, a minimum interval and quote expiry. Explicit opt-outs are stored immediately without a model or operating credit; future queued sends/follow-ups are suppressed. Consent and pause/stop state are rechecked after capability/reservation I/O immediately before dispatch. Already accepted external mail cannot be recalled.

## API and recovery

Public Node routes retain existing rate limits and token ownership:

- `POST /api/modules/business/request`: initial `{requestId, customerToken?, email, message}`; structured `service/scope` intake remains supported.
- `POST /api/modules/business/message`: `{token, messageId, body, runId?}`. The server derives customer, opportunity and thread from the token; supplied email/thread IDs do not override them. Keep the same message ID/body on transport retry.
- `POST /api/modules/business/customer`: token-scoped conversation, exact proposals, order status and private publications.
- `POST /api/modules/business/accept`: `{token, quoteId, version, digest, runId?}` for conversational proposals. Legacy unconverted quotes retain their existing acceptance contract.
- `POST /api/modules/business/opt-out`: token-scoped immediate suppression, including while runtime is paused/stopped. Revocation of a portal access token remains a separate action.
- Operator-only `GET /api/modules/business/conversations`, `GET /api/modules/business/conversation?runId=...&id=...`, and `POST /api/modules/business/conversation-resolve` expose private state and evidence-backed hold resolution. Mutation requires the normal operator session, exact Origin and CSRF token, never a customer token or model flag.

The protected service interfaces mirror these routes; only trusted Node/service clients receive the internal bearer. Do not expose that token or the business database to the agent workspace.

The effect journal persists model reservations, calls and sends before dispatch. Restart or response loss does not authorize another provider effect. Use the existing private effect reconciliation controls for uncertain actions. Observational recovery schedules the existing intent and reads its cached result; it never resends a message or reruns paid inference merely because its response was absent. Confirmed paid failures require a newly approved plan/authorization, not reuse of a settled reservation. Pause retains unfinished conversation events for resume; stop prevents new effects while observation remains available.

## Verification

Development uses controlled addresses, local fixtures and synthetic model/provider usage. No real customer message, Gmail delivery, model charge or payment is claimed. Tests cover multi-turn clarification/negotiation, ambiguous and stale acceptance, exact ownership, changed terms, capacity, replay/restart, unknown model/mail/follow-up recovery, Unicode bounds, model unavailability, malicious authority fields, payment claims, consent races, complaint holds, quiet periods and structured-workflow compatibility. The actual Gmail OAuth/MIME adapter is exercised through the integrated tools gateway with a controlled transport.

Run `npm run services:setup -- --with-tests`, `npm run services:test`, `npm run check`, and `npm run integration:browser`. The latter uses explicit synthetic PayPal/provider boundaries and real local service processes/browser forms; it does not verify a live account. The preserved Stripe fixture demonstration is `npm run business:demo -- --fixtures` with a Python interpreter containing the root Stripe fixture dependencies selected through `WIDEMIND_TEST_PYTHON`. See the [conversation validation report](CUSTOMER_CONVERSATION_VALIDATION.md) for the actual qualified commit, results and environment limits.

Migration `009_customer_conversations.sql` adds business-private state only. Stop old business writers and back up the existing business SQLite database before upgrading; start the new service to apply its additive migration. Do not apply it to an economy or standalone-tools database.

Build release wheels from a clean checkout/export. Reusing an old setuptools build directory after migration filenames change can retain obsolete files; verify the wheel contains Discovery migration 8 and conversation migration 9 exactly once before installation.
