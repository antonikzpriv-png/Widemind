# AI content, planning and image providers

`ai.generate` and `image.generate` make actual OpenAI-compatible HTTP requests
through the trusted gateway transport. They have no SDK dependency, automatic
retry, remote tool execution, or credentials supplied in model arguments.
Configuration and secrets must be outside WideMind's writable workspace. The
gateway owns authorization, reservations, receipts, run balances and recovery.
Generated text, plans and images are private **untrusted task data**. A suggested
tool name in a plan grants no permission to run it or change owner configuration.

## Verification status

| Capability | Verified here | Live status / dependency |
| --- | --- | --- |
| Responses request shape, raw message parsing, structured schema | Official SDK types plus synthetic fixtures and controlled localhost HTTP | No paid inference performed; owner must configure model, account, endpoint and tariffs |
| Measured token accounting, integer rounding, positive quote | Local tests | Provisional owner tariffs; reconcile with provider invoice |
| Cancellation, missing usage, timeout and restart recovery | Local failure tests | Unknown paid outcomes retain liability; retrieval requires a known stored Responses ID |
| Images request, inline base64 receipt and workspace artifacts | Official SDK types plus synthetic fixtures and controlled localhost HTTP | No live image generation; requires supported model and certified charge ceiling |
| Image generation retrieval / provider maximum-cost field | Absent from the verified SDK contract | Unknown requests cannot be retried automatically; generation is unavailable without owner certification |

The fixtures match official raw API types and SDK tests. They are locally
constructed test data, **not live response recordings**. No configured provider
credentials were present, and this environment's destination policy does not
allow `api.openai.com`. Official GitHub sources were permitted; no denied
destination was bypassed.

## Owner configuration

Inside the root configuration's `tools` object, configure `ai.generate` and/or
`image.generate` independently. Each needs `enabled: true`, a supported explicit
`model`, `secret_ref`, and integer `tariff` rates. The `endpoint` defaults to
`https://api.openai.com/v1`. It must use HTTPS and contain no credentials, query,
or fragment. For controlled localhost fixtures only, `allow_local_http: true`
permits HTTP to `127.0.0.1`, `localhost` or `::1`.

Example tool entry, with **illustrative rates/model limits, not a price quote**:

```json
{
  "enabled": true,
  "endpoint": "https://api.openai.com/v1",
  "model": "OWNER_SELECTED_RESPONSES_MODEL",
  "secret_ref": "widemind-openai",
  "model_context_tokens": 128000,
  "max_input_bytes": 16000,
  "max_output_tokens": 1000,
  "max_output_bytes": 64000,
  "max_response_bytes": 2000000,
  "maximum_microusd": 1000000,
  "tariff": {
    "input_microusd_per_million_tokens": 1000000,
    "output_microusd_per_million_tokens": 4000000,
    "cache_write_microusd_per_million_tokens": 1000000
  }
}
```

`model_context_tokens` is the model's documented hard context ceiling. The text
quote conservatively reserves the cost of that **entire ceiling** at the maximum
configured input/cache-write rate, plus the requested `max_output_tokens` at the
configured output rate. UTF-8 byte counts are resource limits, not an assumed
tokenizer. OpenAI enforces `max_output_tokens` for visible **and reasoning**
tokens. The adapter sends no paid built-in tools. Owners must verify their
selected model's context ceiling and worst applicable rates, including cache
writes and their account's service tier. An inaccurate owner limit, tariff or
compatible endpoint invalidates the financial bound. Requests with a quote over
the caller or owner maximum fail before dispatch.

Actual provisional charge uses measured native input/output counts:

```text
ceil((input_tokens * max(input_rate, cache_write_rate)
      + output_tokens * output_rate) / 1,000,000) micro-USD
```

Arithmetic is integer throughout. This deliberately makes no cached-input
discount assumption. It is a configured-tariff operating charge, not a provider
invoice. The exact native usage object is retained for reconciliation. Missing,
noninteger, negative or inconsistent token counts mean **unknown cost**, never
zero; the action remains uncertain. Even refusals, malformed output, cancellation
after dispatch and HTTP errors may be billable.

### Text and structured plans

`AIAdapter` in `src/widemind/tools/ai.py` exposes `ai.generate`:

| Argument | Meaning |
| --- | --- |
| `prompt` | Required nonempty UTF-8 text; combined prompt/instructions/schema must fit owner `max_input_bytes` |
| `instructions` | Optional task instructions, bounded with the prompt |
| `purpose` | `content` (default) or `plan` |
| `format` | `text` (default for content) or `json`; plan requires JSON |
| `schema` | Required for content JSON; plans use the built-in bounded schema |
| `max_output_tokens` | Positive integer no greater than owner cap or model context ceiling |
| `max_output_bytes` | Positive integer no greater than owner cap; oversized results retain measured charge |
| `max_cost_microusd` | Positive integer no greater than owner cap; must cover the conservative quote |

