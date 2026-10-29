# Runnable foundation

## Implemented stack

Node.js **24.x**, TypeScript 5.9, Node's HTTP server and `node:sqlite`. Runtime dependencies: none. TypeScript and Node types are development dependencies pinned by `package-lock.json`. SQLite is a local durable file in WAL mode, with foreign keys, full synchronous commits and a five-second busy timeout. Node may emit an experimental SQLite warning depending on its 24.x release.

One repository produces two independent executables: web/API and worker. They share a database on **one host**, not an NFS/network volume. SQLite is sufficient for this initial single-identity deployment. Multiple workers on that host use database leases; no Redis or browser-driven scheduling is required. An always-on host/process supervisor is required; a serverless request-only deployment is not sufficient.

## Clean checkout

```sh
node --version                         # 24.x
npm ci
cp .env.example .env
chmod 600 .env
npm run migrate
npm run check                          # formatting, source + test types, tests, build
npm run smoke                          # compiled web + worker + auth + durable demo
```

If your managed environment's default npm cache is not writable, use `npm ci --cache /tmp/widemind-npm-cache`. This does not change package sources or disable TLS checks.

Create a password hash without putting the password in shell history (Bash):

```sh
read -rs -p 'Operator password (at least 12 bytes): ' wm_password; echo
printf '%s' "$wm_password" | npm run --silent operator:hash
unset wm_password
```

Put the resulting `scrypt:1:...` value in `WIDEMIND_OPERATOR_PASSWORD_HASH` in `.env`. The helper reads stdin, outputs only a salted scrypt hash, and never saves the password. Do not commit `.env`. The server accepts no default password. Missing configuration leaves public pages available and operator APIs unauthorized. Changing the password hash invalidates existing sessions.

All npm launch/migration/hash commands load `.env` if present. Exported environment variables take precedence. Secret configuration is separate from `RuntimeConfig`; module contexts contain only configuration and the privileged database handle. Individual adapters must load their own narrowly scoped server secrets and never expose them through business snapshots, tool inputs, public data or logs.

## Development

In separate terminals:

```sh
npm run dev                            # http://127.0.0.1:3000
npm run worker:dev                     # persistent worker, independently watched
```

At `/operator`, sign in, create a paused run, and Resume. In the default `foundation` profile it waits with unavailable module diagnostics; **no $5 balance is invented**. Operator controls are real server-side state changes.

For the deterministic, zero-cost vertical slice, set `WIDEMIND_PROFILE=demo` in `.env` and restart both processes. Create a new run and Resume. The worker obtains zero-cost demo authorization, writes a real local note into a durable receipt, settles its zero usage and waits indefinitely. Inspect the action result in Operator. This does not simulate a customer, payment, opening grant or business earnings. Its financial summary remains unavailable. Switch back to `foundation` before installing production providers. A worker refuses runs from another profile.

## Production build and process commands

```sh
npm run build
npm run migrate
npm start                              # compiled web executable
npm run worker                         # compiled persistent worker, separate terminal/service
```

`npm run format` formats source/tests/scripts; `npm run format:check` checks them. `npm run smoke` launches the compiled web and worker against a disposable database, runs real migration/login/create/resume APIs, and verifies a durable local receipt with no assumed balance. It cleans up its child processes and data.

`npm run browser:smoke` checks the actual desktop/phone forms with Chromium after a build. It needs `/usr/bin/chromium` or an explicit `WIDEMIND_CHROMIUM` executable path; it uses Node’s built-in WebSocket/CDP client, not a browser SDK. It writes local screenshots/report under ignored `var/browser-smoke/`. The GitHub CI example uses the runner’s Google Chrome executable.

`npm test` uses source TypeScript directly with Node 24 type stripping; only erasable TypeScript syntax is permitted. `npm run typecheck` checks source and tests. `npm run build` creates `dist/` and rewrites relative `.ts` imports to `.js`. Startup applies the same versioned migrations as `npm run migrate`; run the explicit migration command before upgrades for an early failure and audit trail. Migrations never seed money.

`GET /healthz` confirms only the web process. The authenticated `GET /api/operator/runtime`, Operator's Worker health panel, and `npm run status` report live worker heartbeats, due/leased jobs, unresolved actions and pending inbox events. The CLI exits 0 with a fresh worker and 2 without one. A heartbeat is an observation, not provider readiness or proof of productive work; a killed process can remain fresh until its TTL (at least 15 seconds) expires. Graceful shutdown marks it stopped immediately. Public APIs do not expose these private diagnostics.

`WIDEMIND_DEPENDENCY_MS` bounds asynchronous free module calls (default 5000 ms). Timed-out selection/control/reconciliation calls no longer pin the entire worker; recovery is scheduled. It cannot interrupt synchronous blocking module code or recall provider effects. Tool transport rejection after dispatch remains unresolved and is reconciled before retry. HTTP module timeouts return 503; public content/finance reads degrade to unavailable/null rather than invented values.

## Persistent deployment

Example unit files are in `deployment/`. Install the checkout at `/opt/widemind`, create a dedicated non-root `widemind` service user, install/build there, and place a mode-600 `.env` there with an **absolute** database path such as `/var/lib/widemind/widemind.sqlite`. Create that directory owned by the service user with mode 700. Both services use the same profile, database and installed revision.

