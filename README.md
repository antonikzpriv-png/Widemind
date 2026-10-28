# WideMind

**Starts with $5. Everything after that is earned.**

WideMind is a planned autonomous online business agent. It starts a survival run with **$5 of operating credit**, chooses opportunities, creates useful work, earns real customer revenue, and uses those earnings to fund its next actions.

It is an applied AI product and research experiment. Building a new foundation model is outside this project's scope.

## Current status

This repository contains the project foundation and implementation plan. The runtime, website, Gmail integration, tools, and payments are **planned, not implemented**.

Start with [AGENTS.md](AGENTS.md) for developer instructions and [the roadmap](docs/ROADMAP.md) for implementation milestones.

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

## Start building

Choose and document a small, maintainable stack, then implement the first runnable milestone. No dependency manifest, build command, or deployment configuration has been established yet.

The first integrated demonstration should exercise a persistent worker, externally enforced budget, one tool action, a deliverable, payment reconciliation, and a visible balance update. Mark every simulated dependency explicitly.

The initial technical demonstration may use one service. The product remains an open-ended business agent.

## Decisions still open

Application stack, hosting, domain/path, model provider, Gmail account, payment processor, and graphics/publishing providers have not been configured. Record these choices during implementation without inventing working credentials or integrations.
