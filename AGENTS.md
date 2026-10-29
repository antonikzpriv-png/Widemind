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

The app now provides a Python/SQLite business/economy/runtime implementation, real Gmail/OpenAI/Stripe client code, an authored public/private UI and an explicit offline demonstration. Read [docs/OPERATIONS.md](docs/OPERATIONS.md), [docs/COMPLETION_STATUS.md](docs/COMPLETION_STATUS.md), [docs/BUSINESS.md](docs/BUSINESS.md) and `widemind/business/contracts.py` before integrating economy, tools, runtime, or website work. Do not invent live integrations, deployment URLs, financial outcomes, or completed milestones.

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

Run `python -m unittest discover -s tests -v` and `python -m widemind demo --directory /tmp/widemind-business-demo`. For UI changes also run `node --check widemind/web/static/app.js` and `python tests/ui_browser.py` with optional Playwright installed. `tests/ui_integrated_browser.py` qualifies a running explicit demo only. Runtime Python dependencies are standard library only; packaging uses `pyproject.toml`. Keep fixtures restricted to explicit demo/test modes. Run the demonstration twice against the same directory when changing recovery or idempotency.

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
