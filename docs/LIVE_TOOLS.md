# Configured Gmail and OpenAI tools

`widemind.tools` implements the production `Tools` contract with dedicated Gmail OAuth, Gmail inbox/send/reconciliation, bounded OpenAI research, source collection, and a durable protected action journal. It does not contain a second ledger. The economy service authorizes and accounts for paid execution.

The default clients make real HTTPS requests when configured and invoked under an active authorized run. Automated verification uses explicitly named fixture transports and example addresses; **no real mail, model inference, payment, or live customer was exercised by these tests**.

## Host construction

The host owns credentials and creates fresh clients against protected state outside the agent workspace:

```python
from widemind.tools import GmailClient, GmailConfig, LiveTools, OpenAIResearch, ResearchConfig

gmail = GmailClient(GmailConfig(
    email_address=dedicated_gmail_address,
    client_id=secret_client_id,
    client_secret=secret_client_secret,
    refresh_token=secret_refresh_token,
))
research = OpenAIResearch(ResearchConfig(
    api_key=secret_openai_key,
    model=configured_model,
    input_micro_usd_per_million_tokens=verified_input_rate,
    output_micro_usd_per_million_tokens=verified_output_rate,
    cached_input_micro_usd_per_million_tokens=verified_cached_input_rate,
    source_urls=tuple(operator_source_pool),
    allowed_source_hosts=tuple(operator_approved_exact_hostnames),
))
tools = LiveTools(
    protected_tool_journal_path,
    economy=economy_client,
    runtime=runtime_client,
    gmail=gmail,
    research=research,
    mode="live",  # or explicit provider-test mode "test"
    allowed_recipients=tuple(controlled_pilot_recipients),
)
```

All three token prices are **required**. There are no guessed price defaults for arbitrary models. The operator must verify that configured rates match the selected model, provider account, and requested default service tier before authorizing a live run. Rates are integer micro-USD per million tokens; round up the combined input charge and output charge to whole micro-USD. Cached input is charged at its configured cached rate rather than the ordinary input price. Provider invoice reconciliation and any pricing correction belong to economy; never silently rewrite a charge.

The standard host uses `OPENAI_API_KEY`, `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET`, `GMAIL_REFRESH_TOKEN`, and `WIDEMIND_GMAIL_ADDRESS`, plus explicit model/pricing/source configuration documented in the host setup. Supply secrets through the environment secret store, not browser forms, model context, command arguments, or Git. Credentials use hidden dataclass repr fields and are never written into the tool action journal. Do not print transport headers or OAuth request bodies in logs.

Gmail requires enabling the Gmail API and obtaining OAuth consent for the dedicated WideMind account with `gmail.readonly` and `gmail.send` scopes. App verification, consent-screen state, refresh-token lifecycle, and provider account onboarding are external prerequisites. Every refreshed token is checked against Gmail's `/profile`; a token for another account cannot send WideMind mail. A configured credential is not evidence of successful provider authorization. Start with controlled recipients.

## Actions and limits

- `gmail.read`: reads a bounded page of at most ten inbox messages. Each body is bounded, normalized into a structured request/accept/opt-out/acknowledgment or a clarification reference, and deduplicated by provider ID by the business module. A finite provider page cursor can continue the batch. Prefer Gmail pushes or a five-minute scheduled wakeup; no model is called to wait for mail.
- `gmail.send`: sends text and an optional validated private attachment from the dedicated account. The journal commits its intent and deterministic RFC Message-ID before sending. A per-action cross-process OS lock spans execution, preparation, reconciliation, confirmation, and settlement; concurrent reconciliation returns in-progress instead of aborting active work or overwriting a completed result. Process death releases the lock automatically. API acceptance returns `delivery_state: accepted`, never fabricated recipient delivery.
- `research.collect`: validates the job's topic, sections, source count and source URLs; collects actual source excerpts; sends only those excerpts and agreed scope to OpenAI; validates returned section ordering/citations against the agreed scope. It does not give the model browsing, account, code-execution, or publishing tools.

`available(action)` is local configuration readiness, not a provider health claim. `validate_arguments(action, arguments)` returns local `ready`/`reason` without network, DNS, or inference. Use it before quoting to identify unsupported source domains or recipients. Business and control-plane capabilities are separate: the gateway independently checks the runtime's action capability, state, cancellation flag, and monotonic cancellation generation immediately before external sends/inference. A stop followed by resume cannot hide cancellation of an earlier attempt.

Defaults are 500 new Gmail actions and 50 new research attempts per UTC day per gateway journal; configure `max_gmail_actions_per_day` and `max_research_actions_per_day` to suit provider quotas and the funded run. Counter admission and the new action insertion share a SQLite transaction. Replaying results and reconciling unresolved effects do not execute inference or send again. Account for scheduled mailbox reads when choosing the quota. Hosted compute/network and provider subscription/shared costs must be allocated by the run's economy policy; these are not invented as free model credits.

The HTTPS transport preserves inherited proxies and CA trust. It refuses HTTP redirects, caps response bytes, reads bounded chunks, and enforces a monotonic whole-request deadline with shrinking socket timeouts, including slow-drip responses. Gmail uses an overall deadline across OAuth/profile and a bounded inbox page. Research source collection has an overall deadline, bounded DNS lookups (at most four outstanding daemon resolutions), and runtime cancellation checkpoints between sources. OpenAI has an explicit output-token cap and provider timeout.

## Grounded research and source security

