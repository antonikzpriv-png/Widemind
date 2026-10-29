# Main economy and runtime audit — 29 October 2026

Audited remote main `447e02bf701e3995b2f8161e74b193e27b975385` in an isolated worktree. Final implementation: `43f5bb3cb01490668c1edae3f3e1b01fc4a62790`. Subsequent report-only edits do not change the qualified implementation. The audit covers the economy ledger/payment service, registered runtime bridge, durable execution/recovery, public projections, SDK accounting boundary, configuration, packaging and deployment instructions. Independent reviewers examined separate accounting, payment, runtime, API and configuration paths; reproduced defects were fixed and retested.

## Reproduced findings and repairs

| Finding | Reproduction and impact | Repair and maintained regression |
| --- | --- | --- |
| P2: optional configuration prevented startup | Copy the shared environment template, populate the required role/run values, leave optional runtime profiles blank. JSON parsing aborted the accounting-only service. | Missing/blank/whitespace profiles mean an empty map; nonempty malformed/oversized profiles and configured profiles without a signing key still fail. `tests/test_audit_configuration.py`. |
| P2: invalid accounting authority selected a local wallet | An explicit SDK owner policy such as `accounting_authority: "CENTRAL"` was accepted as standalone and could dispatch paid development work. | Both file loading and direct construction require an exact `central` or `standalone` string when supplied, before opening control state. Omission preserves legacy owner-only development behavior. `tests/test_audit_sdk_configuration.py`. |
| P2: stale work selection could execute | An event or stop/resume/wake revision arrives during asynchronous selection. The work path acknowledged events and inserted/dispatched the stale decision; only the wait path checked revisions. | Check the selection revision in the same transaction as planning and acknowledgment; stale work schedules a fresh snapshot without authorizing or executing. Two-connection regressions in `test/audit-stale-work.test.ts`. |
| P2: public content copied undeclared data | A structurally valid availability object with extra private diagnostics was published whole. Malformed offering/activity leaves could also carry nested metadata. | Project exact declared fields, require bounded primitive strings/valid timestamps and omit invalid records. Actual HTTP canaries verify no undeclared metadata in page/API output. `test/audit-public-client.test.ts`. Declared public text remains the trusted provider's publication responsibility. |
| P2: malformed content took public reads offline | A website dependency resolved to null/undefined or another non-object shape instead of rejecting. Public projection dereferenced it and failed. | Invalid root shapes degrade to empty collections and explicit unavailable status. Both `/` and `/api/public` remain available for seven malformed shapes. `test/audit-public-client.test.ts`. |
| P2: enum coercion accepted invalid wire types | Arrays such as `["sandbox"]` or `["succeeded"]` passed `String(...)` enum checks in summary/opening/receipt handling. | Require actual primitive enum strings. Invalid opening requests cannot contact or seed the backend. `test/audit-public-client.test.ts`. |
| P2: diagnostics suppressed a known liability | A valid authenticated failed/unknown 6,000,000-micro charge with a roughly 65 KiB report failed inside the journal envelope. Other optional report shapes were rejected before settlement. The known bill remained unposted. | Financial identities, amounts and outcomes stay strict; optional diagnostics cannot block admission. The core preserves bounded JSON objects up to 16 KiB or records an explicit omission marker. Existing larger exact receipts replay without editing history. Tests cover negative overages, duplicate receipts, restart, provisional/final true-up, invalid report shapes and legacy replay in `tests/test_audit_settlement_diagnostics.py`. |
| P3: extreme cumulative payment projection overflow | Individually valid transaction amounts whose single-payment hold/released total exceeds signed int64 raised raw SQLite-binding `OverflowError`. Transactions already rolled back; no partial credit occurred. | Return explicit `payment_projection_limit` (422) with full rollback, preserving event identities and evidence. Do not clamp or split provider receipts. Wallet-wide aggregates across distinct payments remain exact above int64. Three boundary regressions in `tests/test_audit_payment_bounds.py`; storage bound documented in `ECONOMY_CONTRACT.md`. |