Use a TLS reverse proxy for Internet exposure; configure `WIDEMIND_PUBLIC_ORIGIN=https://your-domain`, `WIDEMIND_SECURE_COOKIES=true`, and listen on loopback behind the proxy. Secure cookies are mandatory if binding beyond loopback. Requests with mutation bodies require the exact configured Origin; the server never trusts forwarded headers to select its origin. Configure the proxy's own request limits and access policy. Examples are deployment instructions, **not a claim of a configured host or TLS certificate**.

For backup, stop both services and copy the database plus any remaining `-wal`/`-shm` companions together, or use SQLite's online backup facility. Never copy only a live database file while ignoring WAL. Keep private database backups outside the public web root.

The control-plane process runs trusted application code. No arbitrary code/browser/shell adapter is supplied. Before adding one, execute task code under a different OS/container identity with an explicit workspace, no control-plane database/source access, no inherited provider/operator credentials, resource limits, network/account restrictions and a narrowly authorized tool gateway. A TypeScript interface is not an OS sandbox. Module code is privileged trusted deployment code, not agent-generated content.

## States and controls

- `active`: ready to inspect/select/execute work.
- `waiting`: no work until a business-specified UTC wakeup, dependency retry, or explicit Wake. Free polling checks the durable queue, never a model.
- `paused`: no new planning, reservation or tool execution. An already started operation may finish. Reconciliation and settlement remain available.
- `exhausted`: economy denied the action or actual liability exceeded its authorized limit. Free reconciliation and idempotent authorization checks remain scheduled; only the economy can authorize further spend.
- `stopped`: no new work; cancellation is requested for supported active tools. Unknown outcomes retain their reservation and outbox for reconciliation.
- `failed`: unexpected runtime/module error; durable state stays available and an operator Resume explicitly retries recovery.

Pause/stop take effect immediately for new work. Active tools observe stop at the lease heartbeat (normally at most ten seconds) or their execution deadline. Tools must enforce cancellation and provider limits themselves; aborting a promise does not recall an accepted external request. SIGTERM requests active cancellation and drains recovery; a hard kill leaves lease-expiry recovery to another worker.

Resume is an explicit recorded intervention, including reactivation of stopped/failed/exhausted runs; it does not fund the run. Wake is only valid for runs already set to run. Controls increment a revision so in-flight decisions cannot overwrite newer operator intent. A durable job lease has an owner, expiry and increasing fence. Expired workers cannot commit a result or extend a replacement owner's lease.

## Implemented versus unavailable

Implemented: local persistent scheduling, typed registration, migrations, durable runs/actions/outbox/results, leases/fencing, bounded attempts, recovery, operator sessions/CSRF, public/private projections, baseline run screens, zero-cost local demo.

Default-unavailable until configured: operating credit and payments through the protected economy module; opportunity/order selection and the sourced-brief service through the business module; Gmail and OpenAI through its centrally authorized tools bridge. See [business setup](BUSINESS_MODULE.md), [tools](BUSINESS_TOOLS.md), [UI](BUSINESS_UI.md) and the [offline integrated demonstration](BUSINESS_DEMO.md). Graphics, arbitrary code, general browser/publishing adapters and a separate customer purchase/download portal are not registered autonomous business providers by this slice. Private delivery uses the dedicated authenticated Gmail flow. Real accounts, payment ingress, provider qualification and live hosting remain deployment prerequisites. No provider credentials are needed for the foundation tests/demo. No recurring paid planning occurs while idle. Development and hosting costs are not billed by this foundation and must be disclosed/allocated by the economy module before a real survival pilot.

## Durable incoming events

Operator can use a run's **Deliver an operator event** form, or authenticated `POST /api/operator/runs/<uuid>/events` with `{eventId,kind,payload}`. Reuse the event ID when retrying. A first delivery returns 202, an identical retry 200, and reuse with a conflicting run/kind/payload 409. The route records the source/account as operator; body claims cannot impersonate a verified payment provider. Event payloads are private task data, never budget or configuration authority.

Eligible running work wakes independently of an open webpage. Paused/stopped/failed runs retain incoming events until explicitly resumed. Business modules inspect the oldest 100 pending events in a snapshot and explicitly acknowledge IDs; consumption commits with the work plan or waiting decision. Unseen backlog batches continue without busy polling. The demo acknowledges events locally without additional tool execution or paid planning.

Upgrade note: schema migration 2 adds the inbox, worker presence and unique lease tokens while preserving version-1 runs/actions/receipts. Stop the web and worker services before upgrading, back up the database correctly, run `npm run migrate`, then start both at the same revision. Do not run mixed runtime revisions against the same database. Applied migration 1 is unchanged.

## Economy integration

The discoverable economy module now connects this foundation to the protected Python ledger through the [runtime economy bridge](RUNTIME_ECONOMY.md). Run the Node website and autonomous worker, Python economy HTTP service, and Python payment-reconciliation worker as independent supervised processes. A runtime run starts unfunded; its authenticated operator explicitly opens it in sandbox/live accounting mode. The economy supplies exactly $5 once and enforces later reservations/usage independently of model instructions.

Run `python -m pip install -r requirements.lock -e .` before `npm run check`, because the maintained Node suite now starts the real Python HTTP application for cross-language integration tests. Set `WIDEMIND_TEST_PYTHON` to the interpreter path when using a separate virtual environment. These checks use synthetic tool/provider usage, not live payments. Existing `demo` remains an explicit zero-cost foundation fixture; disable the real bridge URL when selecting that profile.
