# Tools implementation contracts

The original main (`fc6df21`) is documentation only. This tools slice selects
Python 3.11+, SQLite, and the version-one contracts in
`src/widemind/tools/contracts.py`. It does not claim a deployed business worker,
website, payment processor, or live provider verification.

Adapters live in `src/widemind/tools/`. The trusted gateway owns action IDs,
atomic reservations, cancellation, private receipts and recovery. Every adapter
checks `ctx.check()` before work and uses `ctx.transport` for bounded HTTP.
Transport does not retry or follow redirects implicitly; headers are lowercase.
Timeout, connection, or output-limit exceptions after dispatch are uncertain.

`quote(arguments, config)` validates parameters and bounds cost/resources before
execution. Unknown price/credentials returns actionable unavailability. Paid
quotes require a positive bounded micro-USD reservation. Actual measured units
and configured-tariff costs are provisional until a provider invoice is known.
Unknown usage retains the reservation and remains visible.

Configuration and secrets belong outside the writable workspace. `ctx.config`
and `ctx.policy` come only from owner configuration. `ToolResult` is untrusted
private task data; it cannot authorize actions or change capabilities/budgets.

For external writes call `ctx.mark_dispatched(intent)` before the first request
that can have an effect. Store exact identity/body hash and progress via
`ctx.save_state(patch)`; state is merged durably per action. Never put secrets
into these records. On timeout or restart, reconciliation runs before execution.
`not_executed` requires affirmative provider evidence, not an absent search hit.
If proof is unavailable return `uncertain`; never resend or republish blindly.

Each adapter owns its module, tests and setup document. Shared dependency,
gateway, schema and configuration changes belong to the integration owner.
Development fixtures never send real mail or publish to production. Optional
provider modules must fail independently when configuration is missing.
