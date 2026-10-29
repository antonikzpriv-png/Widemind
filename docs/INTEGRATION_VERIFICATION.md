# Integrated implementation and verification

Verified locally on 2026-10-29 with Node 24.19.0 and Python 3.12.14. Application source is `6d34f617d9c0d60ef1302fd491ee08671be295d4`, which includes main `1ac5f08` and preserves its standalone tools package. The subsequent evidence commit contains documentation and synthetic test captures only.

## Implemented behavior

- Independent persistent web/runtime processes, SQLite migrations, server-side operator sessions/CSRF, scheduled wakeups, leases, durable action IDs/outbox, cancellation and restart recovery.
- Registered economy/tools/business/website contracts and isolated authenticated service wiring. The business orchestration step is zero-billed; inner paid/tool operations still require their own authoritative reservations and measured receipts.
- Separate append-only economy, explicit sandbox opening, reservation/start/settlement, provisional liabilities, verified PayPal checkout/capture/webhooks/refunds, fulfillment holds and exact public financial projections.
- Actual local workspace files, SVG graphics and static software; configured network/model/Gmail/browser/Docker/publishing adapters report unavailable until their prerequisites are met.
- Public customer request, scoped private token, quote acceptance, PayPal checkout and private artifact download. Customer messages display readable terms and work details. Completed orders no longer offer another payment link.
- Private operator service diagnostics, policy/opportunity controls and observational reconciliation. Public projections omit receipts, private artifacts, credentials and customer records.
- Free idle scheduling and payment observation. No paid planning loop runs while idle. Verified webhooks enqueue durable work; a separate model-free economy worker is required and the launcher starts it when configured.

## Verified commands

| Command | Observed result |
| --- | --- |
| `npm ci` | Installed pinned Node development dependencies |
| `npm run services:setup -- --with-tests` | Installed economy, tools and business in distinct virtual environments |
| `npm run services:configure`, `npm run migrate`, `npm run pilot:init -- --sandbox` | Private configuration/migrations and explicit test opening; no live grant |
| `npm run check` | Format/type checks, 62 Node tests and compiled build passed |
| `npm run services:test` | Economy: 380 passed; tools: 359 passed, 11 skipped and 75 subtests passed; business: 66 passed |
| `npm run smoke` | Compiled independent web/worker, login, controls, receipt durability, worker health and durable-event wakeup passed |
| `npm run browser:smoke` | Desktop/phone login, create/resume/pause, operator event form and unavailable worker presentation passed; no page exceptions |
| `npm run integration:smoke` | Actual services/worker with local PayPal protocol fixture passed end to end |
| `npm run integration:browser` | Actual customer request, quote acceptance, checkout link, artifact download and private operator diagnostics passed on desktop/phone; no page exceptions |
| `.venvs/standalone/bin/python -m pytest -q -rs` | Latest main's standalone tools: 348 passed, 9 skipped and 75 subtests passed after separate `.[test,graphics,browser]` installation |

Node coverage includes exhausted budgets, pause/stop/restart/leases, unknown outcomes, provisional-receipt recovery, refreshed recovery capabilities, dependency timeouts, authentication, private/public separation and old durable records. Service coverage includes concurrent admission, replayed/out-of-order events, billable failures, charge/refund liabilities, token ownership/revocation, unknown effects, exact usage schema and credential/redirect isolation.

Tool skips are explicit missing optional image/browser runtime or pinned Docker sandbox prerequisites. The standalone browser adapter refuses to fall back to host execution without its OS sandbox. Headless browser tests for our own trusted website use a disposable CDP session; this does not prove the external browser tool's sandbox is available. One inherited FastAPI test-client deprecation warning remains.

## Full local acceptance result

The persistent worker handles a controlled request, publishes a quote, observes acceptance and creates checkout. An invalid webhook signature is rejected; the verified approved-order event is delivered twice. The economy reconciliation worker captures and verifies one provider transaction. The business module observes the actual local economy change, reserves tool execution, generates a real SVG, publishes it privately and records earned revenue/hold release. A different token cannot download that delivery. Refreshing the completed request produces no additional checkout or capture.

Only the **external PayPal API** is a fixture. Services, databases, reservation enforcement, worker scheduling, SVG creation, portal publication and the browser download are real local application behavior. Fixture amounts reconcile exactly: $5 sandbox opening + $2 fixture customer receipt − $0.10 fixture fee = $6.90 posted/spendable; no reservations/holds remain. The $2 earned metric is separate from cash. These are test amounts, not actual revenue, payment or profit.

Synthetic evidence: [verification.json](evidence/integrated-20261029/verification.json), [customer phone](evidence/integrated-20261029/customer-delivery-phone.png), and [operator phone](evidence/integrated-20261029/integrations-phone.png). These use disposable identifiers and an `example.invalid` address, with no real credentials or customer data.

## External prerequisites and limits

PayPal Business is the new selected provider. Existing Stripe support/configuration and main's standalone tools work remain available explicitly; they are not silently reused as PayPal accounts or the integrated ledger. OpenAI and dedicated Gmail are the selected model/communication targets.

No provider credentials/accounts, verified live $5 funding, HTTPS host/domain, independent paying customer or profitable survival run were provisioned. The managed environment restricts outbound provider hosts. Optional paid adapters require owner-certified limits/tariffs and dedicated account scopes. Provisional charges remain held until authoritative cost reconciliation; missing data is not treated as zero cost. Production task isolation and hosting/shared-cost allocation remain deployment obligations. Portal publication is actual local delivery, not a claimed Gmail send or proof the customer read it.

Payment observation scans recent commitments; older or unusual provider effects can require authenticated hints/operator recovery. Unknown irreversible effects require definitive reconciliation rather than automatic repeat. A passing bounded local workflow does not establish open-ended commercial viability.

The feature branch is pushed. PR creation was attempted with `gh pr create`, but this environment's request to `https://api.github.com/graphql` returned `Forbidden`. No PR or main-branch merge is claimed. Use the pushed-branch comparison link when API access is available.
