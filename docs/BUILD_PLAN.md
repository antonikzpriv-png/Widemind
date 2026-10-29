# WideMind completion plan

Target window: 2026-10-29 16:47 to 2026-10-30 04:47 Europe/Warsaw (12 hours).

The owner requested all four outstanding items: visual UI, live Gmail/tools/economy/payments integration, real-world validation, and a pull request. Stripe and OpenAI were selected alongside dedicated Gmail. This target is not evidence that external account provisioning or live verification has happened.

## Parallel ownership

| Workstream | Owner | Files | Acceptance |
| --- | --- | --- | --- |
| Public headquarters + operator UI | UI subagent | `widemind/web/static`, UI docs/tests | Responsive WideClarity monochrome styling; real API data; private business views and recovery; demo/live labels; no private data in public pages |
| Economy + Stripe | Economy subagent | `widemind/economy`, economy tests/docs | Exact append-only accounting, atomic reservations, $5 tagged opening credit, verified payments/refunds/fees, durable Stripe effects, no invented revenue |
| Gmail + research tools | Tools subagent | `widemind/tools`, tool tests/docs | Real OAuth/API clients, bounded OpenAI research with source evidence, gateway authorization and cost posting, uncertain delivery reconciliation |
| Runtime + production host | Runtime subagent | `widemind/runtime.py`, `host.py`, runtime tests/docs, env example | Durable pause/stop/resume, capability enforcement, fail-closed host configuration and isolated accounting/control storage |
| Integration + qualification + PR | Primary agent | API/CLI, read models, shared contracts, plan, integrated tests/CI | Packaged runnable UI/demo, production wiring, privacy/recovery tests, recorded live qualification evidence, push and PR |

Each editing agent uses its own branch/worktree. Interface changes are agreed before integration. The primary agent reviews and integrates commits; agents do not independently publish or merge.

## Sequence

1. Freeze interfaces and establish the parallel build (hours 0–3). Serve an authored UI with WideClarity's black surfaces, muted gray, fine borders, pill controls and restrained typography. Keep WideMind branding. Add normalized operator/public read models and safe static routing.
2. Integrate real providers and enforcement (hours 3–6). Connect Stripe, dedicated Gmail OAuth and bounded OpenAI work through the existing business protocols. Accounting and runtime control remain outside the editable agent workspace. Preserve independent worker operation and zero paid inference while idle.
3. Exercise integrated flows and audit (hours 6–9). Run the complete explicit demo through the website and worker, test authorization/private data boundaries, concurrency/exhaustion, unknown effects, payment/refund changes, accessibility/mobile UI, packaging and restart recovery. Run a controlled provider qualification only with configured accounts/credentials and allowed network access.
4. Resolve findings and publish review (hours 9–12). Re-run relevant checks after fixes, record exactly which live/sandbox actions occurred, push a clean reviewed branch, open and attach the PR. Do not mark a blocked external milestone as complete.

## Required external setup

- Dedicated Gmail account, OAuth client/refresh token with correct read/send scopes, verified sender and controlled qualification recipient.
- Stripe test/live mode selection, API and webhook signing secrets, configured account/checkout terms and verified settlement/refund evidence.
- OpenAI project API key, configured model/pricing and enforceable input/output/time/cost limits.
- Private storage paths, operator authentication, runtime autonomy profile, deployment isolation, and allowed proxy destinations (`api.github.com`, Google OAuth/Gmail API, Stripe API and OpenAI API; approved research source hosts).

Credentials belong in secret configuration, never messages or Git. Current cloud observations at planning time contain no configured secret bindings, and GitHub API access returns HTTP 403. Production qualification and PR publication remain incomplete until their actual prerequisites work. Tests/mocks and the explicit demo never count as an independent paying customer or a profitable live run.

## Completion evidence

Record commands, test results, provider mode, action references, material limitations, and actual PR URL. Financial displays distinguish receipts, earned revenue, costs, holds and spendable credit. Unknown or unconfigured values stay unavailable. No stock customers, fabricated transactions or success claims appear in live UI.
