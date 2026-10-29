# Business website and operator console

The Node foundation serves a responsive public headquarters, the integrated customer portal at `/commerce`, existing connected-operations controls at `/operator/integrations`, and an optional private brief-business console. The visual treatment uses WideClarity's monochrome palette, restrained type, pill navigation, black surfaces, and hairline borders. The UI assets are newly authored; no WideClarity logos, proprietary font files, external scripts, or CDN dependencies are copied.

## Open the interface

Use the Node 24 setup and operator authentication in [FOUNDATION.md](FOUNDATION.md). Enable the optional brief-business adapter with `WIDEMIND_BUSINESS_ENABLED=true` in the same environment for both web and worker. Keep `WIDEMIND_PROFILE=foundation`; this optional adapter does not replace the explicit zero-cost foundation demo or the upstream service-backed business/customer-portal implementation. Select one business provider and matching economy/tool credential protocol per deployment. Do not enable the optional brief-business provider alongside `WIDEMIND_BUSINESS_URL` for the service-backed provider. See [INTEGRATED_SETUP.md](INTEGRATED_SETUP.md) for the preserved service-backed PayPal flow.

Run the web and worker as independent processes:

```sh
npm run build
npm start
npm run worker
```

The public page is `/`. Sign in at `/operator`, create a paused runtime run, and use its **business console** link. The private page is `/operator/modules/brief-business/console?runId=<runtime-run-id>`. Registered module pages appear in the existing Run control screen; pause/resume/stop/wake remain the foundation's controls.

The six console views are:

- **Overview:** global opportunity/commitment counts, observed financial availability, delivery obligations, and integration readiness.
- **Opportunities:** durable leads and assessments, unquoted reassessment, new scoped intake, and customer-accepted quote records.
- **Orders:** agreed scope/prices, last observed payment status, original deadlines, open obligations, hold resolution, and authenticated private deliverable detail.
- **Finances:** the authoritative economy summary, with receipts, earned revenue, costs, reservations, customer/refund holds, funding, refunds, and accounting mode kept distinct.
- **Recovery:** unresolved runtime action references, pending events, mailbox-sync requests, and a link to the existing runtime reconciliation controls.
- **Settings:** operator business policy, integration status, and customer opt-out commands.

The optional console uses server-rendered HTML forms and links and includes no application JavaScript. The shared host retains the integrated customer portal's same-origin `script-src` and `connect-src` CSP for `/commerce.js`; inline scripts and off-origin scripts remain blocked. The optional console does not add scripts or weaken that shared policy. Operator access continues to use the existing HttpOnly, SameSite session cookie, exact configured Origin checks, and per-session CSRF token. Private data and tokens are never placed in browser storage. HTML forms use stable per-render request IDs so a duplicate submission remains the same business command.

## Queued commands and truthful state

Submitting a form records a command in the persistent inbox. The independent worker processes it; a paused or stopped run retains the command until explicitly resumed. A form submission is not proof of a sent message, received payment, completed artifact, or saved business transition. Refresh to observe durable results. The UI does not run a paid planning loop or remote mailbox poll while open.

Counts cover the entire selected run. Large collections use 500-record pages with visible totals and previous/next links. Customer/quote detail follows the selected page; private order artifacts are loaded only for an explicitly selected order in that run.

Missing finance remains **Unavailable**, rather than an invented $5 balance or zero revenue. Financial totals use exact decimal-string micro-USD formatting, including sub-cent usage. Order payment details explicitly identify their cached observation time; they are not displayed as a fresh processor check. Sandbox mode is labelled as test funds.

The public renderer receives only the curated public projection. It does not serialize private customer records, inbox messages, artifacts, provider configuration, or operational logs. If the dedicated contact address is configured, **Discuss a project** opens the visitor's email client; the visitor must explicitly send the message. The website does not send arbitrary mail on a public form submission.

The public home page now shares the [activity constellation](THINKING_NETWORK.md) with the default headquarters. Its same-origin script visualizes redacted runtime activity and costs. The private brief-business console remains server-rendered without application scripts.

## Verification

```sh
node --test test/business-ui.test.ts test/module-ui.test.ts test/web.test.ts
npm run build
node scripts/business-browser-smoke.mjs
```

The browser check uses Chromium and a disposable database with an explicitly synthetic intake request, **no provider credentials, no money and no mail**. It drives actual login/create/intake/resume HTML forms, runs a separate background worker, checks all six views at 1280, 390 and 320 pixels, verifies that the selected view remains visible on mobile, checks public/private separation and confirms the optional console has no application scripts or page errors, while retaining the customer portal's same-origin CSP support. Reports/screenshots are local ignored files under `var/business-browser-smoke/`.

This UI check is not evidence of live Gmail, paid research, customer payment, delivery, or profit. Those depend on the configured tools/economy providers and controlled live qualification.
