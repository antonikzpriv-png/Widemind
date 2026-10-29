# WideMind build status and evidence

This report separates implemented code, local validation, and external live qualification. It never treats fixtures as a customer, a settlement, or earned money.

| Requested point | Implemented code | Qualification status |
| --- | --- | --- |
| Visual UI | Public headquarters and authenticated six-view console; WideClarity monochrome visual style; structured customer request composer; private artifact downloads and recovery controls | Browser fixture tests and actual API/independent-worker checks passed; responsive 320–1440px, no private data in public projection, memory-only bearer authentication |
| Live integrations | Real dedicated Gmail OAuth/API, grounded bounded OpenAI research, exact-money protected ledger, Stripe Checkout/signed webhooks/refunds, durable runtime controls and real host composition | Offline tests use actual provider classes with explicit HTTP transport fixtures. No credentials or live provider calls were used in those tests. Real accounts and access remain required |
| Real-world validation | Reproducible offline demo, actual-adapter end-to-end request/payment/creation/acknowledgment/refund test, local browser and package checks, operator qualification runbook | Actual Gmail delivery, actual model charge, verified Stripe sandbox/live events and an independent paying customer have **not** been demonstrated. They cannot be manufactured by the program |
| PR delivery | A review branch contains the implementation and checks; local commit history identifies each subagent workstream and audit correction | PR creation requires working `api.github.com` access. The cloud policy has returned HTTP 403; Git push works. No fabricated PR URL or merge is claimed |

## Local reproduction

```sh
python -m unittest discover -s tests -v
python -m compileall -q widemind tests
node --check widemind/web/static/app.js
python -m widemind demo --directory /tmp/widemind-batch
python -m widemind demo --directory /tmp/widemind-batch
```

For authored browser checks:

```sh
python -m pip install '.[dev]'
python -m playwright install chromium
python tests/ui_browser.py
```

For a real API/worker offline browser qualification, start a fresh explicit demo in one terminal:

```sh
python -m widemind serve-demo --directory /tmp/widemind-ui-demo --seed --token-file /tmp/widemind-ui-operator-token --port 8080
```

In another terminal:

```sh
python tests/ui_integrated_browser.py --base-url http://127.0.0.1:8080 --token-file /tmp/widemind-ui-operator-token --allow-offline-mutations
```

The integrated test refuses provider-test/live targets, reads the owner-only token file without logging it, mutates only labelled demo work, restores the policy, and never invents payments for added requests. The UI fixture suite has 56 checks and integrated suite has 63 checks. **166 backend tests pass**. Python compilation, JavaScript syntax, wheel build/installation, static resource serving outside the checkout, and repeatable packaged demos also pass. Remote CI/Windows and actual live providers are not claimed as qualified.

The full provider workflow test uses the **actual** `Business`, `EconomyService`, `RuntimeControl`, `LiveTools`, `GmailClient`, `OpenAIResearch`, and `StripeClient` classes. Its network boundary is an explicitly offline fixture. It verifies authenticated request → accepted offer → acceptance → payment link → signed Stripe event + verified read → one model request and usage charge → persisted deliverable → verified recipient acknowledgment → earned revenue → duplicate-event immunity → post-delivery refund retaining fees and cost. This is contract/integration evidence, not live-service qualification.

## Audit corrections in this build

Independent review and regression tests fixed reservation run/job ownership, immutable per-execution ceilings, late refund/breach checks immediately before paid dispatch, concurrent recovery versus active execution, stale business effect writes, complete provider input/schema budgeting, cached token pricing, billed error usage, whole-operation HTTP deadlines, mode-pinned Stripe credentials, bounded payment reconciliation, cross-cycle inbox pagination, safe Gmail preflight recovery, deterministic reseeding with multiple customer orders, authenticated receipt correlation, accepted transactional notice progression, provider-test webhooks, and stale financial displays.

The public UI composes a structured email draft that the customer explicitly sends through their own client. It does not expose an unauthenticated arbitrary-mail tool route. Only the private console can see customer identities, messages, scopes or artifacts. Wallet figures become unavailable if the authoritative ledger cannot be read; old cached earnings are not presented as current.

## What the owner still needs to configure

1. **Network:** review/publish the saved cloud environment draft containing GitHub, Google OAuth/Gmail, Stripe, OpenAI and approved research source hosts. Saving alone does not apply the policy; restart if the cloud UI requires it.
2. **Dedicated Gmail:** create/choose the WideMind mailbox; configure its address plus `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET`, `GMAIL_REFRESH_TOKEN`; grant the documented minimum scopes and a controlled qualification recipient allowlist.
3. **Stripe:** configure a test-mode key and webhook signing secret first, plus valid HTTPS success/cancel URLs and a webhook receiver. Key prefixes, event mode and ledger run mode must agree. Live payment activation is a separate explicit mode/run, never conversion of demo money.
4. **OpenAI:** configure the project API key, supported model, explicit ordinary/cached/output prices and bounded input/output limits. No implicit model-price guesses are used.
5. **Host and control plane:** configure absolute protected paths, operator token and separate agent workspace. Run `check-config`, explicit `initialize --evidence-reference ...`, and independent API/worker services. Initialization starts paused with a separately labelled owner opening grant, not customer revenue.

All names/examples are in `.env.example`; actual secrets belong in deployment secret configuration, not Git or chat. See [OPERATIONS.md](OPERATIONS.md), [LIVE_ECONOMY.md](LIVE_ECONOMY.md), and [LIVE_TOOLS.md](LIVE_TOOLS.md). Path checks are not OS isolation: deployment accounts, filesystem permissions, backups, TLS, webhook routing and provider permissions must be set up and verified.

While waiting, no paid reasoning/search loop runs. Incoming mail can use externally scheduled `sync-inbox --request-id ...` or provider push notifications, and verified payment checks use finite `sync-payments --request-id ...` batches. A worker can reconcile financial state while stopped without resuming fulfillment. Unknown external actions are inspected, never blindly replayed.

Automatic new paid rework after a failed billable attempt is deliberately not offered through the generic retry button: it needs a new approved plan and fresh reservation. Source factuality/rights and deliverable quality require the configured service's qualification; citations alone are not proof of truth or a profitable business.
