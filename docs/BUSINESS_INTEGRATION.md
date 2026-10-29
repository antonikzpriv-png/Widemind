# Business, commerce and private delivery integration

The audited Python business core from `origin/codex/business-fulfillment` is retained under `services/business`. It has its own package/SQLite store and no third-party runtime dependencies. Do not install it into the tools/economy virtual environments: their historical Python package namespace is also `widemind`.

Start the real service with:

```sh
PYTHONPATH=services/business python -m widemind.business.host_server
```

Configure a dedicated `WIDEMIND_BUSINESS_TOKEN` (at least 32 ASCII non-whitespace characters), `WIDEMIND_BUSINESS_DB`, loopback `WIDEMIND_BUSINESS_PORT` (default 8082), `WIDEMIND_RUNTIME_DATABASE` (read-only control-plane SQLite path), and `WIDEMIND_BUSINESS_MODE=test` or `live`. Set the matching Node `WIDEMIND_BUSINESS_URL` and token, and an explicit `WIDEMIND_COMMERCE_RUN_ID`. The same run must exist in the Node runtime and authoritative economy; changing a run's mode is rejected. Economy/tools service URLs and their dedicated credentials are also needed. Missing integrations fail closed.

The Node business module selects a due durable business event and proposes a zero-billed `business.step` tool action. The module's decorator composes that capability with the tools provider. The outer action receives normal economy authorization/start/settlement; inner creation actions have their own actual reservations and usage, without double-counting those charges in the orchestration receipt. The bridge journals each outer action before `Business.tick()`. A completed result can be reconciled; an interrupted invocation is unknown and never blindly replayed. Node's worker remains the only persistent scheduler; the Python bridge does not start a second autonomous worker.

## Real customer portal

The web integration exposes offerings, customer request/acceptance/status/private delivery, revocation and operator policy/diagnostics routes under `/api/modules/business/`. Requests store the actual supplied contact address; no customers or payments are invented. A random capability token authorizes only its request's quotes, orders, messages and published artifacts. Only its SHA-256 hash is stored. A client may supply a random base64url/hex token before submitting so a lost response is recoverable; server-created tokens are returned once. Tokens go in POST bodies or browser fragments, never query parameters. Repeated request IDs require the original token, and token revocation prevents future access.

Offers, payment requests, commitments and deliverables are actually published to the private customer portal. This local transport is distinct from Gmail: publication is a durable delivery to the dedicated private access channel, not a claim of email acceptance, inbox delivery, or customer reading. Gmail remains optional. Private files are returned only after a delivery publication exists, not merely after generation.

Available service types are `research-brief`, `svg-graphic`, and `software-starter`. Their availability depends on actual tool capabilities and operator policy. Local graphics/software use bounded safe templates without executing arbitrary code. Research requires configured source collection; it is not fabricated. The engine's registry remains extensible rather than a permanent single occupation.

Customer acceptance freezes quote/customer/scope identity. Payments are verified by querying the authoritative economy snapshot; browser notifications and message amounts cannot fund fulfillment. The actual create-tool ID is correctly bound to its reservation even though the reserve-effect journal has a separate ID. Earned revenue/hold release are requested only after a matching private portal publication, through idempotent economy methods. Fees, refunds, provisional costs and final cost accounting remain economy-owned.

## Verification and limits

`PYTHONPATH=services/business python -m unittest discover -s services/business/tests -v` passes 60 tests: the retained core's 56 cases plus four host integration cases. The host tests use explicit loopback provider fixtures and demonstrate quote → acceptance → verified fixture payment → reservation → one creation → private delivery → authoritative earned record. They also test token ownership/revocation, stopped-runtime read access, stable outer actions and unknown-outcome handling. Fixtures are not evidence of a live payment or provider account.

The core controls must stay outside any agent code workspace. The trusted host bridge has read-only runtime state access and separate business write storage. HTTP binds loopback and authenticates before bounded JSON parsing; it suppresses request bodies/credentials from logs. Public Node ingress adds rate limits using the socket address. Live operation additionally requires configured dedicated provider accounts, network access, human/legal onboarding where required, and verified funding. No such account, receipt or profitable live run is claimed by these tests.