No additional reproducible defect remained in the reviewed paths after repairs. This is a source/test audit, not proof that every future adapter or deployment is correct.

## Executed qualification

- Baseline: **152 Node tests** and **788 Python tests plus 75 subtests** passed; nine SDK runtime-prerequisite checks skipped.
- Final: **176 Node tests**, zero failures/skips. `npm run check` passed formatting, TypeScript checking, all maintained Node tests and the compiled build. All five actual Node-to-Python HTTP/two-SQLite integration tests ran.
- Final: **838 Python tests plus 75 subtests passed**, zero failures, **nine prerequisite skips**, in 40.68 seconds. One existing Starlette HTTPX TestClient deprecation warning remains. Python was qualified at `05ffc3e`; the final `43f5bb3` adds only TypeScript projection handling and its tests, with no Python changes.
- `npm run smoke` passed compiled web/worker startup, migration, authenticated controls, durable action/events and worker liveness. `npm run browser:smoke` passed real desktop/phone form login, run controls and event submission, with no page errors. Its zero-cost demo is not a payment or business-survival claim.
- An independent deterministic accounting probe compared **500 charge/hold/restart stages across 50 runs** with separate arithmetic, local integrity and public-summary checks. Provisional overages, lower/higher final totals, charged failures, exhaustion, free work while negative and refund holds remained correct.
- Independent synthetic payment probes killed execution after cash posting but before queue acknowledgment, then reopened the service and recovered the expired lease without duplicate credit. Stale pending observations preserved settled cash/holds; missing refund transactions retained exposure; later refund/fee posting and repeated reconciliation did not double-count.
- Rebuilt and separately installed package **0.3.0**, schema **5**. All **35 Python/SQL package files** matched source; both installed CLI entry points worked. Installed-package probes verified template startup, idempotent exact opening credit, oversized-diagnostic liabilities, integrity and invalid SDK authority rejection before control-state creation. Wheel SHA256: `9ef9b4832cb6f51da1b16097a9036bf2302beab50e5da7a65467798e18148db9` (local artifact).
- `pip check`, Python source compilation, production npm advisory check (**zero findings**), local Markdown link checks and `git diff --check` passed. No remote CI or production deployment result is asserted.

Commands used in the isolated checkout:

```sh
npm ci
WIDEMIND_TEST_PYTHON=/workspace/.venvs/widemind-economy/bin/python npm run check
npm run smoke
npm run browser:smoke
npm audit --omit=dev --audit-level=high
PYTHONPATH=src /workspace/.venvs/widemind-economy/bin/python -m pytest -q -ra
python -m pip check
python -m pip wheel --no-deps .
```

## Unverified external/runtime prerequisites

No real Stripe sandbox or live transaction, paid inference, real email or production publishing occurred. The current managed environment has no configured Stripe secrets/account and excludes Stripe API access in its enforced network policy. Complete the precise account/key/signing-secret/return-URL/ingress steps in [ECONOMY_SETUP.md](ECONOMY_SETUP.md) before claiming real payment verification. Pending receipts, invoices, fixture output and model claims do not replenish operating credit.

Eight optional Docker checks require an explicitly provisioned pinned `WIDEMIND_TEST_CODE_IMAGE`; one SDK browser check requires a working Chromium OS sandbox. These checks remain skipped with explicit reasons. No unsandboxed browser or host-code fallback was enabled. The web UI browser smoke is a separate check and does not qualify the SDK browser worker.

The standalone SDK is preserved owner tooling, not an automatically registered autonomous paid provider. It still needs an explicit central-authority adapter before that use. Single-payment cumulative hold/release storage remains int64-bounded with explicit rejection; this audit does not add a new accounting schema or unrestricted per-payment aggregate storage.

The GitHub API remains outside the observed network policy, so PR creation may be unavailable even while normal Git publication works. Publication must be verified against the remote main ref; a local audit alone does not establish a remote merge.
