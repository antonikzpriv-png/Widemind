# Runtime economy bridge

The foundation's normal module loader discovers `src/modules/economy/module.ts` in source and `dist/modules/economy/module.js` in a build. This module implements the typed `Economy` interface by calling the protected Python accounting service. It does not create a second ledger or enable a tool/business provider. The Node web process, autonomous-runtime worker and Python payment-reconciliation worker remain independent processes.

## Configure the trusted control plane

Install the normal Node and Python dependencies, configure the protected economy database and role tokens using [economy setup](ECONOMY_SETUP.md), and start the Python HTTP service. For the Node processes, add these variables to their operator-owned environment:

```sh
WIDEMIND_PROFILE=foundation
WIDEMIND_ECONOMY_URL=http://127.0.0.1:8080
WIDEMIND_GATEWAY_TOKEN=<same distinct gateway token configured in Python>
WIDEMIND_OPERATOR_TOKEN=<same distinct operator token configured in Python>
# Optional, 100–4000 milliseconds; default 4000.
WIDEMIND_ECONOMY_TIMEOUT_MS=4000
```

The URL opts the Node bridge in. Without it, the module registers no role/routes, preserving the existing explicit unavailable behavior and zero-cost demo profile. A configured URL requires both valid, distinct role tokens; partial or unsafe configuration fails startup. The real economy bridge requires the `foundation` profile and cannot be combined with the demo economy.

Use HTTP only on explicit loopback (`127.0.0.1`, `localhost`, or `[::1]`), otherwise HTTPS. URLs cannot contain credentials, paths, queries or fragments; redirects are rejected. These are private service settings, not model context, public runtime configuration, browser input or customer metadata. Keep the code, tokens and accounting database inaccessible to the agent's writable workspace. `health()` identifies installed bridge configuration; it does not verify Stripe credentials or establish a real payment.

The Python runtime bridge also needs its configured trusted tool-profile map and authorization-signing secret. A profile binds the exact tool to a provider/account/mode. A missing provider/profile remains unavailable. Tools must verify the opaque authorization and enforce the granted provider limits before execution; installing this bridge does not make an unbounded billing client safe.

## Explicitly open a funded run

First create the paused run through the existing authenticated operator run endpoint. It remains unfunded until an operator calls the module route:

```http
POST /api/modules/economy/open
Content-Type: application/json
X-CSRF-Token: <core session CSRF token>
Cookie: <core operator session>
Origin: <configured public origin>

{"runId":"<existing foundation run UUID>","mode":"sandbox"}
```

The normal root router enforces operator authentication, Origin and CSRF; the handler independently requires its server-derived operator flag and an existing `foundation` run. It submits only the run ID and explicit `sandbox`/`live` mode to the backend's operator-only seed operation. Opening the same run again is idempotent: it cannot add another $5 grant or change the run's mode. A transport failure is uncertain, so inspect/retry the same run rather than creating another identity to obtain more funding.

Summary reads and action authorization never seed a run. Customer invoices, model claims and browser assertions cannot fund a wallet. Live-mode configuration is not proof of processor acceptance. Actual sandbox/live provider setup and verification remain separate requirements.

## Exact money, authorization and settlement

The RPC endpoints are private `POST /v1/runtime/{summary,authorize,settle,seed}` operations. Summary/authorization/settlement use the gateway identity; the seed route alone uses the operator identity. The bridge never forwards browser cookies, CSRF tokens, submitted authorization headers or arbitrary account/provider selectors to Python.

All money is a canonical decimal string. The client rejects numeric JSON money rather than accepting already-rounded JavaScript Numbers. Individual caps and actual charges are nonnegative signed-64-bit amounts. Aggregated summaries use canonical signed/nonnegative decimal strings up to 128 digits and `bigint` arithmetic, preserving totals above JavaScript's safe integer range. The client verifies posted minus reservations and holds equals spendable; when all additive accounting fields are present, it also verifies the full cash equation. Unknown summary fields and backend exception text are not returned to the website.

An authorization response must match the requested run, action and every limit exactly: maximum micro-USD, tokens if present, duration and output bytes. Zero-cost actions retain a genuine zero cap. The backend binds the exact tool/provider/account/mode and supplies the stable opaque authorization; a returned ID alone is not an execution permit. Lost responses retain action idempotency and never justify blind external retries. Only a definitive tool `not_performed` result can invoke the separate `confirmNotPerformed(reservation, evidenceRef)` control operation (`POST /v1/runtime/not-performed`). The backend must explicitly renew the same authorization under its zero-known-liability/evidence/attempt checks; unavailable or uncertain renewal leaves the action unresolved and its cap intact. A missing provider result is not definitive proof. After the supported dispatch-attempt ceiling, a verified `closed` response records the zero-charge, no-effect closure and releases its unused cap; it never authorizes a fourth dispatch.

Settlement sends the accounting envelope: reservation, action/status identity, actual charge, provider/account/event, billable/provisional flags and bounded native/evidence references. The full private tool result already lives in the durable runtime action store and is replaced in the RPC with an explicit omission marker. Invalid or oversized optional diagnostics receive an explicit omission indicator instead of suppressing a known charge. This lets an over-limit charged failure remain visible even when its output is large or malformed. Provisional receipts are submitted for accounting but never reported as completed settlement; the runtime must retain the cap and obtain a final observational reconciliation.

Control calls have a deadline (including transports that ignore abort), a 64 KiB streamed response cap, a 16 KiB accounting-request budget, disabled redirects and best-effort nonblocking stream cleanup. Failures return null/unavailable with fixed redacted reasons. A timeout does not imply the backend did not reserve or settle; repeat only the same durable action identity.

## Verification boundary

`test/economy-bridge.test.ts` uses actual loopback HTTP fixtures and the real module/Store registration path to test exact large amounts, signed categories, unsafe money rejection, bound authorizations, zero costs, known charges with private or invalid diagnostics, provisional receipts, disabled/partial configuration, operator-only opening, timeouts, body limits and redirects. These checks make no Stripe or other paid provider calls. A separate combined-application qualification must exercise this module against the real Python HTTP service; passing provider fixtures must never be labeled a real sandbox payment.
