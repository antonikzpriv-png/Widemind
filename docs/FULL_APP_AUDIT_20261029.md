# Full application audit — 29 October 2026

Audited branch: `codex/widemind-complete`, starting at `fd63fb1` and repaired through `67d677e`. This report covers that Python application, not the different implementation now on remote main. Independent reviewers examined accounting/payments, tools/transport, and UI/runtime; integrated regressions and browser checks exercised the combined fixes.

## Outstanding integration finding

**The business application is not integrated into the current merged foundation. Do not merge this branch directly over main.** During this audit, fetching remote main revealed `63f8b1f`, which uses Node 24/TypeScript web and worker processes, core session/CSRF authentication, registered modules, and the protected Python 0.3.0 economy and standalone tools SDK under `src/widemind`. This branch instead contains a separate root-level Python 0.1.0 application, WSGI API, runtime, economy and tool gateway.

Main currently registers only `demo` and `economy`. Its default business/tools/website roles are unavailable; its zero-cost note demonstration is not an order-to-delivery service. Its foundation pages do not include this branch's six-view business console. A direct merge would create conflicting packages, commands, schemas and financial authorities. Passing this branch's tests does not qualify the business workflow on main.

An independent import probe confirmed that keeping both Python trees makes the root package shadow main's `src/widemind`, breaking economy/SDK imports. Both implementations also define incompatible `runtime_runs` and `runtime_actions` tables. The port must preserve core migrations and namespace business tables. This branch's direct effect execution must become receipt-driven selection under the core runtime. Main's CSP and router do not currently provide the richer private HTML/asset integration required by this UI; that extension needs coordinated review alongside session/CSRF adaptation.

The required integration is a business module under `src/modules/business/`, with owned migrations, implementing the existing `Business.selectWork` contract and authenticated namespaced routes. It must use the current runtime's durable actions/events/reconciliation, the current economy bridge for money and verified payment status, and an explicitly registered tools provider. The preserved standalone SDK is not automatically such a provider; its development wallet cannot become central operating credit. The authored UI must be adapted to core sessions/CSRF and public projections rather than replacing core auth. See main's `AGENTS.md`, `docs/INTERFACES.md`, `docs/RUNTIME_ECONOMY.md` and `src/contracts/index.ts` before porting.

No merge into main or complete merged-product qualification is claimed. The original requirement to ship the business module on the merged foundation remains unfinished.

## Reproduced defects repaired on this branch

| Area | Finding and impact | Repair and evidence |
| --- | --- | --- |
| Payment refresh | Comparing only cached JSON allowed a stale observation after an intervening revision or Checkout identity change. | Fence by both revision and session identity; concurrency regressions reject stale writes. |
| Checkout ownership | Distinct action IDs for one order could create another Checkout and replace refund/payment routing. | Durable one-Checkout ownership per job, including interrupted creation and reviewed retries. |
| Refund/dispute cash | Actual chargeback debits and refund/dispute debits before receipt settlement could be omitted from posted cash. | Record confirmed native debits independently of receipt settlement; remaining exposure stays held without double counting. |
| Sender authentication | A whole-header search could accept `dmarc=pass` in quoted SPF text/comments while the actual DMARC result failed. | Parse actual authentication methods, balanced quotes/comments and aligned DMARC evidence; spoofed clauses fail. |
| Usage recovery | Reopening uncertain research with changed model prices could account at new prices. | Persist ordinary/cached/output prices with the original request and use them for recovery. Legacy unknown prices retain uncertainty and budget holds. |
| Gmail cancellation | Read traversal could continue after runtime/capability revocation during OAuth/profile/message requests. | Check control at each network boundary and during recovery. |
| Transport deadlines | DNS/connect/TLS/header opening was not bounded by the whole-request deadline. | Bounded DNS capacity, synchronous request dispatch, shrinking deadlines through connection/response, and a late control/funding check before provider HTTP bytes. Inherited proxy and TLS trust remain active. |
| Workflow recovery | Authorized retries or successful offer reconciliation could leave no wakeup to continue their owner. | Atomically enqueue the owning opportunity/order after recovery. Pause/stop still prevents new work. |
| Delivery promises | Delayed reservation reconciliation could restart the promised turnaround window. | Persist actual effect start time and anchor the deadline to the original attempt. |
| UI refresh races | An older refresh could replace the state returned after a successful operator command. | Fence reads by mutation generation and queue a fresh post-command read. |
| Financial displays | A stored per-order payment observation could look current. | Expose observation time and cached-source labels; unavailable authoritative balances stay unavailable. |
| Record visibility | A fixed latest-500 window could hide an older open obligation and undercount commitments. | Prioritize outstanding records, paginate every collection and calculate full-run counts. A 501-order regression preserves the older open order. |
| Read consistency | A concurrent worker could change rows between record selection and counter queries. | Use one SQLite WAL read snapshot for business records and totals. The separately owned economy remains authoritative for balances. |
| Delivery/issue counts | Physically delivered work could still appear as an outstanding physical commitment; superseded effects could appear unresolved. | Distinguish delivery from later revenue recognition and exclude superseded attempts from active issues. |
| Input/auth validation | Malformed/duplicate public source URLs and unusable operator-token configuration were accepted. | Validate source lists, pagination parameters and printable ASCII tokens; malformed bearer input returns an auth failure. |

