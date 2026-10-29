# Integrated runtime and economy qualification

This records the initial integration qualification. The subsequent [main audit](MAIN_AUDIT_20261029.md) documents additional repairs and the current test/package results.

The audited Node runtime (`46cacdc`), Python economy (`c1b9a47`) and tools SDK from main (`1ac5f08`) are combined. The registered runtime economy module uses the single protected accounting service. Implementation qualification: `42072f7`; subsequent documentation edits do not alter application behavior. Python package: **0.3.0**; accounting schema: **5**.

## Verified behavior

The normal module loader discovers the economy bridge. Authenticated operator controls explicitly open an existing runtime run with exactly $5 once. Reads and authorization never seed money. The bridge binds each run/action/tool, exact execution limits, provider/account and accounting mode with a persisted HMAC authorization. Monetary RPC fields use canonical decimal strings and integer arithmetic; zero-cost work has an actual zero cap.

Provisional usage posts the known subtotal and remains unresolved until observational tool reconciliation returns the final charge. Later provider events can have new IDs without duplicating cash. Known charges survive invalid/oversized optional outputs; unusable output cannot become successful delivery. Retired tool profiles prevent new execution while preserving reconciliation of existing liabilities. Definitive no-effect recovery retains the cap and enforces three dispatches centrally; the final verified zero-cost closure releases funds without a fourth execution. Lost confirmation responses do not reset counters or permit an extra attempt.

The newer main's Python tools SDK, CLI, adapters, documentation and tests are preserved. Its owner-operated development wallet is not central operating credit. Central environment/policy configuration blocks new paid standalone calls before preparation or dispatch, including attempted standalone overrides. Free tools, receipt replay and observational reconciliation remain available. The SDK is not automatically registered as a Node tool provider; enabling autonomous paid SDK execution still requires an adapter using the central authorization and settlement contracts.

## Local qualification

- **152 Node tests passed**, no failures/skips, through `npm run check`: formatting, TypeScript checking, the complete maintained test suite and compiled build all passed on the combined tree.
- **788 Python tests and 75 subtests passed**, no failures, with **nine prerequisite skips**, in 38.18 seconds using the pinned combined dependency environment. One existing Starlette HTTPX TestClient deprecation warning remains. Eight Docker checks require an explicitly provisioned pinned `WIDEMIND_TEST_CODE_IMAGE`; one SDK browser check requires Chromium with a working OS sandbox. These capabilities were not qualified here; no host-execution or unsandboxed-browser fallback was enabled.
- Five maintained tests run the real Node web/operator routes, runtime engine, Python HTTP process and two real SQLite databases. They cover authenticated idempotent funding, concurrent caps, forged permits, native zero-cost execution, charged failures, provisional/final events across a Python-process restart, negative overages with rejected output, and three-attempt no-effect closure. Tool/provider usage in these tests is explicitly synthetic; this is not a real payment claim.
- `npm run smoke` passed against compiled web/worker entry points, including durable events and worker liveness. `npm run browser:smoke` passed Chromium desktop/phone form login, run creation, resume/pause and operator events with no page errors. This UI check does not qualify the SDK's separate sandboxed browser worker.
- Independent combined review reverified retired-profile startup, final observation identity, exact financial fields, account/mode authorization and lost-response recovery. The standalone SDK authority guard has ten additional regression tests; all 33 targeted SDK boundary checks passed.
- Built and separately installed `widemind-0.3.0-py3-none-any.whl`. Independent review verified all 35 Python/SQL package files against source and the actual non-editable installation, including five accounting migrations, both CLI entry points and central-authority guards. Installed-package RPC checks passed for schema 5, idempotent exact opening credit, zero-cap authorization/dispatch, settlement and integrity. Wheel SHA256: `57f394a5cb80b46c2c25a8cf59a9f50181b9d24667f30edd281a16215b879add` (local qualification artifact).
- `pip check`, production npm advisory scan (zero findings), source compilation and `git diff --check` passed. Remote CI execution and deployment are not claimed.

## Remaining external verification

No real Stripe sandbox or live payment was made. The task environment has no configured Stripe account, API key, webhook signing secret or checkout return URLs. Its observed enforced network policy also excludes `api.stripe.com`. A saved configuration draft requests the needed API destinations and non-secret configuration; it has not been applied or published. Supply `WIDEMIND_STRIPE_ACCOUNT`, `WIDEMIND_STRIPE_API_KEY`, `WIDEMIND_STRIPE_WEBHOOK_SECRET`, `WIDEMIND_STRIPE_MODE=test` and both checkout return URLs privately, then follow the sandbox procedure in [payment setup](ECONOMY_SETUP.md). A reachable webhook ingress or Stripe CLI forwarding is also required. Only verified settled receipts can replenish available operating credit; fixtures and pending receipts do not do so.

Native Git publication works through the task's existing GitHub credential helper. The observed policy excludes `api.github.com`, so PR creation through the GitHub API may remain unavailable; this does not prevent an explicitly authorized normal Git merge/push. Publication status must be checked against the remote main ref, independently of local test results.

This completes the implemented economy/runtime integration and preserves incoming main work. It does not claim real customer work, Gmail delivery, publishing, deployment, provider onboarding, Docker execution or SDK browser rendering were verified. Other independent business branches are preserved; they are not silently imported as alternative runtimes or accounting authorities.

See [runtime economy setup](RUNTIME_ECONOMY.md), [exact RPC contract](ECONOMY_RUNTIME_BRIDGE.md), [payment setup](ECONOMY_SETUP.md) and [operations](ECONOMY_OPERATIONS.md).
