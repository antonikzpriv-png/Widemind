# Creative fulfillment in the PayPal deployment

This extends [the integrated PayPal services](INTEGRATED_SETUP.md); it does not select or change the separate central Stripe business module. The persistent Node worker remains the only business scheduler.

| Offering | Actual deliverable | Required capabilities |
| --- | --- | --- |
| `custom-graphic` | Customer-specific SVG original and full-resolution PNG preview/download | `ai.generate`, `creative.graphics`, `files`, enabled native `graphics`, Pillow |
| `interactive-calculator` | Functional self-contained calculator, JS/CSS source, instructions and frozen acceptance scope | `ai.generate`, `creative.software`, `files`, enabled `code.execute`, reviewed local Python+Node Docker image |

The existing `svg-graphic` and `software-starter` remain labeled **fixed templates**. Enhanced offerings stay unavailable without real dependencies and an operator-approved price. Existing run policies are not silently expanded.

## Scope and verification

Graphics scope freezes `title`, `description`, `width`, `height`, `required_text`, `palette`, `visual_requirements` and `max_revisions`. Canvas bounds are 128–2048 pixels and at most 4 million pixels; colors use opaque six-digit hex. The initial bundled font supports printable ASCII required text; other scripts are rejected before quoting. The model produces bounded safe primitives, never SVG markup or asset URLs. Checks measure required text, stroke/canvas bounds, clipping, occlusion, contrast, palette, XML parsing and actual PNG rendering. Arbitrary visual prose is retained as **customer review notes**, not falsely marked as stylistically verified. See [graphics checks](../services/tools/docs/CREATIVE-GRAPHICS.md).

The software category is an **interactive arithmetic calculator**, rather than arbitrary apps. Frozen numeric inputs/ranges/defaults, output names and 2–16 independent numerical examples define observable acceptance. Supported feature choices are calculation, live updates, reset, input validation, responsive layout and keyboard entry. Unsupported features such as accounts, CSV export and external APIs are rejected before quoting. The customer reviews examples in the quote before accepting. A model-generated bounded expression tree compiles into real HTML/JS; Node executes the delivered JavaScript through the existing networkless Docker adapter against those frozen examples, invalid inputs and boundaries. Reports bind results to the source hash. A feature list or model opinion cannot satisfy the checks. Calculators use JavaScript floating-point numbers and are not an exact accounting ledger. See [software implementation](SOFTWARE_FULFILLMENT.md).

## Accounting, revisions and recovery

Acceptance freezes scope and total creation allowance. Each round permits **two generations at most**: the first design and one repair after a definitive check/validation failure. There are 0–2 included customer revisions. The allowance splits across all possible model calls; every generation and verification slot gets an authoritative reservation **before the first commitment**. Testing/rendering/file operations use reservations too, even at zero direct tool charge. Local infrastructure remains an explicitly excluded owner compute/storage cost.

Stages persist immutable inputs, action IDs, reservations, output/check evidence and usage. An uncertain external effect or interrupted sandbox cannot trigger repair or blind replay; recovery observes the original action first. Known completed stages survive restart, including a terminal tool receipt persisted just before a stage update. Consumed reservations retain their charges when payment loss cancels unused slots. Exhausted repair attempts stop for review and release provably unused caps. Confirmed payment loss stops further work, preserving artifacts and unresolved liabilities; repayment never revives cancelled slots. A separately quoted scope change or operator review is required.

Unused repair caps release after verification. Future revision caps stay reserved until used or **48 hours after first delivery**; the worker schedules free expiry atomically with completion, without paid idle planning. Background payment observation continues at least every 30 seconds even when an older delivery has a future deadline; no website visit is needed. A revision accepted before expiry may finish under its existing authorization, then remaining unused caps release. Included feedback cannot change scope/budget. Additional words, dimensions, inputs, behavior or features create a new private request/quote and require fresh acceptance/funding.

