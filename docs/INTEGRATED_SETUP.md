# Integrated WideMind

This assembly keeps the persistent Node.js web/worker foundation and connects the separately implemented economy, tools and business modules. Each Python service gets its own virtual environment because their original packages use the same `widemind` namespace. They communicate over authenticated loopback HTTP, not by sharing/importing an agent-writable ledger.

For scheduled market research, evidence-backed opportunities and private bounded product experiments, follow [discovery setup](DISCOVERY.md). This optional layer uses the same worker, tools and ledger and starts disabled. It does not expand outreach permissions or alter the central Stripe alternative.

## Local installation

Requirements: Node.js 24, Python 3.12+, local persistent storage. Optional browser/code capabilities need their separately documented isolation/runtime dependencies.

```sh
npm ci
npm run services:setup             # add -- --with-tests for Python test dependencies
npm run services:configure
npm run migrate
npm run services:start
```

`services:setup` first upgrades the installer in each isolated environment to pip 26.2.2. The tools packages require cryptography 50.x, including the bundled OpenSSL security fixes, and the optional graphics extra requires Pillow 13.x; rerun setup when upgrading an older checkout.

`services:configure` creates mode-600 ignored `var/integration.env`, with distinct random internal service credentials and a stable run identifier. It refuses to overwrite existing files. It does not create a live provider account, opening grant, customer or payment. Launch commands load `.env` first and this private file second; exported variables override both.

In another terminal, explicitly open the sandbox run:

```sh
npm run pilot:init -- --sandbox
npm run dev
npm run worker:dev
```

Or launch services, web and worker together after setup/migration with `npm run dev:all`. Closing a browser never stops the worker. Configure the operator password hash as described in [FOUNDATION.md](FOUNDATION.md); it is not an internal service bearer token. Open `/operator`, resume the configured commerce run, then inspect `/operator/integrations`. `npm run services:status` checks service reachability; `npm run status` checks persistent worker observations.

Customer interface: `/commerce`. Available services come from actual configured capabilities. Customers describe work, receive a private token-scoped request link, review/accept a quote, pay through the configured provider, and retrieve only their own delivery. Access tokens use URL fragments and POST bodies, not query strings or public logs. Quotes and receipts do not create money by themselves.

## Payment provider choice

**PayPal Business is the selected new checkout provider.** Existing Stripe implementation/configuration is preserved for compatibility with the separately developed economy module; the active provider is explicit (`WIDEMIND_PAYMENT_PROVIDER=paypal` by default). Do not silently route new checkout through another account/provider.

PayPal requires a dedicated business/sandbox account, client ID, secret, merchant identity, registered webhook ID and permitted HTTPS return/cancel locations. Configure these in server secrets as documented in the economy integration guide. Configure the approved-order webhook at `/api/modules/economy/webhook`; verified approval events capture the registered order and reconcile actual provider transactions. The local service launcher automatically starts the free economy reconciliation worker once credentials are configured and the commerce run exists. For independently supervised deployment, also run `.venvs/economy/bin/python -m widemind.economy.cli worker --run-id <commerce-run-uuid>` with the economy service environment (including the legacy role-token mapping in its integration guide). Webhook acceptance alone queues work; a reconciliation worker is required to capture/read transactions. An approval page or return redirect is not settlement. The economy must verify capture/account/currency/fees/refunds through the provider before posting spendable receipts. Sandbox and live modes remain separate.

The local starting credit created by `pilot:init --sandbox` is explicitly **test credit**, not a verified live $5 grant or customer revenue. Live funding requires the economy's explicit operator setup and backing/attestation process. Do not use an agent assertion as funding evidence.

## Dedicated accounts and capabilities

OpenAI is the selected model/image provider and Gmail the dedicated communication account. Neither is enabled by a bundled credential. Local file, SVG graphic and static software capabilities can operate without them. Private portal communications are real local deliveries, and are labelled as portal communications rather than claimed Gmail sends.

Enable network tools only through the tools service's owner configuration and account scopes. Gmail requires dedicated OAuth setup and configured recipients/autonomy policy. Publishing requires configured target accounts. Code execution requires configured isolation (no arbitrary host execution fallback). Browser tools require their browser runtime. Missing capabilities remain unavailable.

The managed development environment used for this work has no provider credentials and currently blocks provider API hosts. Local fixtures verify provider protocol behavior; they are not a live account check, transaction or survival pilot. Deployment must permit the selected PayPal, Google and OpenAI endpoints through its supported network configuration and use appropriately scoped secrets.

