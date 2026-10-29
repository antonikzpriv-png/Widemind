# Economy audit and extension qualification

This historical economy-only report predates the runtime integration. See the [subsequent merged-main audit](MAIN_AUDIT_20261029.md) for current integration status and qualification; branch/main status below describes this earlier audit only.

Audited base: `3110aad2f58a1db1224907fc2a7f45b8143615c3`. Final implementation qualification: `78694d585b2a97b41a451bac078022be853d5bb5`, economy package **0.2.0**, SQLite schema **4**. This report covers the economy/payment module, not the unmerged autonomous-runtime or business branches.

## Confirmed defects fixed

- Paid adapters were keyed only by provider/account, permitting a test-funded run to select a live adapter. Registration and execution now require an explicit matching mode, and changed profiles fail before dispatch.
- A cash correction to an unresolved provisional charge could duplicate the later authoritative true-up. Corrections to active cost roots and their correction chains are rejected until final reconciliation; legitimate final corrections remain append-only.
- Oversized or malformed optional usage diagnostics could suppress a known real charge and leave spendable credit overstated. The gateway now preserves the authoritative charge, including negative overages, with a bounded explicit diagnostics-omission record.
- Concurrent first-time SQLite initialization could intermittently return a lock error. Initialization now retries only bounded BUSY/LOCKED failures, rechecks schema under the migration lock and rolls back failed upgrades atomically.
- HTTP identifiers could contain an unaddressable slash. Routes/configuration now reject those IDs. Authentication rejects ambiguous duplicate credentials and runs before private request-body parsing.
- Hosted checkout response URLs received stricter validation. Provider operations now share nested time/request budgets, bounded streamed bodies and disabled redirects.
- Review of the new queue caught a failed-payment scheduling starvation case; eligible due payments are filtered before batch limiting, and dead letters cannot silently reset their own attempt budgets or block unrelated payments.
- Review of the new integrity reporter caught incomplete authorization/projection checks. Reports now compare reservation fields with original authorization events and payment release/exposure/revision projections with scoped fulfillment and reconciliation history. Typed corruption remains detectable after a normal provider refresh. Database guards also reject future identity/type corruption; historical inconsistencies are reported rather than rewritten.

## Extensions

- Durable signed-webhook admission returns **HTTP 202** before provider reads. Minimal event descriptors, account/mode-scoped deduplication, immutable queue inputs, fenced leases, backoff, finite retries, dead letters and audited operator recovery survive restarts. Accounting remains idempotent if a process dies after posting money but before completing its task.
- A separate `widemind-economy worker` command periodically reconciles known payments without a model or website. The synchronous operator reconciliation endpoint remains available. Shared isolated-service deployment examples and backup/upgrade instructions are included.
- `recover-checkout` safely locates an existing Stripe session for an uncertain durable request, including completed sessions with no URL. It never creates a replacement checkout or grants cash.
- Redacted financial summaries and bounded read-only local integrity reports expose explicit verification limits. Their HTTP endpoints are operator-only; this does not publish a website or a public unauthenticated financial feed.

## Executed verification

- Baseline: **185 tests passed**, no failures/skips.
- Final full suite: **371 passed, 0 failed, 0 skipped**, 8.14 seconds, Python 3.12.14. One existing Starlette HTTPX TestClient deprecation warning remains; no tests/warnings were suppressed.
- Real independent-process financial seed/reservation races and a worker-process exit followed by lease recovery. Additional qualification ran **20 groups × 6 concurrent initializers (120 processes)** without initialization failures.
- Actual SQLite/FastAPI/CLI integration, additive version-1 upgrade preservation, deliberate migration-failure rollback, durable webhook/worker/accounting replay, authorization and field-boundary tests.
- Explicit Stripe HTTPX fixtures for complete histories, fees, refunds, disputes, pending components, duplicate/out-of-order events, uncertain-checkout recovery, signature/account/mode checks, and provider time/request/body limits. **No real sandbox or live payment occurred.**
- Independent review reverified the previously failing financial and forged-release reproductions, then passed 72 queue/reporting checks and 15 independent regression checks. No additional material defect was identified in the reviewed paths.
- Built the 0.2.0 wheel, installed it outside the source directory in a separate environment and verified all four migrations, schema 4, exact opening credit, authenticated financial/integrity reads, queue access and the worker CLI. `pip check` passed. Wheel SHA256: `bbd2a526f42b5325789421b3d79d199a56d4b0a56d63bf4fbb9c27343b46eb24` (local qualification artifact).
- Source compilation and `git diff --check` passed. Remote CI execution is not claimed.

## Operational limits and remaining setup

Back up and upgrade both trusted processes together. HTTP 202 is queue acknowledgement, not settlement: the independent worker must run. USD-only receipt rules remain; refunds are initiated through Stripe and reconciled here. The accounting database/code/credentials must remain inaccessible to the agent OS identity; the included service units are examples, not a verified live deployment.

Real sandbox acceptance still needs dedicated Stripe account credentials, endpoint signing secret, correct return URLs, reachable `api.stripe.com`, webhook ingress/CLI forwarding and an operator-created actual test payment. Live acceptance additionally requires the owner's business/provider onboarding and deployment. Provider fixtures do not satisfy those requirements.

The separately developed TypeScript runtime foundation is not merged into this branch or main. Its module loader and canonical decimal-string money contracts still need a coordinator-owned bridge to the Python economy HTTP contract before claiming end-to-end runtime integration. No other agent's branch, runtime or website was rebuilt or merged by this audit.

See [operations](ECONOMY_OPERATIONS.md), [setup](ECONOMY_SETUP.md), [checkout recovery](ECONOMY_CHECKOUT_RECOVERY.md), [reporting](ECONOMY_REPORTING.md) and the [integration contract](ECONOMY_CONTRACT.md).
