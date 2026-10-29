# WideMind architecture

## Status

The foundation implements Node.js 24/TypeScript, independent web and worker processes, and local SQLite storage. The Python economy control plane implements its own protected ledger, reservations, Stripe adapter and durable reconciliation worker. See [FOUNDATION.md](FOUNDATION.md), [INTERFACES.md](INTERFACES.md), [economy contracts](ECONOMY_CONTRACT.md) and [economy setup](ECONOMY_SETUP.md). Real provider accounts and deployment remain configuration requirements; separately developed tools/business providers are not silently enabled.

## Product requirements

WideMind is an autonomous online business agent starting with $5 of operating credit. It chooses opportunities and ways to earn rather than being restricted to one predefined service.

Its intended capabilities include internet browsing, code execution, files, persistent memory, graphics creation, its own Gmail and dedicated accounts, real customer conversations, delivery, payments, and publishing.

The website is its public headquarters. The agent must continue operating independently of an open webpage.

## Proposed components

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

Simulated customer request → priced offer → budget-authorized tool action → deliverable → simulated payment reconciliation → updated website balance.

Then verify one actual paid job with real usage and payment records. Expand services through the same interfaces rather than bypassing accounting.

## Implementation decisions

Select a small maintainable stack and record its actual commands. Start with one deployment and one agent identity. Separate service boundaries logically first; multiple deployable microservices are not required for the first version.

## Preserved standalone tools boundary

The owner-operated Python tools SDK from latest main is available under `src/widemind/tools`, with its separate control journal and documented setup in [TOOLS-SETUP.md](TOOLS-SETUP.md). It is not a Node runtime module and its development wallet is not the central economy wallet. Do not seed both ledgers for one runtime action, import SDK balances as customer receipts, or bypass the central authorization/settlement path. Paid standalone calls are blocked when central runtime configuration is present; existing effects can still be reconciled. SDK adapters require an explicit central-authority adapter before being enabled in the autonomous runtime. Existing free tools, secret setup and owner-only standalone development workflows are preserved. Fixture availability is not real account verification.