Maintained regressions are in `tests/test_economy_audit.py`, `tests/test_tools_audit.py`, `tests/test_full_audit.py`, `tests/test_transport_tls.py` and `tests/ui_browser.py`. The fixes added 35 backend regressions to the previous 166-test suite and expanded the authored browser suite from 56 to 73 checks.

## Executed local verification

- **201 backend tests passed**, zero failures/skips in the audit environment. Includes real localhost TLS with an ephemeral certificate: trusted TLS succeeds and an untrusted certificate receives no HTTP request bytes.
- **73 authored browser checks** and **63 real API/independent-worker browser checks** passed. The former uses explicit HTTP fixtures; the latter uses the actual application and a separately running offline demo worker. Neither calls live providers.
- Python compilation, JavaScript syntax and `git diff --check` passed.
- Built and installed wheel `widemind-0.1.0` into a clean virtual environment, then ran outside the checkout. Two demo invocations preserved the same order/artifact. Five WSGI routes served successfully, all six business migrations were present, and SQLite integrity was `ok`.
- Wheel SHA256: `c9cb609d70300c805e925e5bbda75b9f492d2721098ab531065c1b9cba8d7dde` (local artifact). Fixture deliverable SHA256: `3510fe9b3171aa16fc7d4e11d3b72d09395e3fc48a30a71e6d5f2a05e55d841a`.

Core commands:

```sh
python -m unittest discover -s tests -v
python -m compileall -q widemind tests
node --check widemind/web/static/app.js
python tests/ui_browser.py
python -m pip wheel --no-deps --no-build-isolation .
```

For the actual API/worker browser flow, follow `docs/COMPLETION_STATUS.md` and use a fresh explicit offline `serve-demo` instance. Package checks used a clean virtual environment and repeated `widemind demo` outside the repository. No remote CI or Windows result is asserted. No further reproduced defect remained in the locally reviewed branch paths after repairs; this is not a guarantee of defect-free software.

## Upgrade and external prerequisites

Migration `006_observed_and_started.sql` adds payment observation and effect start timestamps. Stop both old-branch API and worker, back up the private database including its WAL correctly, and restart both at the same revision. Historical payment observations have no invented observation time. Historical effects use their stored creation time as the conservative original-attempt anchor. Uncertain research contexts missing original prices require reviewed reconciliation; changing configuration does not retroactively invent a tariff.

Live qualification remains unperformed: no actual Gmail message, paid OpenAI call, Stripe sandbox/live payment, real customer delivery or profitable outcome was created by this audit. Dedicated provider credentials/accounts, a controlled recipient, approved network access and a correctly isolated deployment remain necessary. The GitHub API has returned policy HTTP 403 while Git transport works, so an actual PR cannot be claimed from branch publication alone. No provider secrets or raw customer data were added to Git.