Plans have `objective`, up to 20 `steps` (each containing `description`,
`suggested_tool`, `success_criterion`) and up to 20 `risks`. They are proposals for
the trusted worker to evaluate. They never directly update budgets or invoke
tools. Content JSON supports the explicit bounded schema subset: object (all
properties required, `additionalProperties:false`), array (`maxItems` <=100),
string (`maxLength`), integer/number (finite `minimum`/`maximum`), boolean and
null; optional `enum` and descriptions. Depth is at most eight, with at most 64
properties per object. References, arbitrary schema URLs and other keywords are
rejected. Both provider strict mode and local output validation are used. The
selected model must support this structured-output subset.

Example request file:

```json
{"prompt":"Propose a small customer research service and a delivery plan.","purpose":"plan","max_output_tokens":700,"max_cost_microusd":150000}
```

Results contain `text`, `response_id`, `purpose` and, for structured output,
`json`, with measured `Usage`. Responses are created with `store:true` to permit
recovery; this has provider retention implications for private customer content.
Both the raw bounded receipt and `x-request-id` are saved before output parsing,
followed by the response body `id`. The latter is distinct from the request ID.
Recovery uses the saved receipt or a bounded `GET /responses/{id}`. A request ID
alone is not enough to retrieve or safely repeat unknown generation.

### Images and the charge-bound dependency

`ImageGenerationAdapter` in `src/widemind/tools/image_generation.py` exposes
`image.generate`. Inputs are only `prompt`, `n` (default 1; owner maximum 1..4),
`output_prefix` (default `generated/image`, relative workspace path), and
`max_cost_microusd`. Caller-supplied credentials, model, URLs, quality, size and
tariffs are rejected.

The owner chooses a supported explicit model, fixed `size` (`1024x1024`,
`1536x1024` or `1024x1536`), and `quality` (`low`, `medium` or `high`). The adapter
requests nonstreaming opaque PNGs, with no legacy `response_format` argument.
GPT image models return inline `b64_json`; arbitrary returned URLs are never
downloaded. Images' native token usage is priced with the same conservative
integer rates above, and successful image counts are also reported. Configure
the largest relevant text/image input and output rates in those fields. Missing
native token usage remains uncertain even when base64 images were returned.

**The Images API has no `max_output_tokens` or maximum-cost parameter.** Fixed
dimensions, quality, prompt bound and image count constrain work, but the SDK
does not establish a numerical billing ceiling. Consequently this adapter is
unavailable until the owner supplies a documented, certified maximum charge for
the exact settings and prompt bound. Certification is an explicit external
dependency, **not a provider-enforced cost cap or a cost guarantee inferred from
image dimensions**. If the owner cannot establish that bound, leave this tool
disabled. Do not copy synthetic test bounds into a real account.

Additional image configuration (also needs model, secret, tariff and
`maximum_microusd`; `model_context_tokens` is not needed for images):

```json
{
  "size": "1024x1024",
  "quality": "low",
  "max_input_bytes": 16000,
  "max_images": 1,
  "max_image_bytes": 8000000,
  "max_response_bytes": 12000000,
  "charge_bound": {
    "certified": true,
    "model": "OWNER_SELECTED_SUPPORTED_IMAGE_MODEL",
    "size": "1024x1024",
    "quality": "low",
    "max_prompt_bytes": 16000,
    "maximum_microusd_per_image": 100000,
    "evidence": "REPLACE with the owner-verified provider/account charge-bound reference and date"
  }
}
```

The illustrative maximum is **not verified pricing**. The quote is `n *
maximum_microusd_per_image`; the certificate must match all listed settings.
Actual reported costs above the reserved amount are still surfaced to the
gateway. The gateway must stop further paid work and reconcile the discrepancy.
`max_image_bytes` bounds each decoded image; `max_response_bytes` bounds the
entire JSON/base64 receipt. The default response bound accommodates the owner's
maximum number of configured-size byte buffers, up to 64 MB. Explicit smaller
bounds can cause a billable uncertain response-limit failure.

The adapter saves the full bounded raw receipt and request ID before decoding
or writing. Files use deterministic `output_prefix-<action hash>-<index>.png`
names. Recovery verifies existing bytes against the saved receipt before reuse;
it never overwrites a conflicting file. Fewer returned images count as partial
deliverable work with the native measured charge. A timeout without a saved
receipt remains uncertain: Images has **no generation retrieval endpoint**.
Reconcile account usage/support records using the stored request identity and
never automatically resubmit that paid request. `X-Client-Request-Id` is a
correlation value, **not an idempotency guarantee**.

