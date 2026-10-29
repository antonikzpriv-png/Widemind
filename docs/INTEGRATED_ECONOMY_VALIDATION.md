# Integrated runtime and economy qualification

The audited Node runtime (`46cacdc`) and audited Python economy (`c1b9a47`) are combined, with the registered runtime economy module connected to the single protected accounting service. Implementation qualification: `c952a1a`; later documentation changes do not alter application behavior. Python package: **0.3.0**; accounting schema: **5**.

## Verified behavior

The normal module loader discovers the economy bridge. Authenticated operator controls explicitly open an existing runtime run with exactly $5 once. Reads and authorization never seed money. The bridge binds each run/action/tool, exact execution limits, provider/account and accounting mode with a persisted HMAC authorization. Monetary RPC fields use canonical decimal strings and integer arithmetic; zero-cost work has an actual zero cap.

Provisional usage posts the known subtotal and remains unresolved until observational tool reconciliation returns the final charge. Later provider events can have new IDs without duplicating cash. Known charges survive invalid/oversized optional outputs; unusable output cannot become successful delivery. Retired tool profiles prevent new execution while preserving reconciliation of existing liabilities. Definitive no-effect recovery retains the cap and enforces three dispatches centrally; the final verified zero-cost closure releases funds without a fourth execution. Lost confirmation responses do not reset counters or permit an extra attempt.

## Local qualification

- **152 Node tests passed**, no failures/skips, through `npm run check`: formatting, TypeScript checking, the complete maintained test suite and compiled build all passed.
- **430 Python tests passed**, no failures/skips, 15.33 seconds, using the pinned dependency environment. One existing Starlette HTTPX TestClient deprecation warning remains; no checks were suppressed.
- Five maintained tests run the real Node web/operator routes, runtime engine, Python HTTP process and two real SQLite databases. They cover authenticated idempotent funding, concurrent caps, forged permits, native zero-cost execution, charged failures, provisional/final events across a Python-process restart, negative overages with rejected output, and three-attempt no-effect closure. Tool/provider usage in these tests is explicitly synthetic; this is not a real payment claim.
- `npm run smoke` passed against compiled web/worker entry points, including durable events and worker liveness. `npm run browser:smoke` passed real Chromium desktop/phone form login, run creation, resume/pause and operator events with no page errors.
- Independent combined review reverified retired-profile startup, final observation identity, exact financial fields, account/mode authorization and lost-response recovery. Targeted current checks passed: 43 Python RPC tests and 39 Node client tests.
- Built and separately installed `widemind-0.3.0-py3-none-any.whl`. The wheel includes exactly the five accounting migrations and the intended Python package, with no TypeScript namespace packages. Installed-package RPC checks passed for schema 5, exact opening credit, zero-cap authorization/dispatch, settlement and integrity. Wheel SHA256: `7a410d37ef70a9319355f65eea2ae0c8a23ed7cacde0b29e20ac64ebe263c58a` (local qualification artifact).
- `pip check`, production npm advisory scan (zero findings), source compilation and `git diff --check` passed. Remote CI execution and deployment are not claimed.

## Remaining external verification

No real Stripe sandbox or live payment was made. No Stripe account/key/signing-secret/return URLs were configured in the task environment. Stripe and GitHub API destinations remain blocked in the observed enforced network policy. A configuration draft adds the required destinations and non-secret account/redirect requirements; saving it does not apply or publish those changes. Credentials must be entered through private environment configuration, never committed or pasted into chat.

The task's GitHub authentication also began rejecting native Git access, including read-only access to both repositories, with an invalid username/token response when using its existing SDK credential. Remote publication requires that existing GitHub authorization to be refreshed. These external access failures are separate from the passing local code qualification; source commits and the local main merge remain reviewable.

The separate real-tools/business branches were preserved rather than blindly merged: the real-tools branch currently owns another ledger and must be adapted to the central authority before enabling it. This integration completes the economy/runtime connection; it does not claim that independent customer work, Gmail, publishing, deployment or real provider onboarding have been verified.

See [runtime economy setup](RUNTIME_ECONOMY.md), [exact RPC contract](ECONOMY_RUNTIME_BRIDGE.md), [payment setup](ECONOMY_SETUP.md) and [operations](ECONOMY_OPERATIONS.md).
