# Integrated tools service

The Node web/worker use a separate Python 3.11+ service with **no independent wallet**. The service reuses the existing, tested adapters from tools commit `68abf8a85c2918638b25e89b92d784308857825c`, with a new journal-only gateway. Keep this service in its own virtual environment: tools, economy and business packages intentionally have separate `widemind` namespaces and must not be installed into one environment.

```sh
python3 -m venv .venvs/tools
.venvs/tools/bin/python -m pip install --upgrade pip==26.2.2
.venvs/tools/bin/python -m pip install -e 'services/tools[test]'
.venvs/tools/bin/python -m pytest services/tools/tests -q
```

Install optional `services/tools[graphics]` for Pillow PNG and `services/tools[browser]` plus Playwright's browser image for isolated browser rendering. These do not enable network or live accounts automatically. The service uses POSIX filesystem confinement and is intended for a Linux/POSIX host. Untrusted code execution uses only a separately configured Docker sandbox; there is no local host-execution fallback.

Configure server-only environment values, never model inputs or browser bundles:

```text
WIDEMIND_TOOLS_URL=http://127.0.0.1:8081
WIDEMIND_TOOLS_TOKEN=<independent random token, at least 32 characters>
WIDEMIND_TOOLS_STATE=/private/widemind/tools/control
WIDEMIND_TOOLS_WORKSPACE=/private/widemind/tools/workspace
WIDEMIND_ECONOMY_URL=http://127.0.0.1:8080
WIDEMIND_ECONOMY_GATEWAY_TOKEN=<economy gateway identity>
WIDEMIND_TOOLS_ACCOUNT=dedicated-tools
```

State and workspace must be disjoint directories, owned by the service user with mode 0700. They must also stay outside root runtime/economy databases and source. By default only confined file operations, safe SVG rendering, and two deterministic local deliverable workflows are enabled. Default services create no operating balance.

```sh
.venvs/tools/bin/python -m widemind.integrated_service --host 127.0.0.1 --port 8081
```

An optional `--config /private/owner.json` or `WIDEMIND_TOOLS_CONFIG` uses the existing mode-600 owner configuration format (`services/tools/examples/owner-config.example.json`). Network/provider operations require per-tool `enabled: true` and owner `policy.allow_external_effects: true`; Gmail also requires verified dedicated OAuth/account identity and explicit recipient policy. Publishing requires a configured conditional destination. Paid AI/image adapters require credentials and a bounded, owner-certified model/tariff configuration. No live account verification is inferred from installation or status. To inject a provider key from the service environment, add an owner-only mapping such as `"secret_environment":{"openai-key":"WIDEMIND_OPENAI_API_KEY"}` and configure the adapter’s `secret_ref` as `openai-key`. Only explicitly mapped names are read. The encrypted owner store takes precedence so OAuth token rotation can persist safely; secret values never enter capability responses.

## API and enforcement

All service endpoints require the dedicated tools bearer token, including health/capability reads. Plain HTTP binds only loopback; remote exposure requires an authenticated TLS proxy. Root Node module is enabled only when its URL/token are configured and stays absent in the explicit demo profile.

- `GET /health`, `GET /capabilities`: private readiness and per-capability availability.
- `POST /execute`, `POST /reconcile`: Node `{action,reservation}` contract or existing business RPC `{run_id,job_id,action_id,action,arguments,reservation_id,maximum_micro_usd}`.
- `POST /cancel`: `{actionId}`, requests supported cancellation; it never declares a provider effect undone.
- `POST /artifacts/read`: `{path}`, confined private bytes/base64/hash (1 MiB cap). Root exposes this only through an authenticated operator module route. Customer access must use the commerce module's ownership checks, not the tools token.

Before effects, the service reads `GET /v1/reservations/<id>` with the economy gateway identity and verifies run/action/logical tool/account, monetary maximum and `max_duration_ms`/`max_storage_bytes`/optional `max_tokens`. It persists the action intent, starts the reservation exactly once, and enforces the adapter quote plus cancellation/deadline/output caps. A forged opaque token cannot replace authoritative row verification. The adapter records exact external intent before requests and the service persists results/usage. Retrying an ID with changed parameters conflicts; cached receipts are retrievable after financial settlement.

