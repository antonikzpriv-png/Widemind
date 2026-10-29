# WideMind

**Starts with $5. Everything after that is earned.**

WideMind is an autonomous online business agent under development. It starts a survival run with **$5 of operating credit**, chooses opportunities, creates useful work, earns real customer revenue, and uses those earnings to fund its next actions.

It is an applied AI product and research experiment. Building a new foundation model is outside this project's scope.

## Current status

This branch contains a separate Python application. The [29 October full audit](docs/FULL_APP_AUDIT_20261029.md) found that current main has moved to a different Node/TypeScript foundation; the business module has not been ported there. This branch must not be merged directly over that foundation.

WideMind now has a responsive public website and private operator console, a durable business/fulfillment worker, exact-money economy, Stripe Checkout and signed webhooks, dedicated Gmail OAuth/read/send, bounded sourced OpenAI research, and persistent runtime controls. The UI follows WideClarity's monochrome visual style.

The production clients are implemented. Live operation still requires the owner's configured credentials, controlled accounts, approved network access and deployment isolation. Local provider tests use explicit offline transports; no independent paying customer or real earnings are claimed. See [completion evidence and remaining setup](docs/COMPLETION_STATUS.md).

Start with [AGENTS.md](AGENTS.md), [the build plan](docs/BUILD_PLAN.md), and [operations](docs/OPERATIONS.md).

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

## Run the website and demonstration

Python 3.11+ is required; production runtime uses the standard library.

```sh
python -m unittest discover -s tests -v
python -m widemind serve-demo --directory /tmp/widemind-demo --seed --token-file /tmp/widemind-demo-operator-token --port 8080
```

Open the local public page and `/console`. The demo creates its operator token in the requested owner-only file; enter it in the console. It stays in browser memory only. All demo mail, payments and research are labelled fixtures. A separate worker advances work even with the browser closed.

For the noninteractive demonstration:

```sh
python -m widemind demo --directory /tmp/widemind-batch-demo
```

Repeat against the same directory to verify idempotency. Existing pre-audit databases need the documented one-time run-mode adoption in [BUSINESS.md](docs/BUSINESS.md).

For production or controlled provider-test setup, use [.env.example](.env.example), [operations](docs/OPERATIONS.md), [economy](docs/LIVE_ECONOMY.md), and [tools](docs/LIVE_TOOLS.md). `python -m widemind check-config` prints missing configuration **names**, never secret values. `python -m widemind initialize --evidence-reference YOUR_REVIEW_REFERENCE` explicitly creates a paused run with labelled opening credit. API and worker construction never grant funds or resume it.

## Decisions still open

Python/SQLite, Stripe and OpenAI are selected. Dedicated Gmail and real-provider clients are implemented. Actual credentials, dedicated accounts, live hosting/domain, source permissions, verified model prices, provider qualification and any additional graphics/software/publishing adapters remain owner configuration or future service work.
