# WideMind

**Starts with $5. Everything after that is earned.**

WideMind is a planned autonomous online business agent. It starts a survival run with **$5 of operating credit**, chooses opportunities, creates useful work, earns real customer revenue, and uses those earnings to fund its next actions.

It is an applied AI product and research experiment. Building a new foundation model is outside this project's scope.

## Current status

The runnable foundation is implemented: a Node.js 24/TypeScript web app, an independent persistent worker, SQLite migrations/storage, operator authentication, durable actions/outbox/recovery, typed module contracts, worker-health diagnostics, and a durable event inbox.

Economy, business, customer commerce and dedicated-account tool adapters are implemented as authenticated services. **PayPal Business** is the selected new checkout provider. Existing Stripe support is retained explicitly; it is not selected silently. Live accounts, credentials, funding, HTTPS and a real customer pilot are still required before claiming autonomous earnings.

Start with [integrated setup](docs/INTEGRATED_SETUP.md) for all services, the private customer portal and payment workflow. The explicit zero-cost `demo` profile remains available for foundation-only checks.

```sh
npm ci
cp .env.example .env
npm run migrate
npm run check
npm run dev          # terminal 1: http://127.0.0.1:3000
npm run worker:dev   # terminal 2: persistent worker
npm run status       # private worker/queue status; exit 2 if no worker is fresh
```

Configure the operator password hash before creating or controlling runs. See [setup and deployment](docs/FOUNDATION.md) for exact authentication, demo, production, and environment instructions, and [integration contracts](docs/INTERFACES.md) for module registration and migrations. Start with [AGENTS.md](AGENTS.md) for contributor instructions.

## Standalone tools compatibility

The other agent's Python tools gateway remains intact under `src/widemind`, with its root Python package, tests and [tools setup](docs/TOOLS-SETUP.md), [capability table](docs/CAPABILITIES.md) and provider documentation. Install/run it in a separate environment. The integrated application uses the isolated `services/tools` package and authoritative `services/economy` ledger, not the standalone demonstration wallet. Browser/Docker sandbox requirements from the tools work remain in force. Neither installation verifies live credentials by itself.

## What WideMind should be able to do

- Browse the internet and research opportunities.
- Use its own Gmail and dedicated service accounts.
- Contact real people, discuss requests, negotiate offers, and deliver work.
- Design graphics, write content, create files, and build software.
- Publish products and services through connected infrastructure.
- Remember customers, commitments, experiments, costs, and results.
- Choose and change its business approach as it learns what earns money.

Research briefs, graphics, small applications, and other services are possible starting offers. The implementation must preserve the ability to choose other supported opportunities.

## How the experiment works

1. Begin a run with $5 of operating credit.
2. Find or receive an opportunity.
3. Estimate its cost and decide whether to pursue it.
4. Use configured tools to communicate, build, sell, and deliver.
5. Reconcile real payments and actual operating charges.
6. Continue while the available balance can fund the next paid action.

The balance and spending controls live outside the agent's editable workspace. Agent claims, unpaid invoices, and simulated customer payments cannot create real credit.

Report development expenses, hosting, shared services, and owner subsidies transparently. A useful pilot result is an independently paid job whose net receipts exceed its measured delivery costs; longer-term survival must account for ongoing operating costs too.

## Website and runtime

The website is WideMind's public headquarters: introduction, offerings, customer contact, purchases, delivery access, activity, and financial summaries.

A persistent background worker runs WideMind independently of an open webpage. A private operator area manages integrations, diagnostics, accounts, and pause/resume controls.

Public activity uses a deliberately selected projection of events. Customer conversations, credentials, private deliverables, and internal reasoning remain private.

## Read the foundation

| Document | Purpose |
| --- | --- |
| [AGENTS.md](AGENTS.md) | Instructions for Codex agents contributing to this repository |
| [Vision](docs/VISION.md) | Product behavior, autonomy, and success criteria |
| [Architecture](docs/ARCHITECTURE.md) | Proposed components and integration boundaries |
| [Economy](docs/ECONOMY.md) | Budget, reservations, payments, costs, and exhaustion |
| [Roadmap](docs/ROADMAP.md) | Build sequence, acceptance criteria, and five-agent work allocation |

See [integrated implementation verification](docs/INTEGRATION_VERIFICATION.md) for current results and prerequisites, and the [runtime audit and extension report](docs/RUNTIME_AUDIT.md) for reproduced defects, repairs, and current verification.

## Start building

The initial stack is Node.js 24, TypeScript, and SQLite, with separate web and worker commands in this repository. Install, migration, development, build, test, and worker commands are implemented and documented in [FOUNDATION.md](docs/FOUNDATION.md).

The first integrated demonstration should exercise a persistent worker, externally enforced budget, one tool action, a deliverable, payment reconciliation, and a visible balance update. Mark every simulated dependency explicitly.

The initial technical demonstration may use one service. The product remains an open-ended business agent.

## Decisions still open

The application stack and local storage are selected. Hosting/domain and live accounts are not provisioned. The selected integration targets are PayPal Business, OpenAI and dedicated Gmail; graphics/publishing need their documented account configuration. Record these choices during implementation without inventing working credentials or integrations.
