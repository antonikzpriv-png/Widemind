# Foundation business tools

The discoverable `business-tools` Node module supplies `gmail.read`, `gmail.send`, and `research.collect` through a private Python subprocess. It also routes `commerce.checkout` and `commerce.fulfill` to the trusted commerce delegate. The normal Node runtime still owns action scheduling, reservations, durable receipts and settlement against the existing Python economy. This module never imports the standalone SDK development wallet or writes financial entries.

The module is disabled until `WIDEMIND_BUSINESS_TOOLS_PROFILE` is explicitly `provider-test` or `live`, and requires the `foundation` runtime profile. Constructors perform no requests, funding, mail, inference or journal creation. Missing or malformed provider settings produce unavailable capabilities. Local readiness is not provider qualification. No deployed mock mode is supplied; automated tests inject explicitly named offline transports.

## Configuration

Install the existing Python package and Node application normally. Use the same protected economy URL and gateway identity as the foundation economy bridge. Supply secrets through the deployment secret store, never tool input, shell arguments, public configuration or Git.

| Variable | Meaning |
| --- | --- |
| `WIDEMIND_BUSINESS_TOOLS_PROFILE` | `provider-test` maps to central `sandbox`; `live` maps to central `live` |
| `WIDEMIND_BUSINESS_TOOLS_JOURNAL` | Absolute private SQLite path outside the agent workspace and distinct from the runtime/economy databases |
| `WIDEMIND_BUSINESS_PYTHON` | Trusted interpreter path; defaults to `python3`; no shell or custom arguments |
| `WIDEMIND_ECONOMY_URL`, `WIDEMIND_GATEWAY_TOKEN` | Existing protected economy service and gateway identity |
| `WIDEMIND_BUSINESS_ALLOWED_RECIPIENTS` | JSON address list; mandatory and nonempty for provider-test execution |
| `WIDEMIND_BUSINESS_GMAIL_ACCOUNT` | Exact central profile account reference for both Gmail actions |
| `WIDEMIND_GMAIL_ADDRESS` | Dedicated mailbox address, verified through Gmail `/profile` |
| `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET`, `GMAIL_REFRESH_TOKEN` | Dedicated OAuth credentials; Gmail readonly/send scopes |
| `WIDEMIND_BUSINESS_OPENAI_ACCOUNT` | Exact central profile account reference for research |
| `OPENAI_API_KEY`, `OPENAI_MODEL` | Configured OpenAI project and supported stored-completion model |
| `OPENAI_INPUT_MICRO_USD_PER_MILLION_TOKENS`, `OPENAI_OUTPUT_MICRO_USD_PER_MILLION_TOKENS`, `OPENAI_CACHED_INPUT_MICRO_USD_PER_MILLION_TOKENS` | Required exact integer prices verified by the operator; cached price may be zero and cannot exceed ordinary input |
| `OPENAI_MAX_OUTPUT_TOKENS`, `OPENAI_MAX_INPUT_BYTES` | Defaults 1800 and 24000; finite provider/output and complete-request bounds |
| `WIDEMIND_RESEARCH_SOURCE_URLS`, `WIDEMIND_RESEARCH_ALLOWED_HOSTS` | JSON fallback HTTPS URL pool and exact hostname permissions; absent explicit hosts, only pool hosts are permitted |
| `WIDEMIND_BUSINESS_PUBLIC_PROXY_GUARDED` | Set exactly `true` only when the inherited proxy enforces public destinations at connect time; required for public-source requests through a proxy |
| `WIDEMIND_BUSINESS_GMAIL_DAILY_LIMIT`, `WIDEMIND_BUSINESS_RESEARCH_DAILY_LIMIT` | Durable new-attempt quotas, defaults 500 and 50 per UTC day |

Configure `WIDEMIND_RUNTIME_TOOL_PROFILES` in the central economy with exact tuples: Gmail actions use provider `gmail`, research uses `openai`, and each account/mode must match the settings above. Commerce uses its separate documented `commerce` profile and configuration. The Node module passes the protected runtime database path internally for read-only cancellation, policy and frozen-order checks; tool input cannot choose this path.

## Boundaries and recovery

Actions and reservations must match their exact run, action, tool and limits. The gateway asks `/v1/runtime/verify` to check the opaque reservation, then calls its one-shot dispatch gate immediately before provider request bytes, after source collection, DNS and TLS. Research also checks the unfinished order's frozen quote/scope/cost, current business policy and current verified commerce payment at this boundary. A customer refund, reduced allowance, revoked capability or stop followed by resume blocks the earlier attempt. Explicit free Gmail calls require a zero incremental-cost ceiling and still use central authorization.

Research input is `{topic,sections,minSources,sourceUrls?}`. The conservative total token allowance must cover the complete serialized provider request plus 2048 framing bytes and the configured full output cap; the business service proposes 30000 tokens with the default input/output bounds. The gateway returns validated ordered sections with `sourceUrls`, original source excerpts and a checked Markdown artifact. Citation consistency does not establish factual accuracy, licensing or profitable quality.

Gmail send input is `{to,subject,body,attachment?:{filename,mediaType,content,checks?}}`. Acceptance returns `deliveryState: accepted`; it does not prove receipt. Attachment messages include an instruction to reply in-thread with `{type:"acknowledge",action_id:"actual-delivery-action-id"}` (`actionId` is also accepted). Inbox reads return bounded structured messages and independently verified `deliveryUpdates`: the top Google authentication result must contain one actual aligned DMARC pass outside comments/quoted properties, and the explicit acknowledgment must match the attachment's action ID, intended sender and original Message-ID. Negative replies, opt-outs, ordinary correspondence, forged From headers and unrelated acknowledgments cannot confirm delivery.

The protected journal commits immutable intent and exact original research prices/evidence before dispatch. A per-action OS lock prevents concurrent replay/recovery. The gateway freezes `result.executionStartedAt` at successful central dispatch, before inference, and preserves that timestamp during later recovery so delivery deadlines do not move. Known usage is retained and charged even when generation or artifact checks fail. Lost responses remain uncertain and retain the central reservation; observation of a stored completion or deterministic Gmail Sent identity never resends or repeats inference. Reconciliation uses original prices and excerpts; missing original pricing stays uncertain for reviewed recovery. A missing/stale journal cannot declare an existing central dispatch free. Provider result/usage is persisted before returning the receipt for central settlement. Killing a subprocess cannot leave a background HTTP sender running; uncertain accepted effects remain journaled for recovery.

The shared SDK HTTP transport preserves inherited proxy/CA configuration, pins validated public DNS destinations, refuses implicit redirects, and bounds DNS, connection, TLS, headers and response reads. Its late admission callback runs once before origin request bytes. Public proxy destination enforcement still requires the explicit deployment guard. Paths are logical checks; OS ownership, isolated agent execution, TLS, backups and secret access remain deployment requirements.

## Local verification

`node --test test/business-tools.test.ts` checks discovery, default-disabled operation, missing/malformed settings, journal separation and cancelled dispatch. `python -m pytest -q tests/test_business_tools_runtime.py tests/test_business_tools_http.py tests/test_http.py` checks the actual central verification/ledger with explicitly offline provider fixtures, usage settlement, uncertain recovery at original prices, late refunds/cancellation, quota/replay, authenticated acknowledgment, charged quality failure, and the actual HTTP admission boundary. These checks exercise no live Gmail, OpenAI, Stripe payment or customer.
