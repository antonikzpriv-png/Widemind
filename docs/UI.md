# WideMind website and operator console

The authored static UI uses WideClarity's monochrome visual language: black surfaces, restrained gray text, fine borders, pill navigation, and compact operational records. WideMind has its own wordmark treatment. No third-party font binaries, WideClarity branding, build framework, CDN, or browser dependency is required at runtime.

The public headquarters is `/`; the operator shell is `/console`. Assets are `/static/app.js` and `/static/style.css`. Root-host integration serves these assets and implements the read projections and existing business commands. The website does not run the worker or invent provider readiness.

## Public page

The page shows configured service offerings, a dedicated-account email contact link, selected redacted activity, and available financial summaries. A missing balance is **Unavailable**, not zero. Receipts, earned revenue, and operating charges are separate. No private account, customer, job, message, or artifact record is requested by this page.

A contact request opens a structured request composer. It validates topic, sections, minimum sources, and optional source URLs for research, then prepares an email draft containing the normalized Gmail command JSON. Other registered services can supply a JSON scope. The visitor explicitly opens and sends the draft from their own email account to the configured contact identity. The site does not accept an unauthenticated command that can send arbitrary mail or invoke a paid tool. If no contact identity is configured, the page says so instead of inventing an address.

## Private console

The console includes:

- Overview: actual commitments, pipeline stages, financial records, and unresolved effects.
- Opportunities: search/filter, source and assessment, frozen quote terms, scoped intake, and recording actual customer acceptance.
- Orders: payment and delivery status, deadlines, scope, private artifact inspection/download, scheduled wakeups, and explicit payment-hold recovery.
- Finances: economy-reported balances/funding/holds, receipts versus earned revenue, costs, refunds, and per-order obligations.
- Activity & recovery: durable event states and explicit reconciliation/retry requests.
- Settings: operator policy, goals, enabled capabilities, prices, prioritization, budgets, and reported provider readiness.

The bearer token exists in the JavaScript module's memory only. It is cleared from the input immediately and never written to browser storage or URLs. Locking the console clears the token, pending requests, private view/dialog content, and run identifiers. A late response from an old authentication session cannot restore private state. A late order response cannot replace a newer dialog.

Customer-supplied strings and artifacts are rendered with DOM text nodes, never inserted as HTML. A deliverable download uses the authenticated order response and a short-lived local Blob URL. An operator must use the configured private token; it is not a customer portal credential.

There is no timer that searches for work, performs a paid action, or retries a failed effect. Refresh reads the persisted projection. Explicit commands are posted to the existing server contracts, where runtime state, capabilities, identity, payment, and budget remain authoritative.

The execution banner distinguishes offline demo fixtures from **provider-test**, where external Gmail/research calls can be real while processor records remain test transactions. An enabled-looking control or service is never evidence that a live provider was qualified.

## Verification

`node --check widemind/web/static/app.js` validates JavaScript syntax.

The explicit test-mode browser harness uses a local HTTP fixture server and no provider calls:

```sh
python tests/ui_browser.py
```

It requires Playwright and a Chromium executable (`chromium` on PATH or Playwright's installed browser). It checks structured public email requests, desktop/tablet/mobile widths including 320px, all six sections, authentication failure/success, no credential persistence, XSS-safe rendering, intake/source URLs, private downloads, runtime commands, reconciliation, stale-dialog protection, lock cleanup, provider-test labeling, and public dependency failure. It is separate from the standard-library backend unit suite. Tests and fixture customers are not live business evidence.

The static UI needs the packaged asset serving, API read projections, and configured business clients supplied by the main application. Deployment still requires TLS, proper operator access control, private data storage, and provider setup; the frontend does not substitute for them.

## Integrated offline browser qualification

`tests/ui_integrated_browser.py` additionally runs against the actual packaged website/API and independently running demo worker. It does not replace API responses with route fixtures. It refuses live/provider-test targets and requires both a loopback URL and explicit mutation permission. It reads an owner-only token file without printing the credential.

```sh
python tests/ui_integrated_browser.py --base-url http://127.0.0.1:8091 \
  --token-file /private/demo/operator-token --allow-offline-mutations
```

Run it against a freshly seeded offline `serve-demo` instance. It creates labelled fixture intake/acceptance records, verifies the worker waits for payment rather than inventing it, tests controls and policy persistence, and restores the original policy and active state. These mutations are intentionally confined to the selected explicit demo instance.
