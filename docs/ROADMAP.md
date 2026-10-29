# WideMind implementation roadmap

## Current status

The runnable foundation and integrated economy, tools, business, public commerce and operator modules are implemented. Local acceptance uses the real persistent worker and all three services with an explicitly local PayPal protocol fixture. No live payment, dedicated mailbox verification, deployed website or real survival run is claimed.

Milestone 1 is implemented. Milestones 2–6 have working local implementations and deterministic integration coverage; live/provider acceptance remains conditional on credentials, accounts, isolation, network configuration and funding. The checkboxes below track code availability, not completed live acceptance. See [INTEGRATED_SETUP.md](INTEGRATED_SETUP.md) and the service integration guides. Milestone 7 remains unstarted.

The first business model is an experiment, not a permanent restriction. The five Codex roles below build one initial WideMind identity.

## 1. Runnable foundation

- [x] Select and document the application stack, configuration, migrations, and actual development commands.
- [x] Implement a persistent worker and minimal website/API.
- [x] Persist runs, actions, results, and errors.
- [x] Add explicit active, waiting, paused, exhausted, stopped, and failed states.

**Deliverable:** A locally runnable website and worker using a deterministic demonstration task.

**Acceptance:** Restarting either process preserves state. Closing the website does not stop the worker. Waiting does not repeatedly trigger model calls.

## 2. Enforced economy

- [x] Implement the external ledger and $5 opening credit.
- [x] Reserve funds before paid actions and reconcile actual charges.
- [x] Track model/tool costs, fees, refunds, and the selected operating-cost boundary.
- [x] Implement exhaustion, revenue-crediting, and restart rules.
- [x] Separate test transactions, owner funding, and subsidies.

**Deliverable:** A budget controller used by every billable execution path.

**Acceptance:** Concurrent work cannot overspend credit. Duplicate events cannot duplicate earnings. Unsettled or claimed revenue is not spendable cash. WideMind cannot alter the ledger.

## 3. Runtime and opportunity selection

- [x] Implement planning, execution, result evaluation, and persistent memory.
- [x] Let WideMind propose and compare opportunities.
- [x] Persist commitments, cost estimates, expected outcomes, and actual results.
- [x] Add bounded retries, cancellation, resumable jobs, and idle behavior.

**Deliverable:** WideMind chooses an approach and completes a multi-step task using registered tools.

**Acceptance:** It resumes after interruption and avoids repeatedly paying for the same unsuccessful action. Its business approach can change without rewriting the runtime.

## 4. Tools and dedicated accounts

- [x] Add browser/internet, workspace files, and code-execution adapters.
- [x] Add Gmail reading/sending adapters and durable conversation tracking; dedicated OAuth setup remains external.
- [x] Add local SVG generation and configured model/image adapters.
- [x] Add publishing through configured accounts.
- [x] Document credentials, quotas, account setup, and verification dependencies.

**Deliverable:** Working adapters with structured inputs and recorded results.

**Acceptance:** Test mail reaches a controlled inbox. Files and graphics can be retrieved. Interrupted external actions are reconciled before retry. Secrets stay out of public logs and model-visible configuration.

Provider onboarding and verification may need owner input. Continue independent implementation while resolving specific dependencies.

## 5. Commerce, outreach, and delivery

- [x] Implement opportunities, offers, conversations, orders, commitments, and deliverables.
- [x] Connect business payments through verified events.
- [x] Provide configured dedicated-account email adapters and record outreach policy; live inbox acceptance remains unverified.
- [x] Record recipients, replies, opt-outs, quoted terms, deadlines, and fulfillment.
- [x] Let WideMind choose offerings supported by its tools.

**Deliverable:** An end-to-end customer workflow from request or opportunity to paid delivery.

**Acceptance:** A controlled pilot order produces an offer, payment record, deliverable, and reconciled balance. Failed delivery and refunds remain visible. Outreach avoids duplicate sends and respects recorded opt-outs.

Resolve payment timing before implementation so the agent can fund the work it promises to deliver.

## 6. Public headquarters and private operations

- [x] Show current offerings, run status, financial summaries, and completed work.
- [x] Provide contact, purchase, and private delivery flows.
- [x] Publish a selected, redacted activity feed.
- [x] Add private integration setup, diagnostics, reconciliation, and pause/resume/stop controls.
- [x] Distinguish independent customer receipts from test payments and owner support.

**Deliverable:** A website connected to the persistent worker and ledger.

**Acceptance:** Visitors can understand and hire WideMind. Customer information, mailbox contents, credentials, and unpublished work remain private. Public financial totals reconcile with accounting.

## 7. Real paid survival pilot

- [ ] Document a run with exactly $5 opening operating credit.
- [ ] Enable configured real tools and customer work.
- [ ] Record interventions, subsidies, and outside funding.
- [ ] Publish measured outcomes using the selected accounting method.

**Deliverable:** An auditable real-world experiment.

**Acceptance:** An independent customer pays for useful delivered work, and verified net receipts fund further operation. Exhaustion stops new paid execution while preserving history and reconciliation.

A job covering its delivery costs is the first milestone. Sustained profitability also accounts for ongoing hosting, tools, subscriptions, and other defined operating expenses.

## Five-Codex work allocation

| Owner | Responsibility |
| --- | --- |
| Coordinator | Shared contracts, migrations, integration, and release verification |
| Runtime engineer | Planning loop, jobs, memory, recovery, and opportunity selection |
| Tools engineer | Browser, files, execution, Gmail, graphics, and publishing |
| Economy/commerce engineer | Ledger, reservations, payments, offers, orders, and delivery records |
| Website engineer | Public headquarters, customer flows, and private operator interface |

Freeze shared interfaces and file ownership before parallel implementation. Coordinate schema and dependency changes through the integration owner.

Each handoff includes changed files/branch, launch instructions, verification results, known gaps, and configuration requirements.

## Decisions to record

- Stack, hosting, database, queue, and deployment arrangement.
- Website domain/path and authentication.
- Model, browser, graphics, email, publishing, and payment providers.
- Operating-cost allocation and customer payment timing.
- Credential provisioning, quotas, and provider verification.
- Run-ending/reactivation policy and experiment reporting.
- Repository licensing, if the owner chooses to publish a license.

Record choices before implementing dependent integrations. Missing credentials should block only the work that needs them.

## Definition of done

A runnable slice has its implementation, configuration, migrations where relevant, appropriate checks, actual launch instructions, and known limitations.

A plan, mock connector, or screenshot alone is not a completed live capability. Keep simulation and live-provider evidence clearly separated.
