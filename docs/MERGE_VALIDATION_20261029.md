# Combined main and commerce qualification

Inputs: current main `63f8b1ff7d9ed7ea0a50b79c0f034026f69dfc80` and audited commerce branch `4b0a530fa39c10a82189437c055c287de5063f52`. The merge was prepared in a separate worktree so the existing development worktrees and private data were preserved.

## Resolution

- Retained main's central economy/Stripe runtime bridge, Python package/schema, selection-revision fencing, provisional/rearm behavior, bounded result accounting and strict public projections.
- Integrated the audited PayPal commerce, private customer portal, business and tools services. One selected economy role serves a deployment: prefixed integration credentials choose the matching service protocol; unprefixed central credentials choose main's central RPC protocol. Mixed credentials or incompatible service/central combinations fail startup. These alternatives do not combine balances or migrate one schema-5 ledger into the other.
- Preserved main's client API and added `service-client.ts` / `service-module.ts` for the service protocol. Factories honor the supplied private environment instead of inheriting unrelated process credentials.
- Propagated common main fixes into the corresponding service code: known bills survive unusable optional diagnostics, unsupported cumulative payment projections are rejected atomically, provider enums stay strict, and the standalone SDK cannot bypass central paid accounting.
- Kept exact sub-cent financial display and all new main categories, while retaining commerce-run financial selection and redacted public business content.
- Generated service configuration now selects distinct runtime/ledger database defaults. `.env` loads before private `var/integration.env`, allowing generated service settings to override blank fields from the shared template. Exported variables still take precedence. Existing private files are not overwritten.
- Updated the root lockfile to the audited cryptography 50.0.2 and Pillow 13.0.0 versions. CI installs the central Python dependencies for the combined Node/Python checks as well as isolated service environments. Unit-service commands remove deployment credentials/database settings and use explicit local fixtures.

## Local checks

| Check | Result |
| --- | --- |
| Node format/type/tests/build | 192 tests passed, zero failures |
| Root central economy and SDK Python suite | 838 passed, 9 optional-runtime skips, 75 subtests passed |
| Service economy suite, including backported main regressions | 398 passed |
| Service tools suite, including central-authority guards | 389 passed, 11 optional-runtime skips, 75 subtests passed |
| Business suite | 75 passed |
| Shared-template/private-config test | Actual configuration and migration select isolated paths and create no accounting wallet or funds |
| Compiled web/worker smoke | Passed: auth, controls, durability, worker health and event wakeup |
| Foundation browser | Desktop/phone login, create/resume/pause, event form and unavailable-worker display passed |
| Commerce browser | Actual local services/worker, quote/accept, private download, operator policy/error controls, pinned public finance, hard worker kill/restart and idempotent capture/refund passed |
| Root package | Locked install and `pip check` passed; wheel built with migrations |
| Root dependency advisory scan | No known advisories reported for installed versions |

The first foundation-browser run timed out on a CDP screenshot while the full suites were running concurrently. Its isolated rerun passed without changing the browser harness timeout or application behavior. No page exceptions were reported in either passing browser gate.

Payment records are explicitly local PayPal protocol fixtures. Real services, databases, tools, portal delivery and worker recovery run around that boundary. The fixture records one checkout/capture and one external refund, retaining exact $6.90 before-refund and $4.90 after-refund balances. These are not live receipts, customer revenue or profit.

## Boundaries

Live provider accounts, verified funding, hosting/domain/network configuration, automated PayPal dispute handling and optional browser/Docker tool prerequisites retain the limitations in [IMPLEMENTATION_AUDIT.md](IMPLEMENTATION_AUDIT.md). Passing local checks does not provision those dependencies. Existing database files were not converted or funded during validation.
