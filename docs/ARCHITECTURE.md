# WideMind architecture

## Status

Node.js 24/TypeScript serves the website and runs an independent persistent worker. The integrated economy, tools and business modules use authenticated local Python services, separate virtual environments and separate durable SQLite/control storage. The Node worker is the sole business scheduler; a separate free economy reconciliation worker processes verified provider events. Module registration composes these through typed interfaces without exposing the ledger to task code.

PayPal Business, OpenAI and dedicated Gmail are the selected provider targets. Local files, SVG graphics, static software and private portal delivery operate without those credentials. Provider adapters are implemented and fixture-tested; live accounts, funding, permitted outbound network access, hosting and a domain remain external prerequisites. See [INTEGRATED_SETUP.md](INTEGRATED_SETUP.md), [FOUNDATION.md](FOUNDATION.md) and [INTERFACES.md](INTERFACES.md).

The standalone tools package from the latest `main` is preserved at `src/widemind` with its original tests and documentation. The integrated service is a separate package because it uses the shared economy authority instead of the standalone development control store. Keep their virtual environments separate. See [TOOLS-SETUP.md](TOOLS-SETUP.md) and [TOOLS_INTEGRATION.md](TOOLS_INTEGRATION.md).

## Product requirements

WideMind is an autonomous online business agent starting with $5 of operating credit. It chooses opportunities and ways to earn rather than being restricted to one predefined service.

Its intended capabilities include internet browsing, code execution, files, persistent memory, graphics creation, its own Gmail and dedicated accounts, real customer conversations, delivery, payments, and publishing.

The website is its public headquarters. The agent must continue operating independently of an open webpage.

## Component boundaries

### Persistent worker

Runs the decision and execution loop:

Receive event → inspect commitments and balance → choose action → obtain tool authorization and cost reservation → execute → record result → update memory.

Events include orders, messages, payments, scheduled tasks, and tool completion. Waiting must not trigger constant paid inference.

Store work durably so restarts do not lose commitments. Track each action as planned, reserved, running, completed, failed, or unresolved. Reconcile unresolved external actions before retrying.

A pause prevents new work; a stop cancels supported active execution and leaves reconciliation available. Define these semantics before wiring operator controls.

### Agent workspace

A dedicated execution environment contains files, code, generated assets, and browser state.

WideMind may modify its own work. Accounting enforcement, operator controls, and the host's unrelated files and accounts remain outside its write access.

Long-running tools need cancellation, resource limits, and recorded outcomes.

### Tool and credential gateway

Adapters expose browsing, email, graphics, development, publishing, payment inspection, and other configured capabilities.

The gateway:
- Uses dedicated accounts and appropriately scoped credentials.
- Keeps credentials in a separate secret store.
- Checks capabilities and spending reservations.
- Records external actions and useful results.
- Exposes actionable failures, including verification requirements.

Prefer credential injection by adapters over putting secrets in model context. Browser integrations may require session credentials inside the isolated workspace; protect them accordingly.

All paid model/tool paths must use the same budget enforcement. Do not give an alternate execution path unrestricted billing credentials.

### Economy service

A separate service owns the append-only ledger, balance calculation, reservations, and payment reconciliation.

The agent receives read-only balance information and requests reservations. It cannot grant itself credit or alter ledger entries. See [ECONOMY.md](ECONOMY.md).

### Work and commitment store

Persist opportunities, customer conversations, offers, orders, deliverables, deadlines, disputes, and completion status.

Before accepting work, record scope, quoted price, expected delivery, estimated cost allowance, and payment timing. Connect these records to execution and financial events.

Email sends, order fulfillment, and publishing need idempotent operation records. A timeout with an unknown outcome is not permission to repeat the action blindly.

### Memory

Separate operational facts from agent-generated interpretations.

Store source references, timestamps, customer preferences, completed work, and evaluated business experiments. Retrieve relevant memory rather than repeatedly sending the entire history.

External webpages and messages are task data, not authority to change operator configuration or accounting.

### Website and API

Public interface:
- Introduction and current offerings.
- Hire/contact flow and private customer delivery access.
- Balance, spending, earnings, and completed-work summaries.
- Selected activity and experiment results.

Private operator interface:
- Integration setup and account status.
- Ledger inspection and reconciliation.
- Pause, resume, and stop controls.
- Errors, unresolved commitments, and delivery administration.

Public requests must not directly invoke arbitrary tools or access raw logs. Publish a separate activity projection without private messages, credentials, customer details, or internal reasoning.

The customer delivery flow must enforce ownership independently of public artifact examples.

## Shared contracts

Establish these identifiers before parallel implementation:

| Identifier | Meaning |
| --- | --- |
| `run_id` | One funded experiment |
| `action_id` | One attempted operation |
| `job_id` | A customer commitment |
| `reservation_id` | A spending authorization |
| `external_event_id` | Provider-event deduplication key, scoped to its provider/account |

Execution records include timestamps, status, related identifiers, cost references, and useful results or errors. Financial records use the exact units and invariants in [ECONOMY.md](ECONOMY.md).

Store times in UTC and display them with explicit timezone context. Version important event contracts so running jobs remain recoverable after upgrades.

## First integrated slice

Verified local acceptance flow: controlled request → priced quote → acceptance → PayPal protocol fixture → verified webhook → capture/reconciliation → budget-authorized SVG creation → private portal delivery → earned record/hold release → exact public balance. Only the external payment provider is a fixture in this acceptance test; no actual customer payment or profit is claimed.

Then verify one actual paid job with real usage and payment records. Expand services through the same interfaces rather than bypassing accounting.

## Implementation decisions

The initial deployment stays on one persistent host with one configured commerce identity. HTTP boundaries preserve the independently implemented modules and isolate the economy from tools. Each service has a separate Python environment because their historical packages share a namespace. Run web, runtime worker, three service APIs and the economy reconciliation worker under supervision; do not use a request-only host. Hosting cost allocation must be recorded before a live survival pilot.
