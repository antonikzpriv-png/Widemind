# Customer research validation — 2026-10-29

Code revision: `683ff3b4d4e831b661a525f5708d8e33b16992fb`. Started from
`e70220f` and integrated main through `5b6a1a1` before final verification.
This work selects integrated PayPal **service-v1**. The central Stripe
implementation, root Python runtime and `src/modules/business-tools` were not
modified. No live provider credentials, payment or customer job were used.

## Reproduce

Use the separate environments in [integrated setup](INTEGRATED_SETUP.md).
The business environment uses its existing standard-library test runner. Never
install the three `widemind` packages into one interpreter. The central Stripe
Node tests additionally use an isolated root project environment installed from
`requirements.lock` and the root Python project.

```sh
.venvs/tools/bin/python -m pytest -q services/tools/tests
.venvs/economy/bin/python -m pytest -q services/economy/tests
.venvs/business/bin/python -m unittest discover -s services/business/tests -v
WIDEMIND_TEST_PYTHON="$PWD/.venvs/standalone/bin/python" npm run check
npm run integration:smoke
demo_output=$(mktemp -d /tmp/widemind-customer-research.XXXXXX)
.venvs/tools/bin/python scripts/customer-research-demo.py --output "$demo_output"
```

Tools tests find the isolated interpreters at `.venvs/economy/bin/python` and
`.venvs/business/bin/python`; override using `WIDEMIND_TEST_ECONOMY_PYTHON` /
`WIDEMIND_TEST_BUSINESS_PYTHON`. Missing interpreters skip their cross-service
checks with an installation message. **Both checks ran in this validation.**

## Observed results

| Check | Observed result |
| --- | --- |
| Integrated tools | 520 passed, 75 subtests passed, 9 skipped; 53.99 seconds |
| Service-v1 economy | 435 passed; 11.79 seconds; one upstream Starlette/httpx deprecation warning |
| Integrated business | 140 passed; 25.49 seconds |
| Node format, types, tests and build | Passed; 234 tests, zero failures |
| Local PayPal integration smoke | Passed across actual economy, tools, business and persistent Node worker, including restart, private delivery, refund and exact financial totals |
| Customer-specific offline demo | Two different reviewed Markdown documents, private manifests and independent generation receipts |
| Python distributions | Tools and economy wheels/source distributions built; economy migration 006 included |

The nine tools skips are one optional Chromium sandbox check (runtime unavailable)
and eight opt-in Docker execution checks (no explicit test image selected).
Browser sandbox and generated-code execution adapters were not modified.
No live browser verification is claimed.

## Material regression coverage

- Customer questions, acceptance links, bounded multiline search queries,
  publication dates, relevance, source deduplication and literal source offsets.
- Unrelated/insufficient evidence stopping before paid generation; fabricated
  citations, unsupported numbers, missing requirements and omitted conflicts
  blocking delivery.
- Short, long, split and lightly rewritten excerpt dumps; repeated claims,
  section filler and duplicate generic conflict discussions. The full bounded
  set of 24 recorded conflicts fits the synthesis interface.
- Separately reserved corrections, exhaustion before dispatch, and interrupted
  generation recovering its saved result without another model POST.
- Worker fencing, immutable terminal receipts, stable charge identities and
  known bills surviving oversized outputs/diagnostics.
- Actual authenticated economy HTTP verifies a paid child independently posts
  its subtotal; the zero-cost parent neither double-holds funds nor releases
  the child's uncertain remaining liability.
- Actual generated output crosses into the isolated business consumer, retaining
  exact downloadable Markdown bytes and withholding the private manifest.
- New main's discovery cost observations aggregate research child charges;
  a settled zero-cost parent cannot make unresolved child costs appear final.
  Native-ledger tests cover true-up, cancellation, overage and invalid membership.

## What this establishes

Provider responses, model answers and source claims are synthetic controlled
fixtures. The customer demonstration's financial fixture is marked **UNVERIFIED**;
separate cross-service tests use the actual service-v1 economy with explicit test
credit. The PayPal smoke uses a local protocol fixture, not an actual transaction.
These checks establish local behavior and integration, not live accuracy,
provider billing, customer acceptance or earnings.

Citation/date/numeric/similarity screening is heuristic; it cannot prove semantic
entailment, source reliability, factual truth or completeness. Source minimums
count distinct URLs, not independent publishers. Owner-certified AI/search
allocations remain provisional until charge reconciliation. An interrupted
operation without recoverable completion/usage evidence stays unknown and held;
operator review can be necessary. Continuations retain the original monetary cap
and finite stage/correction bounds; execution deadlines apply per invocation.

See [setup and accounting](CUSTOMER_RESEARCH.md),
[reproducible demo](CUSTOMER_RESEARCH_DEMO.md) and
[search setup](../services/tools/docs/SEARCH.md). Live accounts, verified tariffs,
permitted network paths and optional browser dependencies remain owner deployment
prerequisites.
