# WideMind

**Starts with $5. Everything after that is earned.**

WideMind is a planned autonomous online business agent. It starts a survival run with **$5 of operating credit**, chooses opportunities, creates useful work, earns real customer revenue, and uses those earnings to fund its next actions.

It is an applied AI product and research experiment. Building a new foundation model is outside this project's scope.

## Current status

The runnable foundation is implemented: a Node.js 24/TypeScript web app, an independent persistent worker, SQLite migrations/storage, operator authentication, durable actions/outbox/recovery, typed module contracts, worker-health diagnostics, and a durable event inbox.

The Python economy control plane implements the ledger, reservations, Stripe Checkout/webhooks, durable reconciliation, uncertain-checkout recovery and redacted financial reporting. Its registered TypeScript module connects the runtime to that single accounting authority; see [runtime economy setup](docs/RUNTIME_ECONOMY.md). Real Stripe accounts and sandbox verification remain configuration requirements. The owner-operated tools SDK and its Gmail/model/research/files/graphics/code/publishing adapters are preserved from newer main; see [tools setup](docs/TOOLS-SETUP.md) and [capabilities](docs/CAPABILITIES.md). They are not automatically registered as Node runtime providers. Their standalone development budget is separate from the central operating ledger; paid standalone execution is blocked when central runtime configuration is present. Missing providers report unavailable. The explicit `demo` profile performs one real zero-cost local action; it does not invent customer revenue.

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

See the [runtime audit and extension report](docs/RUNTIME_AUDIT.md) for reproduced defects, repairs, and current verification.

## Start building

The foundation uses Node.js 24, TypeScript and SQLite, with independent web and worker processes; see [FOUNDATION.md](docs/FOUNDATION.md). The economy uses Python 3.12+, FastAPI, HTTPX and a separate protected SQLite database. Install with `python -m pip install -r requirements.lock -e .`, validate with `python -m pytest -q`, and follow [economy setup](docs/ECONOMY_SETUP.md) and [operations](docs/ECONOMY_OPERATIONS.md) for its HTTP and reconciliation-worker processes.

The first integrated demonstration should exercise a persistent worker, externally enforced budget, one tool action, a deliverable, payment reconciliation, and a visible balance update. Mark every simulated dependency explicitly.

The initial technical demonstration may use one service. The product remains an open-ended business agent.

## Decisions still open

Application stacks and local storage are selected; Stripe is the implemented payment adapter. Hosting, domain, real payment account credentials, model provider, Gmail and graphics/publishing providers remain unconfigured. Record these choices during implementation without inventing working credentials or integrations.
