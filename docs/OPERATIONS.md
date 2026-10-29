# Runtime, host composition, and live qualification

## What runs

`widemind.runtime.RuntimeControl` persists the run's state, allowed gateway actions, cancellation generation, and an append-only operator audit. `widemind.host.business_factory` composes the business module with the real economy, Stripe, Gmail, research, and runtime clients. It never silently selects demo adapters, grants funding, or resumes work while constructing an API request.

The website and worker are separate processes. An open page is unnecessary for receiving queued events, inspecting commitments, or resuming work. Idle scheduling reads SQLite without invoking a model or provider. See [BUSINESS.md](BUSINESS.md) for the business queue and recovery rules.

`WIDEMIND_MODE=test` is the default production-host profile for provider qualification. This means **real clients**, a Stripe sandbox, and a test wallet; Gmail messages and OpenAI requests still use actual accounts, may have external effects, and may incur actual charges. It requires an explicit controlled-recipient allowlist. A host-created business instance has `execution_profile: provider-test` and `uses_fixtures: false` so the UI and CLI can distinguish it from the offline demo.

`WIDEMIND_MODE=live` requires live Stripe configuration and a separate live run/wallet. Test receipts cannot enter a live wallet. The offline `demo` command remains a different entry point using labelled fixtures only. Never rename test data or change a run's stored mode to pretend a live result exists.

## Configure without exposing credentials

`.env.example` supplies names and nonsecret examples; the application does not automatically load it. Set variables with the deployment's environment/secret manager. Do not paste tokens into chat, commit them, place them in URLs, or send them to a model.

Required groups:

| Group | Configuration |
| --- | --- |
| Run/profile | `WIDEMIND_RUN_ID`, `WIDEMIND_MODE=test` or `live` |
| Private state | `WIDEMIND_BUSINESS_DB`, `WIDEMIND_RUNTIME_DB`, `WIDEMIND_ECONOMY_DB`, `WIDEMIND_TOOLS_DB`, `WIDEMIND_WORKSPACE` |
| Optional secret directory | `WIDEMIND_CREDENTIALS_DIR` |
| Operator access | `WIDEMIND_OPERATOR_TOKEN`, at least 32 unpredictable characters |
| Dedicated mailbox | `WIDEMIND_GMAIL_ADDRESS`, `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET`, `GMAIL_REFRESH_TOKEN` |
| Qualification recipients | `WIDEMIND_ALLOWED_RECIPIENTS`, a JSON list of addresses controlled by the operator; mandatory in provider-test |
| Research | `OPENAI_API_KEY`, `OPENAI_MODEL`, explicit integer `OPENAI_INPUT_MICRO_USD_PER_MILLION_TOKENS` and `OPENAI_OUTPUT_MICRO_USD_PER_MILLION_TOKENS` |
| Research limits | Optional `OPENAI_MAX_OUTPUT_TOKENS` (default 1800), `OPENAI_MAX_INPUT_BYTES` (default 24000), and `WIDEMIND_RESEARCH_SOURCE_URLS` as a JSON HTTPS URL list |
| Stripe | `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_SUCCESS_URL`, `STRIPE_CANCEL_URL` |

Model prices are explicit configuration, not inferred earnings or guaranteed provider rates. Verify them against the actual configured provider/model. Bounded usage is enforced by the gateway; actual/provisional charges and unexpected liabilities remain economy records. A sandbox payment is not real revenue, even if its accompanying OpenAI usage is a real development/qualification expense.

Use `widemind.host.readiness()` for safe configuration diagnostics. It returns only configured/missing states and variable names. `configured_unverified` means configuration is present, not that OAuth scopes, credentials, balances, network access, or a remote account were validated. It performs no provider requests. Never print the environment to troubleshoot setup.

### Filesystem boundary

Use absolute canonical paths. The host rejects overlapping database files, accounting/runtime/tools journals inside the business-data directory, and any private state or credentials inside the agent workspace. A suitable arrangement is:

```text
/var/lib/widemind/business/business.sqlite3
/var/lib/widemind/control/{runtime,economy,tools}.sqlite3
/var/lib/widemind/credentials/
/var/lib/widemind/workspace/
```

The business directory and control directory are separate. Private files are created owner-only. Configure actual operating-system ownership, permissions, a dedicated service identity, network policy, resource limits, and the agent execution sandbox. **Path checks alone do not prevent an arbitrary process running as the same user from reading another directory.** Do not give agent-executed code the host's provider credentials or accounting files. Run credential/accounting gateways in the protected control plane and use authenticated service clients if the agent can execute arbitrary code. Browser/code execution and publishing adapters require their own sandbox/provider qualification before enabling them.

Do not place SQLite WAL databases on an unqualified network filesystem. Retain private WAL/shm files and journals in backups. Use SQLite's online backup API or quiesce writers; copying just the main file while WAL writers run is insufficient.

## Explicit initialization and launch

The default factory is:

```text
WIDEMIND_HOST_FACTORY=widemind.host:business_factory
```

The root CLI may expose an initialization command; the stable host operation is:

```python
from widemind.host import initialize_live
initialize_live(evidence_reference="operator-approved opening credit and qualification run")
```

Despite its historical name, `initialize_live` honors `WIDEMIND_MODE`. It records an explicit opening grant of 5,000,000 micro-USD in the matching economy and creates the runtime **paused**. That grant is owner support, never customer receipts or earned revenue. Repeating initialization does not give a second opening grant or resume a stopped run. Initialization is an operator action, not a public request.

After configuration and initialization, use separate supervisors:

```sh
python -m widemind worker
python -m widemind api --port 8080
```

The API is loopback by default. Deploy it behind TLS, request/connection limits, authentication, a production WSGI server, and appropriate process supervision. Keep worker restarts independent of website availability. Start the run through authenticated operator controls only after the qualification checklist and funding/account policies are reviewed. API/worker construction never performs funding automatically.

The host closes its economy/runtime/tool connections when its request-scoped business Store closes. Partial startup failures also close already-created resources. Each process/thread must have its own SQLite connections.

## Runtime semantics

`state(run_id)` returns `active`, `waiting`, `paused`, `stopped`, `exhausted`, or `failed`. An unknown run returns `failed`; mode mismatches are rejected.

- Active/waiting allow configured new work.
- Paused prevents new dispatch and leaves queued business work intact.
- Stopped prevents new dispatch and signals cooperative cancellation.
- Exhausted/failed prevent new paid work while preserving diagnostics and reconciliation.

`change_state(run_id=..., state=..., action_id=..., evidence_reference=...)` is an authenticated operator/control-plane operation. Action identities are immutable and idempotent; conflicting reuse is rejected. Its `Outcome` is durable and can be queried through `reconcile(action_id=...)`. The private runtime audit cannot be updated or deleted through ordinary SQL transitions.

`configure_capabilities` is a separate operator action. Editable business goals cannot grant a gateway capability. Gateway dispatch checks both the runtime state and its configured action allowance. A tool can retain `cancellation_generation(run_id)` and compare it between steps; a stop followed quickly by resume still invalidates the older generation. `cancelled(run_id)` reports whether current dispatch is blocked.

A stop cannot retract mail already accepted by Gmail, reverse a payment, or guarantee cancellation inside an unsupported provider request. Preserve uncertain effects and charges and reconcile them. The business module continues to accept read-only reconciliation/payment updates while stopped; that is not permission for new fulfillment.

## Controlled provider qualification

These steps require owner-provisioned accounts, network access, and an explicitly authorized profile. Automated tests use mocked transports and do **not** establish that this live checklist has run.

1. Configure `WIDEMIND_MODE=test`, a new qualification run, Stripe test credentials/webhook secret, dedicated Gmail OAuth, and a recipient allowlist containing only controlled inboxes. Confirm the redirect URLs and verified webhook endpoint. Set conservative actual model prices/token limits and an owner-funded expense ceiling.
2. Inspect readiness without disclosing secrets. Initialize the test run paused, confirm one opening grant and zero customer receipts/earned revenue, and review control-plane filesystem isolation.
3. Authorize active state. Submit a small scoped request from the controlled sender, with two permitted HTTPS sources. Confirm one persisted opportunity/quote and one offer mail. Replay the same event and verify no second send or order.
4. Accept the exact quote using the same controlled customer identity. Open the actual Stripe test checkout and use the processor's documented test payment method. Verify the signed event is reconciled by the economy; a screenshot, invoice, mail body, or arbitrary webhook JSON cannot credit the wallet.
5. Inspect the hold, spending authorization, and provider token/runtime ceiling. Fulfill a bounded sourced brief, verify agreed sections/citations, inspect the actual OpenAI usage record and final/provisional operating charge, and verify the private attachment in the controlled inbox.
6. Do not equate a Gmail Sent entry with confirmed delivery. Exercise the configured verified acknowledgment/delivery-evidence path before fulfillment recognition. Confirm cash receipts, earned revenue, and unresolved delivery obligations remain separate.
7. Pause and stop during recoverable work; restart both worker/API. Confirm no new paid execution while blocked, reconciliation remains available, the same action ID is not repeated, and original deadlines survive recovery.
8. Replay payment events, test refunds/disputes before and after fulfillment, and verify cash/earned/holds/spendable totals against private ledger entries. Test an insufficient-funds action and confirm no provider call. Review unresolved costs/effects rather than retrying blindly.
9. Retain a redacted qualification record with run/profile, provider evidence references, measured costs, failures, and interventions. It is a **provider-test** result, not a real paying customer or autonomous profit.
10. For a real pilot, create a separate `live` run and configure real business terms, live Stripe credentials, full operating-cost allocation, dedicated customer communication, and explicit live-effect authorization. The first live milestone requires an independent customer's verified payment and useful delivered work; tests or screenshots cannot manufacture that evidence.

No actual provider credentials, controlled inbox, Stripe settlement, or independent paying customer are included in this repository. Missing provisioning or network policy must remain an explicit dependency, not a mock substituted into a live profile.