## Production

Build Node with `npm run build`, migrate with all old processes stopped, and supervise web, runtime worker, all three service APIs and the economy reconciliation worker independently. The process launcher supports `npm run services:start -- economy` (and `tools`/`business`) for individual services; setup uses isolated `.venvs/<service>` environments. Use TLS for public access and authenticated loopback/private service transport. Keep `var` databases/control state, integration secrets and service source inaccessible to tool execution; only the dedicated tools workspace is writable by sandboxed task code.

SQLite databases and WAL must persist on the same host and be backed up consistently. Do not run mixed application versions on the same databases. The example web/worker units remain templates; they do not claim a deployed host/domain or certificate.

## Verification and scope

Use `npm run check`, `npm run smoke`, and `npm run browser:smoke` for the foundation and frontend. `npm run services:test` runs the independently installed Python suites (install with --with-tests). `npm run integration:smoke` starts all real local services and the persistent worker, uses an explicitly local PayPal protocol fixture, and verifies quote, acceptance, signature verification, capture, authorization, artifact delivery and exact financial totals. `npm run integration:browser` runs the same workflow with Chromium using the actual customer request/accept/download screens, phone layout and authenticated operator integration screen. It writes synthetic screenshots under ignored `var/integration-browser`. Neither command posts invented live customer revenue. Tests distinguish local artifacts, sandbox accounting and external-provider fixtures from live behavior.

A real paid survival pilot still requires configured/verified business accounts, a real funded opening, an independently paying customer, delivery acceptance and measured expenses. No code test can manufacture those facts.

## Audit behavior updates

The headquarters now receives available offerings and selected completions from the business service. `WIDEMIND_COMMERCE_RUN_ID` also fixes the public financial identity; creating another run does not change those totals. `/api/public` includes additive `financialRunId`.

A recovered `business.step` receipt describes the zero-cost orchestration handoff, not successful fulfillment. Queued core work, running event leases and uncertain paid effects retain their own states. Only a missing bridge invocation permits same-ID dispatch retry. Pausing during publication leaves work queued for explicit Resume. Capability reads are cached only within one host request and run outside business write transactions; policy/input changes are checked again before committing an offer.

PayPal capture/refund webhooks reconcile from fresh authenticated resource reads after the privacy-minimized durable queue. Private economy diagnostics also expose reconciliation task status. Unsupported provider event types remain unresolved for operator review; automated PayPal dispute/chargeback ingestion is not implemented. No webhook payload or model assertion supplies an invented financial adjustment.

## Current main compatibility

The central runtime economy from main remains available through [RUNTIME_ECONOMY.md](RUNTIME_ECONOMY.md). Its unprefixed operator/gateway credentials select the central RPC bridge; the integration-prefixed economy role credentials select the matching `services/economy` protocol for PayPal commerce. Mixing both credential families fails startup. Select one authority per deployment, and keep separate runtime/ledger databases when testing the alternatives. Their independent schema-5 migrations are not interchangeable; no database conversion occurs.

New `services:configure` files explicitly choose `var/integrated-runtime.sqlite` and `var/integrated-economy.sqlite`, overriding shared-template defaults. `WIDEMIND_SERVICE_RUNTIME_DATABASE` and `WIDEMIND_SERVICE_ECONOMY_DATABASE` can select their locations during configuration. Existing private files are never overwritten. Back up and review any older setup before changing its authority; preserve outstanding runs with their original provider configuration.

Both the root central bridge and integrated services retain main's receipt, selection-revision and public-boundary safeguards. Shared accounting fixes for cumulative payment bounds, strict provider enums and known-charge admission have also been carried into the service implementation. The optional root SDK and service SDK cannot bypass central paid accounting through their local development wallet.

The accounting files now also carry immutable protocol-family ownership: `central-v1` for the root central ledger and `service-v1` for the integrated service. Startup and later connections reject the opposite family, partial schema-5 footprints and missing ownership on an already opened ledger before money can be read or reserved. An existing complete, unmarked schema 5 is pinned only to its matching footprint; common pre-fork schemas 1–4 are pinned to the explicitly selected owner during their existing atomic upgrade. This does not convert or combine funds, change `user_version`, or rewrite applied migrations. A wrong-family error means use the original service and configuration, not delete its marker or import its balance.
