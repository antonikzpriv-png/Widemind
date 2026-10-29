# Foundation verification — 28 October 2026

Production source commit: `e8f9cc3` (full SHA and file hashes in [verification.json](verification.json)). All checks ran locally with Node 24.19.0, npm 11.9.0 and TypeScript 5.9.3.

- Clean `npm ci`, migrations, formatting, source/test type checking, **26 tests**, and production build passed.
- Compiled smoke launched the actual web and worker entrypoints, authenticated, created/resumed a run through HTTP, and verified the persisted local note receipt. The balance remained unavailable.
- Process tests ran competing workers against one SQLite file, closed the web server before work, restarted a worker, and checked that execution was not duplicated.
- Recovery tests covered lost responses, reconciliation before retry, stable IDs, charged failures/settlement interruption, attempt ceilings, expired-lease fencing, pause/stop, unexpected liability and unavailable dependencies.
- Auth tests covered missing credentials, CSRF/Origin, durable sessions, password rotation, logout, rate limits, namespaced route authorization and public/private projections.
- Browser smoke used actual Chromium pointer/text input to sign in, create a paused run, resume and pause. Desktop and 390px phone checks passed without page exceptions or horizontal overflow. It caught and verified the fix for browser form login being rejected when `Referrer-Policy: no-referrer` produced a null Origin; `same-origin` now preserves the required request origin.
- All four development/production web/worker commands started successfully, and the password-hash CLI was checked with disposable input.

Reproduce using [FOUNDATION.md](../../FOUNDATION.md). `npm run browser:smoke` requires Chromium and a production build. CI is configured, but these receipts do not claim a remote Actions run or deployed service.

The screenshots use disposable demo data only. They do not show a real funded run, customer, payment, configured provider or deployed website.

[Desktop headquarters](headquarters-desktop.png) · [Phone operator controls](operator-phone.png)
