# Runtime audit and extension — 29 October 2026

This audit extends the runnable foundation from `b07453c`. Source implementation: `ffbb1c9`. The [verification receipt](evidence/runtime-audit-20261029/verification.json) identifies the exact commits, source hashes and checked scope. No live customer/provider credentials or paid operations were used.

## Reproduced defects and repairs

Six deterministic regression tests were run against the original foundation first: all six failed. They pass with the repairs.

| Defect | Observed original behavior | Repair |
| --- | --- | --- |
| Lease identity reused after idle queue deletion | An old lease matched a recreated job for the same owner | Independent unique token on each acquisition, checked on ownership/renewal/commit |
| Tool transport exception after dispatch | Run failed without a scheduled reconciliation | Record unknown outcome and keep recovery scheduled |
| Free dependency hangs | One call could pin the worker and its lease indefinitely | Deadline-bound asynchronous module/control calls, durable timeout diagnostic and retry |
| Authentication cached before request body | A request completed with 201 after its session was logged out | Revalidate revocation/expiry after the body is read and before mutations |
| Money accepted JavaScript numbers | Numeric input silently converted to monetary text | Require canonical decimal strings or bigint |
| Action bounds counted characters | Multibyte input exceeded the byte limit and still executed | Enforce UTF-8 byte limits on input and receipts |

Further checks and fixes preserve chunked UTF-8 bodies, reject insecure secure-cookie configuration, remove core session/CSRF credentials before module forwarding, return 503 on module deadlines, and expose raw request bytes for public ingress signature verification. Provider adapters must authenticate those bytes themselves; parsed JSON alone cannot verify an exact signature.

## Extensions

- Durable, private inbox with source/account/event deduplication, conflict rejection, restart persistence and explicit business acknowledgments. Paused/stopped/failed runs are not implicitly resumed. Incoming data never grants budget or operator authority.
- Business decisions atomically acknowledge inspected events with a plan or wait. New arrivals fence stale decisions. Backlogs larger than the 100-event snapshot continue without stranding later messages or repeatedly running paid planning.
- Actual worker heartbeat records, expiry and graceful shutdown observation; private queue/recovery metrics through Operator, `/api/operator/runtime`, and `npm run status` (0 with a fresh worker, 2 without one).
- Operator event API/form with existing session, exact Origin and CSRF enforcement. First/duplicate/conflicting deliveries return 202/200/409.
- Append-only migration 2. Tests preserve version-1 runs/actions/receipts and settle an old saved receipt without re-executing its tool.

## Verification

`npm run check` passes formatting, source/test types, **50 tests**, and production build. `npm run smoke` verifies compiled web/worker execution, authenticated controls, actual worker liveness and event-driven wakeup/consumption. `npm run browser:smoke` uses real Chromium input for desktop/phone login, create, resume, pause and event delivery, with zero page errors. Process tests verify competing workers, website-independent execution, restart, and status CLI exit codes. Local fixture signatures reject an altered body and deduplicate valid delivery; this is not a live payment integration.

The [phone screenshot](evidence/runtime-audit-20261029/operator-phone.png) contains disposable demo data only and deliberately shows the web page reporting no persistent worker in that browser-only fixture. The compiled/process checks separately run real worker processes.

Setup and upgrade instructions: [FOUNDATION.md](FOUNDATION.md). Integration changes: [INTERFACES.md](INTERFACES.md). Stop both services before upgrading and restart at one revision; do not run mixed worker revisions against the same database.

## Remaining boundaries

Live economy/business/tool providers, funding, payment reconciliation and deployed hosting remain unavailable in this foundation. A timer cannot preempt synchronous blocking module code or recall an accepted external effect. Authorization, settlement and external tools still require idempotent provider implementations and definitive reconciliation. Worker liveness is measured within a TTL (at least 15 seconds after a hard kill), not a profitability or productive-work claim. Existing unresolved reservations are retained rather than assumed free.