Paid model tariffs remain **provisional** until authoritative invoice reconciliation. Manifests retain creation-time cost observations. Per-job accounting aggregates actual/provisional authoritative stage reservations and keeps costs nonfinal while future/unresolved liabilities remain; no model assertion finalizes a bill or credits funds.

Migration `008_creative_fulfillment.sql` adds private creative jobs/stages, artifact versions and idempotent change requests. Old saved orders/templates remain readable. Each published version has a version-1 manifest with frozen scope/hash, file hashes/sizes, checks, creation costs, budget, feedback and previous-manifest hashes. Original versions remain downloadable; crash recovery reuses exact persisted versions/timestamps. Customer access requires the original scoped capability and a committed private publication.

Previews use token-authorized POST documents inside an iframe with only `sandbox="allow-scripts"`. Response CSP also forces an opaque origin and blocks network/forms/navigation/parent access. Operator cookies and secrets remain outside the generated page. See [portal UI and access controls](CREATIVE_PORTAL.md).

## Setup and run

```sh
npm ci
npm run services:setup -- --with-tests --with-graphics
npm run services:configure
npm run migrate
docker build -t widemind-creative-sandbox -f docker/creative-sandbox.Dockerfile .
docker image inspect --format '{{.Id}}' widemind-creative-sandbox
```

Copy [the example owner config](../services/tools/examples/creative-owner-config.example.json) to a protected mode-600 file outside both workspace/control directories. Replace absolute paths and set `WIDEMIND_TOOLS_CONFIG`. Map the dedicated OpenAI key through `secret_environment`; configure the actual approved model, context/output bounds and **verified integer tariffs**, then explicitly enable `ai.generate` and owner external-effects policy. No key or actual tariff is bundled. For graphics, enable `creative.graphics`. For software, enable `code.execute` with the inspected immutable image ID and owner compute allocation, then enable `creative.software` with `verification_runtime:"node"`. Its code settings inherit the owner `code.execute` settings; task data cannot choose an image or enable disabled dependencies. Fulfillment never pulls images automatically.

Set `WIDEMIND_DEPENDENCY_MS=30000` for the web and worker: Docker/image readiness checks can exceed the foundation’s default five-second dependency deadline. This remains a free, bounded capability check, not a paid model call. Start `npm run services:start`, `npm run dev`, and separately `npm run worker:dev`. In `/operator/integrations`, approve graphics/software capabilities and prices for `custom-graphic` and `interactive-calculator`. Existing policies keep their old prices. The configured model's worst-case quote must fit each generation slot; choose an appropriate approved allowance/price rather than bypassing accounting. Sandbox opening or verified live funding remains explicit through the existing setup. Hosting and live provider accounts/network permissions are still deployment prerequisites.

## Verification and examples

```sh
npm run check
npm run creative:browser
PYTHONPATH=services/business .venvs/business/bin/python -m unittest discover -s services/business/tests -q
PYTHONPATH=services/tools/src .venvs/tools/bin/python -m pytest services/tools/tests -q
WIDEMIND_CREATIVE_IMAGE=sha256:<local-image-id> npm run creative:integration
```

The full integration runs actual authenticated services, persistent worker, SQLite, native AI usage accounting, graphics renderers, private publication and real networkless Docker JS execution. **PayPal and model HTTP responses are explicitly local protocol fixtures**. Checks include duplicate approvals, restart, actual downloads, immutable revision manifests, an intentionally incorrect calculator formula followed by one funded repair, idle behavior, ownership and browser previews. Synthetic outputs/reports go to ignored `var/creative-integration/`; they are not live customers, payments or profit.

Examples: [graphics source/originals](../services/tools/examples/creative-graphics/) and [calculator source](../examples/creative/software/project-estimator/). Further tests cover exhaustion, pause/cancellation, malformed designs, uncertain effects, old records, revision bounds and expiry. Live operation requires dedicated credentials/accounts, funding, permitted provider access and HTTPS; no such live qualification is claimed.