## Owner-run real-provider smoke

These commands were **not executed against a real provider** in this task.
They assume the integrated CLI is installed (`pip install -e .`), owner policy
authorizes external paid work, network policy permits the chosen endpoint, and
`/owner/config.json` contains an enabled, accurately priced `ai.generate` entry
above under `tools`. Root configuration also needs `control_dir`, a separate
`workspace_dir`, and `policy` configured for the authorized runtime profile.
For an operator test use `"policy":{"profile":"sandbox","network":{"trusted_fixture_origins":[],"public_proxy_guarded":false}}`.
Here `sandbox` identifies the operator test profile; OpenAI inference is still
real and billable. The root `tools` object should contain
`"ai.generate":{...the tool entry above...}`. Use a dedicated API key with an
account spend limit. The prompt below contains
no private customer data.

```bash
widemind --config /owner/config.json secret set widemind-openai
widemind --config /owner/config.json init --run-id provider-smoke
python - <<'PY'
import json
from pathlib import Path
Path('/owner/ai-smoke.json').write_text(json.dumps({
    'prompt': 'Return one short sentence describing a useful research brief.',
    'max_output_tokens': 80,
    'max_cost_microusd': 150000
}))
PY
widemind --config /owner/config.json execute ai.generate --run-id provider-smoke --action-id 6cb8a9b5-6d87-4cab-b5ce-22646b62c657 --input-file /owner/ai-smoke.json
widemind --config /owner/config.json status
```

The illustrative text settings above quote 128,320 micro-USD for 80 output
tokens, so this smoke requires that much spendable credit even though measured
usage should be smaller. Replace model limits and tariffs with current verified
account values before running. Inspect the private receipt for actual token
counts, `x-request-id`, response ID and provisional charge. For an interrupted
request run `widemind --config /owner/config.json reconcile
6cb8a9b5-6d87-4cab-b5ce-22646b62c657`; reuse the same action identity for recovery.
Use a fresh UUID for each intentionally new paid smoke, never a new UUID merely
to work around an uncertain outcome. Image smoke is enabled only after genuine
charge-bound certification: replace the tool name with `image.generate` and use
`{"prompt":"A simple green leaf on white","n":1,"output_prefix":"smoke/leaf","max_cost_microusd":100000}`
in a separate input file and intentionally new action.

## Reproducible local checks and official references

```bash
python -m pip install -e '.[test]'
python -m pytest -q tests/test_ai.py tests/test_image_generation.py
```

Contract verification used official `openai/openai-python` commit
[`35fc9f33a769858475f2f512112c4256480fff41`](https://github.com/openai/openai-python/commit/35fc9f33a769858475f2f512112c4256480fff41),
dated 2026-10-28. Relevant pinned sources:

- [Responses create/retrieve and output-token bound](https://github.com/openai/openai-python/blob/35fc9f33a769858475f2f512112c4256480fff41/src/openai/resources/responses/responses.py).
- [Responses request `store` and parameters](https://github.com/openai/openai-python/blob/35fc9f33a769858475f2f512112c4256480fff41/src/openai/types/responses/response_create_params.py).
- [Structured text configuration](https://github.com/openai/openai-python/blob/35fc9f33a769858475f2f512112c4256480fff41/src/openai/types/responses/response_format_text_json_schema_config_param.py).
- [Native Responses usage](https://github.com/openai/openai-python/blob/35fc9f33a769858475f2f512112c4256480fff41/src/openai/types/responses/response_usage.py).
- [Images generation parameters and current model retirement notes](https://github.com/openai/openai-python/blob/35fc9f33a769858475f2f512112c4256480fff41/src/openai/types/image_generate_params.py).
- [Images response including optional native usage](https://github.com/openai/openai-python/blob/35fc9f33a769858475f2f512112c4256480fff41/src/openai/types/images_response.py).
- [Official synthetic raw Responses fixture shape](https://github.com/openai/openai-python/blob/35fc9f33a769858475f2f512112c4256480fff41/tests/test_large_payload_contract.py).
- [Official synthetic Images payload shape](https://github.com/openai/openai-python/blob/35fc9f33a769858475f2f512112c4256480fff41/tests/test_image_safety_identifier.py).

That revision marks `gpt-image-1` shut down on 2026-10-23 and DALL-E 2/3 retired
on 2026-05-12. No default image model is hardcoded; choose a currently supported
model from the provider's current account documentation. These source checks
verify request/response contracts, not account access, live model availability,
pricing or a billing bound.
