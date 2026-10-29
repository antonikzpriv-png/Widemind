# WideMind implementation audit

Audited baseline: `332d0cecc4fc1e4f11558079c84d45883af48eda`. This pass reviews the integrated application and its boundaries with the retained standalone tools package. It includes code inspection, fault injection, regression tests, actual local services/worker, desktop/phone browser interaction and dependency advisories. It does not establish live provider or deployment readiness.

## Findings corrected

| Finding | Impact | Correction and verification |
| --- | --- | --- |
| Business bridge crash could block the runtime indefinitely | High: a saved running invocation and still-durable core event returned unknown forever, preventing later work | Reconcile the zero-cost handoff from its exact core event. Preserve queued/running leases and unknown inner effects, with `processed=false` and `recoveredOrchestration=true`. Missing invocations provide same-ID not-performed evidence. Tests cover before-dispatch, running and already-terminal crash windows without executing business work inside reconciliation. |
| PayPal capture/refund events lost their mappings in the queue | High: the queue intentionally retained only resource IDs, but processing expected stripped supplementary fields/links | Retrieve capture/refund/parent identities with authenticated provider GETs. Fresh reads determine amounts and ownership. Both event types now pass through verified ingress, minimal durable queue, worker and idempotent ledger; private webhook fields stay out of storage. |
| Tool reconciliation did not bind the full saved request | Medium: an internal authorized caller could request a cached receipt using changed inputs or a different run's reservation with the same action ID | Validate the complete original action/reservation fingerprint before returning a cached receipt or reconciling an adapter. Regression covers changed inputs and cross-run cached results; correct replays still work. |
| Headquarters had no registered website provider | Medium: commerce worked, while the headquarters showed no real offerings or curated completions | Register the business-backed website projection. Publish available services and enum-controlled completion text, with no customer scope/contact/artifact data. Actual browser and public API checks verify the wiring. |
| New unrelated runs replaced public financial totals | Medium: a newly created unfunded run could hide the configured commerce balance | Bind financial publication to `WIDEMIND_COMMERCE_RUN_ID`, expose additive `financialRunId`, and fail unavailable if that configured run is absent. Unit and full-stack checks create a second run and retain the correct totals. |
| Business response size was checked after buffering | Medium: a service could stream an oversized response and consume memory before rejection | Enforce the byte limit while reading and cancel the stream immediately. The regression deliberately leaves an oversized upstream response open and verifies rejection/cancellation. |
| Capability requests ran inside a business write transaction | Medium: slow/unavailable tools could hold the SQLite writer while scanning opportunities | Read capabilities outside the transaction, once per host request, including outage caching. Revalidate policy and opportunity inputs before commit. Tests verify unlocked I/O, one capability read, outage behavior and rejection of stale quotes after a concurrent policy change. |
| Pause during portal publication blocked resume | Medium: an in-progress offer could become permanently blocked when publication observed Pause | Preserve the event as queued when runtime control interrupts select/offer/advance. Resume reuses the durable work and existing effect identities. A regression pauses at publication and verifies the quote is offered after Resume. |
| Package constraints selected vulnerable versions | Dependency scan found advisories in old pip, cryptography/OpenSSL wheels and optional Pillow | Bootstrap pip 26.2.2 and require cryptography 50.x / optional Pillow 13.x in both tools manifests. Updated standalone CI/bootstrap guidance; reran service/standalone tests and advisory scans. No advisory exceptions were added. |

The handoff fix confirms only orchestration ownership. It does not claim customer fulfillment, provider success, final paid usage or release of an uncertain inner reservation. Business core receipts, leases, effects, customer orders and accounting retain their separate authoritative states.

## Verification

| Check | Result |
| --- | --- |
| `npm run check` | Formatting, type checks, 64 Node tests and compiled build passed |
| `npm run services:setup -- --with-tests` | Separate environments installed with patched installer/crypto requirements |
| Economy suite | 382 passed, including 12 PayPal integration cases |
| Tools suite | 360 passed; 11 optional-runtime skips; 75 subtests passed |
| Business suite | 75 passed, including 19 host integration cases |
| Standalone tools suite after dependency updates | 348 passed; 9 optional sandbox skips; 75 subtests passed |
| `npm run smoke` | Compiled web/worker, auth, controls, durable receipt and event wakeup passed |
| `npm run browser:smoke` | Desktop/phone login, create/resume/pause, event form and worker diagnostics passed |
| `npm run integration:browser` | Real local services and persistent worker; customer request/accept/download, operator policy/error controls, public headquarters, hard worker kill/restart and queued external refund passed |
| Dependency checks | `npm audit`: no reported advisories. `pip check`: no incompatible requirements. Python advisory scans use actual isolated environments; see machine-readable evidence for scanned/skipped package details. |

The integration gate creates an actual local SVG and downloads it through the customer interface. A wrong token cannot read that delivery. The worker is killed with an accepted commitment outstanding, then restarted against durable state; checkout/capture/creation are not duplicated. Invalid signatures are rejected. Replayed approval/refund events produce one capture and one refund. A second unrelated runtime run cannot replace the published commerce balance.

All external PayPal responses are explicitly local protocol fixtures. Fixture accounting reconciles $5 opening + $2 receipt − $0.10 fee = $6.90 before refund, then $4.90 after the externally initiated $2 refund. Gross earned performance is separate from refunded cash; this is not actual revenue or profit. The local services, worker, databases, SVG, portal and browser download are real application behavior.

Screenshots precede the final external refund; the machine-readable result retains both balances.

Synthetic [machine-readable evidence](evidence/implementation-audit-20261029/verification.json) records the validated source SHA, results and screenshot hashes. Screenshots contain only disposable fixture data: [headquarters](evidence/implementation-audit-20261029/headquarters-phone.png), [customer delivery](evidence/implementation-audit-20261029/customer-delivery-phone.png) and [operator integrations](evidence/implementation-audit-20261029/integrations-phone.png).

## Remaining boundaries

- No live PayPal, Gmail or OpenAI credentials, verified live opening funds, HTTPS host/domain or independent paying customer were available. This is not a live survival pilot or a claim of complete autonomous commercial viability.
- PayPal capture/refund reconciliation is implemented. Automatic PayPal dispute/chargeback ingestion is unavailable; unsupported verified event types remain unresolved and require operator review. Private economy status exposes reconciliation task failures. The ledger must never invent an adjustment from an unsupported event.
- Optional external browser/code capabilities still require their documented OS sandbox, browser/runtime and pinned Docker image. They fail unavailable when missing; tests do not substitute host execution for those prerequisites.
- Paid model tariffs can be provisional. Uncertain/final invoice reconciliation remains necessary; unknown liability is retained rather than assumed zero. Deployment must isolate trusted control-plane state/secrets and disclose hosting/shared-cost allocation.
- Capability caching lasts one business HTTP request, and the tool gateway rechecks actual authorization/configuration before dispatch. Payment observation is bounded to recent commitments as documented; older effects may require authenticated hints or operator recovery.
- Advisory scans report known published advisories for installed package versions at audit time. They do not certify unpublished application code or future package releases. One inherited FastAPI test-client deprecation warning remains.

Changes remain on the existing feature branch. No main-branch merge is performed. GitHub API PR creation was previously blocked by the managed network policy; pushing this branch does not imply a PR exists.