Node executions return receipts for the root economy adapter to settle. Existing business RPC executions settle through economy directly because that protocol expects a gateway-managed financial lifecycle. Both use the same reservation and provider/account/event IDs; there is no second balance or duplicated charge source. Native units/provider request IDs remain in private `nativeUsage`; the receipt's provider is the logical reserved tool and its account is the configured tools account.

Unknown effects remain unknown and are reconciled before retry. Definitive not-performed proof alone permits a new dispatch of the original action ID. Paid tariff usage is provisional until provider/invoice reconciliation; the direct RPC path posts known provisional subtotals as `outcome=unknown` and retains the hold. Unsupported invoice lookup remains explicitly unresolved. Gmail send acceptance is reported as `accepted`, never invented inbox delivery; the customer portal can establish delivery access independently.

## Capabilities and real deliverables

Native provider names: `files`, `graphics`, `research.fetch`, `browser.read`, `gmail`, `ai.generate`, `image.generate`, `code.execute`, `publish.artifact`. Gmail aliases translate `gmail.send/read/list/thread` into the existing adapter operations. Missing configuration is unavailable.

Business workflows:

- `graphics.create`: `{title,description?,width?,height?,elements?,background?,filename?}` creates actual SVG using safe primitives. Returns `{filename,media_type,content,checks,artifact}`.
- `software.create`: `{title,description?,features?,filename?}` creates an escaped static HTML source file; it executes no arbitrary host code. Returns the same artifact shape.
- `research.collect`: `{topic,sections,min_sources}` requires owner-configured distinct HTTPS `source_urls`. It fetches actual bounded excerpts/citations and explicitly labels them as source excerpts, not an inferred synthesis. It is unavailable without sources/network setup. Sourced factual interpretation/quality review remains a business requirement.

Local compute, filesystem and network costs are disclosed as owner-supplied/unallocated, not claimed as profitable free infrastructure. The action still requires a genuine economy authorization even at zero direct tool charge.

## Verification

Verified locally: editable isolated Python install; 352 Python tests passed with 11 optional-environment skips and 75 subtests; three Node adapter tests and type checking. Added cases execute actual files/SVG/HTML, reject reservation identity mismatch before effects, recover/replay without duplicate dispatch, enforce authenticated HTTP/private artifacts, and retain provisional paid liability. Provider tests use deterministic transports and controlled local endpoints. No Gmail delivery, actual model charge, payment, live publishing or deployed sandbox is claimed from fixture results.

## Recovery fencing and stop propagation

The journal now hashes the owner-selected adapter configuration, policy, workspace and adapter version when preparing each new action. Pending effects refuse execution/reconciliation under changed account/model/endpoint/workspace configuration; restore the original configuration or use explicit operator review. The fingerprint stores no literal secret values. Previously completed immutable receipts remain readable after configuration changes.

When `WIDEMIND_RUNTIME_DATABASE` is configured, active adapter checks read the matching runtime run through a read-only SQLite connection. A stopped/missing run or unreadable control plane fails closed; pause permits an already-active operation to finish. Explicit `/cancel` and runtime stop both reach supported adapter cancellation, including business-internal tool calls. Observational reconciliation remains available after stop and never labels an accepted external effect undone.

The business RPC response includes `state`/`data` expected by its protocol, while retaining `status`/`result` aliases. Business recovery can send only `{action_id}`; the service reloads the original durable action/reservation instead of requiring a caller to reconstruct execution parameters.

The integrated and standalone tools manifests now require cryptography 50.x; their optional graphics extra requires Pillow 13.x. Reinstall the graphics extra explicitly when upgrading an environment that already has Pillow. Reconciliation checks the complete original action/reservation fingerprint before returning any cached receipt, including a confirmed result. Upgrading does not migrate encrypted secret material or alter the Fernet format.
