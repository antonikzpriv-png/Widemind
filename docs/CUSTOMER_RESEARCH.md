# Customer research in the integrated services

`research.collect` version 2 produces a customer-specific Markdown brief and a
private evidence manifest through `services/tools`. It uses the **PayPal
service-v1 economy**, with separately authorized search, retrieval, generation
and file children. It has no independent wallet or hidden paid planning call.
Start with [integrated setup](INTEGRATED_SETUP.md) and [tools setup](TOOLS_INTEGRATION.md).
The central Stripe family, schema 5 and `src/modules/business-tools` are unchanged;
never mix their credentials, databases, protocols or migrations with this path.

## Scope and owner setup

The agreed scope contains `topic`, 1–8 distinct `sections` and `min_sources`
(default 2, at most 12). Optional `context` supplies up to 2,000 characters of
customer circumstances; `requirements` supplies up to 20 acceptance instructions.
`as_of` is an ISO date, defaulting to the current UTC date or owner setting.
`max_age_days` optionally requires publication freshness, from 1 to 3,650 days.
The workflow freezes questions, requirement links, dates and queries before work.

```json
{"topic":"Booking tools for a Dublin dental clinic",
 "sections":["Subscription cost","Calendar integration"],"min_sources":2,
 "context":"Two reception staff need appointment scheduling.",
 "requirements":["Compare subscription cost","Identify calendar integration limits"],
 "as_of":"2026-10-29","max_age_days":365}
```

Configure a mode-600 owner file through `WIDEMIND_TOOLS_CONFIG`/`--config`.
This fragment selects capabilities only; **complete the model, network and
account settings before enabling real execution**. It contains no price quote.

```json
{"policy":{"allow_external_effects":true},
 "secret_environment":{"openai-key":"WIDEMIND_OPENAI_API_KEY"},
 "tools":{"files":{"enabled":true},"research.fetch":{"enabled":true},
   "ai.generate":{"enabled":true,"model":"OWNER_VERIFIED_MODEL","secret_ref":"openai-key"},
   "research.collect":{"enabled":true,"source_mode":"curated",
     "source_urls":["https://OWNER-SOURCE.example/first","https://OWNER-SOURCE.example/second"],
     "fetch_mode":"http","max_sources":12,"max_search_queries":4,
     "max_correction_passes":1,"max_output_tokens":4000,"max_prompt_bytes":24000}}}
```

Use current account-verified `model_context_tokens` and integer micro-USD per-million
`tariff.input_microusd_per_million_tokens` / `tariff.output_microusd_per_million_tokens`
(including `tariff.cache_write_microusd_per_million_tokens` when applicable), model endpoint
and token/byte/cost caps in `ai.generate`. Its quote reserves the full configured
context ceiling plus bounded output; bytes are not an exact token estimate.
Use dedicated secrets from owner configuration, never customer inputs. Research
prompt/output settings are clamped to the actual AI child limits; schema overhead
must also fit. Missing credentials or any required capability means unavailable.

For discovery, select `source_mode:"search"` and enable `research.search` with an
owner HTTPS `endpoint` and `provider:"searxng"` or `"brave"`. SearXNG requires
`request_microusd:0` plus an explicit owner-hosting `allocation`; Brave requires
a positive owner-certified fixed request bound and dedicated `secret_ref`.
There are at most **8 queries and 12 fetched sources** per job. Curated mode uses
owner URLs directly and needs no search account. Endpoints, source/network policy,
tariffs and accounts remain owner controlled; task text cannot configure them.

Enable `files` and either `research.fetch` or `browser.read` for `fetch_mode` HTTP
or browser. Browser mode additionally needs the optional Playwright/browser
runtime and its isolated network/proxy setup. Permit the selected source, search
and model hosts through supported policy; use the guarded public-network path
required by the tools transport. Installation does not provision live accounts,
proxy access or browsers. Local network/compute subsidies remain disclosed costs.

## Evidence, synthesis and delivery