Use `scope.source_urls` for job-specific sources, or the configured fallback source pool. URLs must be unique HTTPS sources with sufficient count. Source hosts must be in the operator's exact hostname allowlist; if no explicit allowlist is supplied, only hostnames from the configured source pool are allowed. Then DNS must resolve only to global/public addresses, the port must be HTTPS 443, and redirects are refused. A customer cannot grant new network permissions by supplying a URL. Keep the host's outbound policy restricted to approved services/sources as a further boundary; an HTTP proxy can resolve independently of the local DNS check.

Only bounded HTML/plain-text/JSON evidence is extracted; PDF and other formats need a separately configured extraction adapter. Scripts, style, templates, and noscript content are omitted from HTML evidence. Input pages and messages are task data, not authority to change budgets, credentials, or permissions. The prompt contains only scope and evidence, and no billing/OAuth secrets.

The **complete serialized OpenAI request**, including schema enums containing source URLs, messages, metadata, and conservative framing overhead, is counted against the input and cost bounds. The maximum cost assumes ordinary-price input and full output cap; observed usage applies actual cached-token pricing. Oversized input, unapproved hosts, missing sources, insufficient authorization, or cancellation fail before paid inference. A billed response that fails section/citation checks remains charged and does not become a deliverable. Citation consistency is checked; factual accuracy, source freshness/licensing, and sellable quality still require the configured review process.

OpenAI requests use stored chat completions with action metadata so a lost response can be inspected without repeating inference. This stores customer research input/output in the configured OpenAI account; agree retention/data terms before a live pilot. Reconciliation queries stored completion evidence, validates the unique action metadata and usage, and renders against the **original preserved excerpts**, not refetched pages. Missing/malformed usage is uncertain and retains the authorization. API/provider account support for stored completions must be qualified with the configured account before live operation.

## Spending and interruption

The gateway records the exact run, job, arguments, action ID, maximum, and reservation before any provider action. For research it calls:

```python
economy.begin_execution(
    run_id=run_id, job_id=job_id, reservation_id=reservation_id,
    action_id=action_id, maximum_micro_usd=maximum_micro_usd,
)
economy.settle_execution(
    reservation_id=reservation_id, action_id=action_id,
    actual_micro_usd=observed_usage_cost,
    evidence_reference=provider_completion_id, provisional=False,
)
```

The returned authorization must match every identity and maximum before even collecting source pages. The same owner-bound authorization is rechecked idempotently immediately before the paid POST, so refunds, coverage changes, and cost-limit breaches during preparation block inference. A reservation from another run/job cannot pay for this action. One reservation belongs to one execution attempt. The gateway posts actual observed usage on success **and billed failure**, retains unknown liability, and never repeats an uncertain inference request. Unexpected charges above the allowance remain visible for economy's breach/exhaustion handling.

Provider usage/results are persisted before financial settlement. If settlement fails, restart recovery settles the same evidence idempotently without another model call. If the process dies before the preserved research context is committed, provider dispatch was structurally unreachable: reconciliation releases the unclaimed reservation, or finalizes zero only when economy confirms that this exact execution owned it and had no charge. Another action's reservation or uncertain liability is never released by guessing.

The journal mode is pinned; live and test journals cannot be reused across modes. Keep the protected journal and economy state in consistent backups. Retain its `.actions` lock directory while processes are running; remove old locks only during a quiescent, reviewed retention/cleanup operation, never to force an uncertain action retry. Do not delete an uncertain intent to make an action retryable. A paid retry requires a fresh approved budget/plan, not a consumed reservation. Free Gmail calls use a zero incremental-cost ceiling; a provider charging for those operations requires an explicit reservation/pricing extension first.

## Actual delivery evidence

Gmail Sent-folder presence proves a send was accepted, **not that the customer received or read it**. A timeout is reconciled by deterministic RFC Message-ID lookup and remains accepted until evidence exists; it never triggers another send.

For incoming replies, the Gmail boundary checks the top Gmail `Authentication-Results` header for aligned DMARC success, then matches the exact intended recipient and original `In-Reply-To` Message-ID. Bare From, SPF alone, a lower injected result header, an unrelated reply, or a different recipient cannot confirm delivery. This is email-domain authentication evidence, not a general customer account login. Providers/domains unable to furnish that evidence need another reviewed confirmation path.

Private attachment messages include an instruction to reply in-thread with:

```json
{"type":"acknowledge","action_id":"the-actual-delivery-action-id"}
```

An ordinary verified in-thread acceptance reply also acknowledges receipt of that particular message. The gateway returns `delivery_updates` with the owning action/job IDs so the business runtime can reconcile its effect and wake the order. Business still decides whether evidence meets the agreed fulfillment state; no model assertion creates earned revenue.

`confirm_delivery(action_id=..., recipient_email=..., evidence_reference=...)` is a **trusted control-plane method**, not a public API or model tool. Its caller must authenticate actual customer acknowledgment or other reliable delivery evidence before using it. It checks acceptance and recipient binding, records the private evidence reference, and changes only that message's transport state. Root/operator authentication alone is not proof of customer receipt. No automated code should call it merely to unblock an order.

## Verification

```sh
python -m unittest discover -s tests -p 'test_tools*.py' -v
```

Tests inject in-memory fixture transport/runtime/economy objects; unexpected URLs fail rather than touching the network. They cover scoped source collection, complete request bounds, cached usage, billable failures, swapped authorizations, insufficient funds, cancellation between sources and inference, unknown send/model outcomes, settlement restart recovery, pre-dispatch process death, controlled recipients, OAuth identity mismatch, forged sender metadata, recipient-bound delivery replies, quotas, source-host restrictions, slow-drip timeouts, mode pinning, duplicate effects, and credential separation. Live provider qualification remains a separate controlled pilot with actual account credentials and independently verified records.
