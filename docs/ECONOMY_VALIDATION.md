# Economy implementation validation

Validated on 28 October 2026 against implementation commit `114dca2`. Base: `fc6df21bb04074afbb2887c0dedcff4a628889c1` (documentation only). This task adds the economy/payment service and its entry points; it does not implement the autonomous worker or website.

## Executed locally

- `python -m pytest -q`: **185 passed, 0 failed, 0 skipped**, 4.83 seconds, Python 3.12.14. One dependency deprecation warning: Starlette's HTTPX TestClient bridge; no tests suppressed.
- Real independent-process races for opening-credit seeding and reservation admission on one SQLite database.
- API integration against actual SQLite: authorization, exact integer inputs, exhausted wallets, failed-but-charged calls, known subtotals, unknown outcomes, negative liabilities, append-only corrections and noncash metric compensation.
- Database UPDATE/DELETE and SQLite REPLACE attempts rejected for immutable journal records, including with default `recursive_triggers=OFF`.
- Real CLI subprocess/HTTP startup and restart, with exactly one $5 opening entry and preserved state.
- Actual FastAPI → Stripe adapter → SQLite workflow using explicit HTTPX provider fixtures: checkout, signatures, duplicate/out-of-order events, complete charge-history pagination, fees, refunds, disputes, reversals, holds and reconciliation.
- Trusted bounded-dispatch gateway: limit propagation, under-reservation rejection, one durable dispatch, authoritative usage receipts, retained reservations after errors/cancellation, and no registered-adapter fallback.
- `python -m pip check`: no broken requirements in both the development environment and a separate environment installed from `requirements.lock` plus the built wheel.
- `python -m pip wheel --no-deps .`: installable wheel includes the SQL migration. Installed that final wheel in a separate environment, outside the source directory: database migration, exact seed and authenticated charge/checkout status endpoints succeeded. Wheel SHA256: `997b60103afd77c1f37d03726ea272171cbaf3dcd5e9530aca6df832d4fc14a2` (local qualification artifact, not a public release).
- `git diff --check` and source compilation passed.
- Independent accounting/security review reverified the fixes and found no additional concrete defect in the reviewed paths.

## Verification boundary and remaining requirements

**No real sandbox or live payment was made.** Tests use synthetic provider fixtures and explicit test credentials. No Stripe credentials were configured. CI configuration is included; remote CI execution is not claimed by these local results.

See [setup](ECONOMY_SETUP.md) for exact variables, Stripe account/key permissions, pinned API version, webhook subscriptions, signature-secret provisioning, checkout URLs and the real sandbox procedure. Real verification needs a dedicated account/sandbox, actual keys and signing secret, `api.stripe.com` access, webhook ingress/CLI forwarding and an operator-created sandbox checkout payment. Live use additionally requires business verification, live capabilities/keys, HTTPS deployment and applicable customer/tax/refund policy.

USD settlement only; no invented FX conversion. Refunds are initiated in Stripe Dashboard and reconciled here. Checkout uncertainty beyond the safe 23-hour idempotency retry window requires operator inspection/recovery rather than another blind charge/session creation. SQLite is a local, single-host service, not a distributed database. The systemd file is a deployment example, not a deployed boundary; enforce its separate service identity and inaccessible accounting files before live agent execution.

There is no inference/tool provider implementation or public website in this module. Future billable adapters must register trusted provider-enforced bounds with `PaidGateway`, or use the documented reserve/start/settle gateway protocol. Hosting/shared-service allocations and provider invoice reconciliation remain explicit operator/trusted-adapter inputs, never model assertions or automatically discovered free money.
