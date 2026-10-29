# Foundation runtime economy bridge

The Python economy exposes the foundation's [Economy contract](INTERFACES.md) through authenticated JSON RPC. The Node economy module calls these normal application routes. It does not write the accounting database directly, invent a summary, or automatically fund a run. The Node runtime database and Python accounting database remain separate local files with their own migrations.

## Configuration

Run the trusted Python service using the existing [setup instructions](ECONOMY_SETUP.md). In its operator-owned environment add:

```sh
WIDEMIND_RUNTIME_AUTH_SECRET='<separate persistent random secret of at least 32 characters>'
WIDEMIND_RUNTIME_TOOL_PROFILES='{"local.note":{"provider":"local","account":"sandbox","mode":"sandbox"}}'
```

The profile map is an object keyed by tool ID; every entry contains exactly `provider`, `account` and `mode`. Mode is `sandbox` or `live` (`test` is accepted as a Python configuration alias for sandbox). Configure real tool identities according to their trusted gateway/provider accounts; the example is not a claim that a paid provider or tool is installed. Unmapped tools and mismatched run/account modes fail closed. The secret must differ from all bearer identities and remain stable across restarts; rotating it invalidates old opaque authorizations until the operator restores or explicitly reconciles them. Do not supply it, or operator/gateway bearer tokens, to model inputs or the agent workspace.

On the Node server/worker configure the bridge URL and its narrowly held gateway/operator bearer credentials as documented by the Node economy module. Use HTTPS between hosts; loopback is suitable for a single-host trusted deployment. Keep code, configuration, provider credentials and both control-plane databases outside the agent's write access. Nonempty tool profiles without a persistent key are rejected rather than silently substituting a demonstration balance. Keeping the persistent key with `WIDEMIND_RUNTIME_TOOL_PROFILES='{}'` is a valid reconciliation-only configuration: all new execution is disabled while prior signed bindings, observational verification and known-charge settlement remain available.

## RPC v1

All routes below require the gateway or operator bearer identity, except `/seed`, which requires the operator. The agent bearer cannot invoke them.

| Route | Body | Response |
| --- | --- | --- |
| `POST /v1/runtime/summary` | `{runId}` | Foundation `FinancialSummary` or `null` for an unfunded/unknown run |
| `POST /v1/runtime/seed` | `{runId, mode: "sandbox" or "live"}` | Summary after exactly one idempotent 5,000,000 micro-USD opening grant |
| `POST /v1/runtime/authorize` | `{runId, actionId, tool, limit}` | `authorized` with a bound reservation, `insufficient`, or `unavailable` |
| `POST /v1/runtime/verify` | `{reservation, action, dispatch?: boolean}` | `verified` with configured provider/account/mode, or `unavailable` |
| `POST /v1/runtime/not-performed` | `{reservation, evidenceRef}` | `renewed`, `closed`, or `unavailable` after definitive free provider observation |
| `POST /v1/runtime/settle` | `{reservation, receipt}` | `settled`, or `unavailable` with a bounded diagnostic code |

`limit`, `reservation`, `action` and `receipt` use the actual TypeScript definitions in `src/contracts/index.ts`, including camelCase names. Every money input is a **canonical decimal string**, not a JSON number. Individual caps/actual charges are nonnegative signed-64-bit amounts. Every monetary summary value is also a decimal string; aggregate values support up to 128 decimal digits and are never converted through a JavaScript number. Posted/spendable values, net earned revenue/subsidies, processor fee expenses/rebates, corrections and adjustments can be signed. Native currency/minor-unit/conversion/rounding evidence is retained with trusted usage.

Summary separates opening grants, owner/test funding, receipts, operating charges, fees, refunds, chargebacks, reversals, adjustments and noncash metrics. Its equation includes a separate signed `otherCashMicros` aggregate for future cash categories. Summary reads never create a run or grant money. Only an explicit authenticated operator seed supplies the opening $5; repeated seeds cannot multiply it or change the mode.

