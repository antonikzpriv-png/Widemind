# Business, commerce and private delivery integration

The audited Python business core from `origin/codex/business-fulfillment` is retained under `services/business`. It has its own package/SQLite store and no third-party runtime dependencies. Do not install it into the tools/economy virtual environments: their historical Python package namespace is also `widemind`.

Start the real service with:

```sh
PYTHONPATH=services/business python -m widemind.business.host_server
```

Configure a dedicated `WIDEMIND_BUSINESS_TOKEN` (at least 32 ASCII non-whitespace characters), `WIDEMIND_BUSINESS_DB`, loopback `WIDEMIND_BUSINESS_PORT` (default 8082, or `--host` / `--port` CLI flags), `WIDEMIND_RUNTIME_DATABASE` (read-only control-plane SQLite path), and `WIDEMIND_BUSINESS_MODE=test` or `live`. Set the matching Node `WIDEMIND_BUSINESS_URL` and token, and an explicit `WIDEMIND_COMMERCE_RUN_ID`. The same run must exist in the Node runtime and authoritative economy; changing a run's mode is rejected. Economy/tools service URLs and their dedicated credentials are also needed. Missing integrations fail closed.

The Node business module selects a due durable business event and proposes a zero-billed `business.step` tool action. The module's decorator composes that capability with the tools provider. The outer action receives normal economy authorization/start/settlement; inner creation actions have their own actual reservations and usage, without double-counting those charges in the orchestration receipt. The bridge journals each outer action before `Business.tick()`. A completed result can be reconciled; an interrupted invocation is unknown and never blindly replayed. Node's worker remains the only persistent scheduler; the Python bridge does not start a second autonomous worker.

## Real customer portal

The web integration exposes offerings, customer request/acceptance/status/private delivery, revocation and operator policy/diagnostics routes under `/api/modules/business/`. Requests store the actual supplied contact address; no customers or payments are invented. A random capability token authorizes only its request's quotes, orders, messages and published artifacts. Only its SHA-256 hash is stored. A client may supply a random base64url/hex token before submitting so a lost response is recoverable; server-created tokens are returned once. Tokens go in POST bodies or browser fragments, never query parameters. Repeated request IDs require the original token, and token revocation prevents future access.

Offers, payment requests, commitments and deliverables are actually published to the private customer portal. This local transport is distinct from Gmail: publication is a durable delivery to the dedicated private access channel, not a claim of email acceptance, inbox delivery, or customer reading. Gmail remains optional. Private files are returned only after a delivery publication exists, not merely after generation.

Available service types are `research-brief`, `svg-graphic`, and `software-starter`. Their availability depends on actual tool capabilities and operator policy. Local graphics/software use bounded safe templates without executing arbitrary code. Research requires configured source collection; it is not fabricated. The engine's registry remains extensible rather than a permanent single occupation.

Customer acceptance freezes quote/customer/scope identity. Payments are verified by querying the authoritative economy snapshot; browser notifications and message amounts cannot fund fulfillment. The actual create-tool ID is correctly bound to its reservation even though the reserve-effect journal has a separate ID. Earned revenue/hold release are requested only after a matching private portal publication, through idempotent economy methods. Fees, refunds, provisional costs and final cost accounting remain economy-owned.

## Verification and limits

`PYTHONPATH=services/business python -m unittest discover -s services/business/tests -v` passes 75 tests: the retained core's 56 cases plus nineteen host integration cases. The host tests use explicit loopback provider fixtures and demonstrate quote → acceptance → verified fixture payment → reservation → one creation → private delivery → authoritative earned record. They also test token ownership/revocation/uniqueness, stopped-runtime read access, stable outer actions and observational recovery from a committed core event. Fixtures are not evidence of a live payment or provider account.

The core controls must stay outside any agent code workspace. The trusted host bridge has read-only runtime state access and separate business write storage. HTTP binds loopback and authenticates before bounded JSON parsing; it suppresses request bodies/credentials from logs. Public Node ingress adds rate limits using the socket address. Live operation additionally requires configured dedicated provider accounts, network access, human/legal onboarding where required, and verified funding. No such account, receipt or profitable live run is claimed by these tests.

Two Node integration tests also verify free selection, zero-cost outer candidates, fixed public run selection, rate limits and strict service URL/credential configuration. Core events that are already terminal can observationally reconcile a bridge crash window without rerunning business work. Pending or running core effects remain unknown; no paid tick occurs in reconciliation.

Recovery controls are authenticated Node routes `/api/modules/business/reconcile`, `/retry`, `/resolve-hold`, and `/reconsider`. They schedule the audited core's reconciliation/recovery transitions instead of directly replaying paid work. Unknown effects cannot be retried; paid failures require a newly approved plan/authorization. Terminal bridge-event observation can recover a process interruption after the core event committed. The status projection supplies private effect IDs, states and reasons for operator recovery.

Free work-state observation compares authoritative economy snapshots for recent existing commitments, queuing payment-change events only when financial facts actually change. Global economy event sequence changes alone do not cause busy wakeups. Public provider ingress can also send authenticated event hints for older commitments. Both internal HTTP client variants refuse redirects, including same-host redirects, so bearer identities are never forwarded to a different endpoint. Remote HTTPS preserves the configured proxy.

Fulfillment recovery verifies the exact job/reference earned-metric observation and confirmed hold-release result. A recorded metric alone is not sufficient to mark a partially failed fulfillment sequence complete. Pending hold release remains unresolved. Tests cover this partial sequence, unknown creation recovery without a second tool invocation, payment wakeups without a notification, absence of unrelated-revision loops, and redirect credential isolation.

Audit recovery: an interrupted outer invocation can observe its durable core handoff without completing the underlying job or resolving unknown effects. The receipt sets `recoveredOrchestration`, preserves the core queue/lease, and the next separately authorized step may resume due work. A missing invocation returns definitive same-ID retry evidence. No tick or provider call runs inside bridge reconciliation.

Capability observations are cached within a single request and evaluated outside the business write transaction. The transaction revalidates policy and opportunity inputs before allocating allowances or freezing quotes. Changed policy schedules a fresh local selection. Pausing during offer/publication keeps its event queued so explicit Resume can continue safely; prior effect IDs remain intact.

The website role now publishes available offerings and enum-controlled completion activity through `/v1/public`; customer addresses, request scope, portal tokens and artifacts are absent. The public financial identity is fixed to the configured commerce run.
