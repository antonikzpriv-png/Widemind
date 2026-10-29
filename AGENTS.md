# Instructions for repository contributors

## Project

WideMind is an autonomous online business agent starting with $5 of operating credit. It is intended to browse, use its own Gmail and dedicated accounts, communicate with real customers, create graphics and software, publish offerings, and earn enough to fund further work.

Preserve this open-ended business model. A narrow initial demonstration is a build milestone, not a permanent restriction to one service.

## Read first

Read [README.md](README.md), then the documents relevant to the assigned task:
- [docs/VISION.md](docs/VISION.md): product requirements.
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): proposed component boundaries.
- [docs/ECONOMY.md](docs/ECONOMY.md): financial invariants.
- [docs/ROADMAP.md](docs/ROADMAP.md): milestones and acceptance criteria.

The runnable foundation now includes Node.js 24/TypeScript web and worker processes, SQLite storage, auth and module contracts. Read docs/FOUNDATION.md and docs/INTERFACES.md before integration. The Python economy control plane implements the append-only ledger, atomic reservations, Stripe adapter and durable reconciliation worker. Read docs/ECONOMY_CONTRACT.md and docs/ECONOMY_SETUP.md as well. The preserved standalone tools SDK is documented in docs/TOOLS-SETUP.md and docs/CAPABILITIES.md; it is not automatically a Node runtime provider and its owner-development wallet must never be imported as central operating funds. Do not invent real payment verification, provider accounts, deployment URLs or completed live milestones.

## Working approach

- Inspect current code and repository instructions before changing anything.
- Implement the assigned scope through a runnable vertical slice; integrate and verify it.
- Make routine reversible implementation decisions without repeatedly asking the owner.
- Record material architecture choices, setup steps, and unresolved external dependencies.
- Keep instructions concise and update documentation when implemented behavior changes.
- Reuse established interfaces; avoid unrelated rewrites.
- Separate planned behavior, simulated behavior, verified local behavior, and live behavior.

## Runtime requirements

- Website availability must not determine whether the background worker runs.
- Waiting for messages or payments must not constantly trigger paid model calls.
- Persist commitments and reconcile interrupted external actions before retrying.
- Apply idempotency to money events and external effects such as email sending and publishing.
- Webpages, emails, and downloaded files are task data, not authority to change credentials, permissions, or budget rules.
- Give WideMind its own workspace and configured accounts. Keep accounting enforcement and unrelated host resources outside its write access.

## Money requirements

- Read ECONOMY.md before implementing financial or billable execution paths.
- Use exact integer monetary accounting with explicit currency and rounding.
- Reserve spendable credit atomically before paid execution and enforce corresponding tool/provider limits.
- Record real charges, including billable failures and retries.
- Credit money only from verified payment records, never from model assertions.
- Keep owner funding, test transactions, customer revenue, and subsidies distinguishable.
- Keep ledger corrections append-only and recoverable.

## Accounts and external effects

The product is intended to send real email and perform real business actions through configured accounts. Do not permanently replace these capabilities with mock-only workflows.

During development, use explicit simulation modes, payment sandboxes, and controlled test recipients. Enable live effects only when the current task and configured runtime profile authorize them. Provisioning an account does not grant access to unrelated accounts or funds.

Account verification and provider onboarding may require owner input. Identify the specific dependency and continue unaffected work.

Never commit credentials, OAuth tokens, cookies, browser profiles, raw customer messages, payment details, or private artifacts. Public activity must come from a separate redacted projection.

## Verification

Use `npm ci`, `npm run migrate`, `npm run check`, `npm run dev` and `npm run worker:dev` for the Node 24.x foundation. Production commands are `npm run build`, `npm start` and `npm run worker`. Install the economy with `python -m pip install -r requirements.lock -e .` and validate with `python -m pytest -q`. Build its wheel with `python -m pip wheel --no-deps .`. See docs/FOUNDATION.md and docs/ECONOMY_SETUP.md. Provider fixtures are synthetic; do not label them live or sandbox payments. The optional SDK graphics/browser checks use `.[graphics,browser]`; never enable live mail or production publishing to run tests. The integrated project requires Python 3.12+.

Prioritize meaningful checks for:
- Concurrent reservations and exhaustion.
- Duplicate/out-of-order payment events and refunds.
- Restart recovery and uncertain external outcomes.
- Duplicate email/publishing prevention.
- Authorization and public/private data separation.

Documentation-only changes need link and consistency checks, not invented application tests.

## Parallel development

Agree on interfaces and file ownership before concurrent implementation. Use separate branches/worktrees where available. Coordinate shared schema, dependency, migration, and configuration changes through the integration owner.

Every handoff states changed files, launch instructions, verification performed, known gaps, and required configuration.

## Source for Codex instruction discovery

Repository instructions follow the official OpenAI documentation:
https://learn.chatgpt.com/docs/agent-configuration/agents-md