## Authorization and execution

Authorization admission and its immutable run/action/tool/exact-limit/provider/account/mode binding commit in one accounting transaction. A replay returns the same identity only while the reservation is still usable and its configured profile still matches. Removing or changing a tool/account prevents new execution, but does not erase the original accounting identity: verified old-provider charges and observational reconciliation continue against the persisted binding and HMAC. A definitive no-effect confirmation for a retired profile closes verified zero cost and releases its cap instead of renewing execution. An already dispatched, unknown, settled or cancelled action cannot obtain a fresh execution authorization through replay. HMAC proofs are opaque and verified against the persisted binding, independently of model instructions. Editing a limit, action, tool or account cannot expand permission.

A trusted tool gateway calls `/verify` with both the complete action and reservation. `dispatch: true` atomically marks the reservation running once and rechecks paid credit; duplicate dispatch fails. The gateway must also enforce the exact provider token/time/output/charge bounds before execution and use action-level provider idempotency. `dispatch: false` verifies identity for **observational reconciliation**, including running/unknown/settled attempts; it never authorizes another effect. A transport timeout or absent provider outcome is not permission to repeat the effect. After a trusted observational gateway definitively proves that an effect was not performed, `/not-performed` can renew the same reservation while preserving its exact cap. It requires a distinct evidence reference for each dispatch and rejects any known final or provisional usage, including a zero subtotal. Unknown outcomes and eventually consistent “not found” responses are insufficient. At most three actual dispatches are permitted, enforced in the core even through legacy start routes. After the third confirmed not-performed attempt, the service records verified zero final cost, releases the remaining reservation and returns `closed`; it never authorizes a fourth dispatch. Replaying evidence cannot renew a newer running attempt. If the renewal response was lost, a fresh observational reference can acknowledge an already-reserved latest renewal without resetting state or counters. A fresh reference can likewise acknowledge an already-closed verified zero outcome. The gateway/runtime remain responsible for establishing definitive provider evidence; model assertions are not authority.

Native zero-cost actions reserve exactly zero, including when a wallet is exhausted or negative. No artificial one-micro reservation or grant is created. A provider charge above a supposedly free cap still posts as a liability, may make the wallet negative and blocks new paid spending. Free actions still require a configured tool/profile, bound authorization and one-time dispatch.

## Receipts and recovery

Only the trusted gateway identity submits provider usage. Provider/account/action must match the persisted authorization. Actual failed-call charges post just like successful-call charges. A provisional receipt posts its verified subtotal, retains the unused cap and returns `unavailable` so the runtime keeps the action unresolved and reconciles later. A later final event can have a different provider event ID: the stable native attempt identity applies only the delta and releases the remaining reservation.

The provider/account/mode/event inbox and charge settlement commit atomically. Duplicate observations cannot charge twice; reuse of an event for another action or conflicting immutable data fails closed. A fresh provider event confirming the same final amount and outcome appends its observation/evidence without posting another charge. A changed final amount/outcome requires an explicit compensating correction rather than an automatic rewrite. Late duplicate provisional observations cannot roll a final charge backward. Unknown outcomes and reservations survive service/runtime restarts.

Full tool results remain in the private runtime action store. The accounting RPC ignores optional result/external-reference diagnostics rather than dropping verified actual usage because a result is large; authoritative evidence references, flags and native monetary evidence remain in accounting metadata. This boundary does not fetch paid inference, create provider accounts or claim a live payment. Provider fixtures and synthetic tool receipts are local validation only; real Stripe sandbox/live verification still requires its separately configured account, credentials, ingress and network access.

Schema 5 adds native zero-cap reservations, immutable runtime authorizations and receipt-event records while preserving prior history and references. The migration temporarily disables foreign-key enforcement only on its dedicated migration connection before the transaction, checks all references before commit, then restores enforcement. Back up and stop mixed revisions before upgrading; do not rewrite applied prior migrations or clear unknown reservations to recover.