Collection retains literal source spans with source IDs, offsets, retrieval dates
and publication-date basis. It normalizes URLs and rejects duplicate/near-duplicate,
unrelated or empty content. Future/stale dates do not establish current coverage;
unknown dates stay explicit, and a freshness requirement cannot be satisfied by
inventing a date. Search-provided dates are weaker observations. Relevant evidence
must cover the agreed questions and requirements before generation starts.

Structured synthesis separates sourced **facts**, **inferences with assumptions**
and **unresolved questions**. Deterministic review checks retrieved citation IDs,
question/source coverage, numbers and units, requirement links, repeated filler,
long copied passages and lightly rewritten/split excerpt dumps. Recorded potential
conflicts need both evidence sides and explicit uncertainty rather than a fabricated
resolution. Footnotes point to the actual retrieved bibliography.

The first generation and each correction use distinct `ai.generate` child IDs,
reservations and measured receipts. `max_correction_passes` is 0–3; the default is
one correction, so the maximum is four paid generations when configured to three.
Failure to pass bounded review produces no completed delivery. Allowance must
cover **worst-case child quotes**, including search and every permitted correction,
not just the expected small token usage from the first draft.

Accepted output has `synthesis_version:2`, passed checks and an actual Markdown
artifact. Business delivery verifies its exact SHA-256 and byte size, frozen scope,
citations and substantive sections. Version-1 excerpt-only results, repeated
excerpt payloads and changed document bytes are rejected. The customer receives
the reviewed document; the full evidence/progress/receipt manifest remains private.

## Authoritative cost and recovery

Only `services/economy` advances to **service-v1 migration 006/schema 6**. Stop old
service processes, back up the service database/WAL and let the matching service
apply its native migration before restarting. Central Stripe remains schema 5;
there is no cross-family conversion or shared balance.

Authenticated gateway/operator `POST /v1/reservations/{parent_reservation_id}/children`
and `GET /v1/reservations/{parent_reservation_id}/children` bind ordinary child reservations
to the running `research.collect` parent. Its original cap is authoritative: child
allocations consume that cap atomically without holding the same money twice.
The parent's actual charge is **zero orchestration cost only**; every paid provider
charge belongs to its child. Closing the parent never releases an unknown child's
remaining liability. Job/payment snapshots aggregate parent and children once,
retain provisional subtotals and keep `costs_final:false` while liability is unresolved.
An accepted brief or positive provisional margin does not prove final profit.

Canonical stage inputs/prompts, child identities and terminal receipts are durable;
parent execution leases fence superseded workers. Free reconciliation observes
saved children/provider recovery and never reserves or generates new work. A
`workflow_resumable` flag queues continuation for a normal next authorized
`business.step`; it does not bypass stop, budget, lease or uncertain-child checks.
Unknown provider outcomes stay held and cannot trigger a blind repeat request.

## Local verification and limits

[Offline demo instructions](CUSTOMER_RESEARCH_DEMO.md) run
`.venvs/tools/bin/python scripts/customer-research-demo.py --output /private/fresh-output`.
Controlled HTTP search/source/model fixtures produce actual report/manifest files.
Owner `offline_fixture_label` marks **both** as offline controlled fixtures. Its
financial fixture is explicitly unverified; synthetic model responses are not
live inference, customer payments, autonomous earnings or source-truth verification.

The separate native accounting test runs the actual service-v1 economy over HTTP
in its **own interpreter**, rather than substituting that financial fixture:

```sh
WIDEMIND_TEST_ECONOMY_PYTHON="$PWD/.venvs/economy/bin/python" \
  .venvs/tools/bin/python -m pytest -q services/tools/tests/test_research_economy_http.py
```

Install both isolated test environments first; missing economy interpreter skips
this check. Sources/models still use controlled fixtures and test credit is not
live funding. Citation, date, numeric and similarity screening is heuristic: it
cannot prove semantic entailment, completeness, publisher reliability or factual
truth. Live model/search accounts, permitted network/proxy paths and any browser
runtime remain deployment prerequisites, not outcomes established by local tests.
