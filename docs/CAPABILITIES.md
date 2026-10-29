# Implemented capabilities and verification status

This table describes the tools slice, not a deployed autonomous business.
All provider responses used by automated tests are explicitly synthetic.
Local HTTP fixtures perform actual socket requests and persist real receipts;
they do not establish provider access, deliverability, pricing or account setup.

| Capability / tool | Implemented behavior | Verified here | External dependencies / limits |
| --- | --- | --- | --- |
| Planning/content `ai.generate` | Configurable OpenAI Responses endpoint/model; strict structured plans/content, output-token ceiling, provider usage and request IDs, saved-response recovery | Synthetic protocol/usage/failure fixtures, actual loopback HTTP through gateway/ledger; actual journal overflow regressions | No live inference. Dedicated key, supported model, verified context ceiling and tariffs required. Unknown requests without a response ID cannot safely be retried |
| Research `research.fetch` | Bounded HTTP extraction of title/text/links, dated URL+hash references, explicit redirect policy | Real loopback HTTP, extraction/redirect/SSRF/cancellation tests | URL research/navigation, no paid search-index API. Public research through a proxy requires an owner-verified SSRF-enforcing egress proxy; fail-closed by default |
| Browser `browser.read` | Real Playwright Chromium worker, rendered page extraction, optional screenshot, intercepted read-only requests, isolated profile/process group | Worker protocol, isolation/policy/resource supervision and real-child cleanup tests | Actual browser rendering **unavailable in this environment**: installed Chromium sandbox cannot start; managed browser download denied. Runtime test skips with reason. Requires a working OS sandbox; no `--no-sandbox` fallback |
| Dedicated Gmail `gmail` | OAuth state/PKCE, actual audience/scope/subject/account verification, profile/list/read/thread/attachments, MIME drafts/sends, correlated recovery | OAuth/MIME/recipient/attachment fixtures, durable restart and accepted-send/lost-response fixtures with no duplicate send | No Google account connected and no email sent. Dedicated OAuth account/scopes and owner-authorized recipients required. Search correlation cannot prove non-delivery; ambiguous outcomes remain uncertain |
| Workspace `files` | Confined create/read/list/delete, SHA-256 artifact metadata, private job-associated deliverable manifests, quotas and atomic writes | Actual files, symlink/race/link/quota/concurrency tests, gateway receipts and CLI smoke | Agent-wide private workspace. Job labels/manifests do not establish customer access rights; no customer-facing delivery endpoint |
| Local graphics `graphics` | Actual SVG primitives/text; optional Pillow PNG rendering | Parsed SVG and actual PNG pixel checks, injection/size limits | Pillow required for PNG; local compute/storage explicitly excluded owner subsidy |
| Generated images `image.generate` | Actual configurable Images request, measured native token usage, private image files and hashes, conservative charge reservation | Synthetic official protocol fixtures, loopback HTTP, usage/artifact/recovery/actual journal-bound tests | No live image generation. Missing key/model/tariffs or genuine exact-settings charge-bound certification leaves it unavailable. API has no max-charge parameter; a reservation alone is insufficient |
| Generated Python `code.execute` | Actual Docker execution with explicit read-only inputs, no network, CPU/RAM/PID/time/output/tmpfs limits, validated artifacts and durable cleanup/recovery | Synthetic contract tests **and real Docker isolation/limit/cancellation/cleanup tests** using one explicitly provisioned immutable official Python image | Python only; no host fallback or automatic pulls. Dedicated audited daemon/image required. Docker shares the host kernel; production hostile-code workers need additional hardening. Host costs are an explicit subsidy |
| Publishing `publish.artifact` | Owner-configured HTTP PUT/WebDAV, immutable action+content key, conditional creation, byte-hash GET verification and durable receipts | Real local HTTP including accepted PUT/dropped response followed by restart and one-PUT recovery | No production or external publication. Dedicated conditional-PUT collection and scoped credentials required. Public URL mapping is owner asserted; receipt verifies bytes at the storage endpoint |
| Shared controls | Atomic integer micro-USD reservations, append-only charges, persisted action/config/account identities, leases, cancellation, private encrypted secret store | Concurrent exhaustion, replay/conflict/restart, known/unknown charges, paid failures, private-state isolation and metadata/output-failure regressions | Operator-driven synchronous gateway. No public API, scheduler, payment settlement, customer/refund holds, invoice integration or autonomous revenue claim |

## Verification commands

See the [dated validation record](TOOLS-VALIDATION.md) for exact executed checks,
results and external dependencies.

```sh
python -m pip install -e '.[test,graphics,browser]'
python -m pytest -q -rs
python -m compileall -q src
WIDEMIND_TEST_CODE_IMAGE='python@sha256:5789aa194f757888f41fa3d48e14b6feaa1b57c0af0b56fcb1372d6474d3bff0' python -m pytest -q tests/test_code_docker.py
```

The normal suite skips optional real Docker tests without an explicit approved
image variable and skips actual Chromium when its sandbox is unavailable. It
does not download images/browsers, send mail, call paid providers or publish
externally. CI's browser/Docker skips must not be reported as live verification.
The explicit Docker test used the local image provisioned for development and
left no owned code containers running. Fixture tests prove contracts and
recovery behavior; held-out or live observations would be needed for claims
about model quality, browser compatibility, delivery or commercial success.

[Tools setup](TOOLS-SETUP.md) links each adapter's exact inputs, owner
configuration, limitations, official protocol sources and recovery instructions.
[AI-PROVIDERS.md](AI-PROVIDERS.md#owner-run-real-provider-smoke) supplies a
documented real-provider smoke path, intentionally not executed here.
