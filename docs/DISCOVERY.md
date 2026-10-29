# Scheduled discovery and bounded experiments

This extends the integrated **PayPal** business service. The central Stripe alternative under `src/modules/brief-business` and root Python package retains its own configuration and database. Do not combine accounting files or balances.

Discovery reads owner-selected market sources, matches configured need terms against actual retrieved text, and records a proposed offering through the existing opportunity intake. This is evidence matching, not proof of willingness to pay. A source observation, proposed product, prototype, customer response, verified conversion and measured contribution are different facts.

## Configure the deployment

Follow [integrated setup](INTEGRATED_SETUP.md). Run the Node web and independent worker plus the isolated economy, tools and business services. Configure the existing commerce run in matching mode. Discovery is disabled until the operator enables it; installation does not fund a run.

Open the private **Discovery** page from operator navigation. Start with [the disabled example](../examples/discovery.example.json), replace the placeholder source with an authorized real source, review scope and allowances, and enable discovery. Neither the example URL nor its terms are a verified opportunity.

Configure markets, need terms, proposed services/scopes, freshness, intervals, cooldowns, attempt caps and a lifetime allocation allowance. `kind: "experiment"` deliberately has no customer. A `kind: "lead"` market may specify `lead_email`; the contact must occur in the observed source text before becoming an identified lead. That occurrence does not prove acceptance, payment or permission to contact someone.

Enable `research.fetch` in the **integrated tools** owner configuration, with bounded `max_text_chars`, `max_links`, `max_response_bytes` and exact `allowed_hosts`. Enable only creation tools needed by selected services, such as `software.create` or `graphics.create`. Network operations also require tools policy `allow_external_effects: true`, its documented SSRF protections and allowed network destinations. See [tools integration](TOOLS_INTEGRATION.md) and [HTTP transport](HTTP-TRANSPORT.md). Research does not require arbitrary messaging or publishing permissions.

Every research read and prototype operation obtains a real integrated economy reservation, including zero direct-charge operations. Local compute/network remain owner-supplied, unallocated costs. Paid providers require existing enforceable tariffs and limits; discovery introduces no unmetered planning or model endpoint.

The bounded research request asks for up to 12,000 text characters and no extracted links. Configure `research.fetch.max_text_chars` to at least 12,000 and an appropriate response-byte cap; stricter tool caps reject the request before retrieval rather than being overridden.

## Scheduling and recovery

Configuration, attempts, evidence, experiments and outcomes live in business storage. The append-only migration does not alter accounting schemas. The existing Node worker selects scheduled business events and dispatches `business.step`; there is no additional autonomous scheduler or model loop. Waiting and deadline evaluation do not call paid tools.

Deploy web, worker and business service at the same revision. Stop those processes, back up the business database with its WAL and the separate runtime/economy/tools state, reinstall the business package if using wheels, then restart. `008_discovery.sql` applies through the existing business store migration runner. Existing opportunities, quotes, orders and seven earlier migrations remain intact. No balance is created by this migration.

Schedules survive restarts. Attempt identities, attempt caps, conservative allowance allocations and opportunity identities survive reconfiguration. Disabling/re-enabling does not reset spending history. Deduplication prevents repeated opportunities. Cooldowns and expiry prevent stale discoveries being treated as fresh proposals. Retrieval time establishes observation freshness, not the original publication date.

Runtime pause/stop, disabled tools, exhausted allowances and failed authorization prevent new execution. Unknown effects retain original IDs and liabilities; reconciliation observes the original operation rather than creating another. Review blocked work/reservations before explicitly authorizing another attempt. An operator wake cannot raise budget or bypass cooldowns.

Free outcome observation rotates through at most five pending work-cost records and five orders per wake, with durable cursors; immutable final reservation costs are cached. Historical completed orders remain in the rotation for refunds. The owner-selected observation interval and history size determine update latency. New or unresolved records prevent incomplete positive performance credit. Use the private recovery controls to reconcile an original effect; experiment recovery keeps its original plan, IDs, allowance and deadline.

## Experiments and outcomes

Start an experiment explicitly from a discovery. Supply a hypothesis, allowance, deadline, success criteria and supported stop conditions. It creates and validates at most one private sample through the registered service's existing plan and scope validator. It fabricates no customer, quote, order, payment or delivery commitment. Publishing/outreach still require their existing authorization.

A valid sample proves only that its checked scope passed. Commercial success requires verified outcomes. Deadline, allowance and single-attempt bounds stop repeated spending; expiry is a free scheduled transition. Stopping does not erase charges, undo accepted external effects, cancel customer obligations or refund a customer.

Required machine-enforced `stop_conditions` are `deadline`, `allowance_exhausted`, `single_attempt`, `scope_failure` and `operator_stop`; arbitrary prose is rejected. `success_criteria` requires `artifact_valid: true` and may add `min_conversions` and `min_contribution_micro_usd`. Deadlines are UTC epoch seconds within thirty days. The private attribution form can link a later real order of the same service to an experiment using an actual evidence reference; attribution itself creates no response, conversion or payment.

Private outcomes show recorded responses, conversions, revisions, delivery costs, refunds and contribution, with uncertainty visible. Positive priority adaptation requires completed, paid work and final authoritative costs. Unpaid promises, expected margins and provisional costs cannot improve performance scores. Refunds and fees reduce measured contribution. Adaptation stays within operator limits and cannot grant capabilities, outreach permissions or wallet funds. Contribution excludes unallocated hosting/development costs and is not full profit.

Source text, contact details, unpublished samples and internal outcomes remain private. Public activity retains its separate redacted projection.

## Reproduce fixture qualification

```sh
npm ci
npm run services:setup -- --with-tests
npm run services:test
npm run check
npm run discovery:demo -- --fixtures
npm run discovery:demo -- --fixtures --browser
```

The demonstration uses actual local services, scheduler, reservations and tools with controlled source-page fixtures. It creates no real customer or payment and proves no profit. Browser checks use the authenticated private page; set `WIDEMIND_CHROMIUM` if needed. Test-mode fixture sources remain explicitly identified and are rejected by live discovery configuration.

Real operation needs owner-selected sources/markets, authorized tools, a funded run, protected deployment and allowed source/provider networking. Fixture success verifies no real opportunity, customer response or revenue.

See [executed validation and audit repairs](DISCOVERY_VALIDATION_20261029.md).
