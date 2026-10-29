# Customer conversation qualification

Base: integrated main `e70220f1f7b38771cf619fba1d1241298763b922`. Qualified implementation: `45268caa4f5db84df23bccf1217c97977a8f3453`; subsequent documentation/environment-example edits do not change the qualified application behavior.

The change extends the existing PayPal business service, Gmail transport and customer portal. It adds no accounting authority, runtime scheduler, provider account or live payment claim. Stripe deployment/source paths and their credential families remain separate.

## Executed checks

| Check | Result |
| --- | --- |
| `npm run check` with the configured root fixture Python | **240 Node tests passed**, zero failed/skipped; formatting, TypeScript and compiled build passed |
| Integrated economy service suite | **414 passed**, one existing Starlette HTTPX deprecation warning |
| Integrated tools service suite | **397 passed, 81 subtests passed, 11 optional prerequisite skips** |
| Final business service suite | **139 tests passed**, including 28 realistic conversation scenarios and nine actual host/portal cases |
| `npm run integration:browser` | Passed against actual local economy/tools/business processes, persistent worker and desktop/phone Chromium forms; explicit local PayPal protocol fixture; one capture and one refund reconciled, worker hard restart, customer isolation and private SVG delivery verified |
| Preserved Stripe `npm run business:demo -- --fixtures` | Three scenarios passed: interrupted delivery/reconciliation, pause/unavailable tools and real central-ledger exhaustion; synthetic provider/customer/payment data only |
| Business/tools wheels | Both built successfully; conversation controller/schema/poller/migration 8 and Gmail wrapper included |
| Source/format checks | Python compilation, Prettier and `git diff --check` passed |

Commands used in the checkout:

```sh
npm ci
npm run services:setup -- --with-tests
npm run services:test
WIDEMIND_TEST_PYTHON=/workspace/.venvs/widemind-economy/bin/python npm run check
npm run integration:browser
WIDEMIND_TEST_PYTHON=/workspace/.venvs/widemind-economy/bin/python npm run business:demo -- --fixtures
PYTHONPATH=services/business .venvs/business/bin/python -m unittest discover -s services/business/tests -v
```

The explicit Python path above selects the task's pre-existing root Stripe fixture environment; it is not a required deployment path. Integrated services remain in their own `.venvs/economy`, `.venvs/tools` and `.venvs/business` environments.

## Focused coverage and review

Maintained scenarios cover multi-message clarification, complete scope/quote proposals, negotiation floors, capacity refusal, ambiguous acceptance, exact current version/digest, changed terms, pending-message acceptance fencing, cross-customer tokens, same-customer thread isolation, replay/restart and conflicting reused message identities. Model-unavailable/budget-exhausted cases create no commitment. Malicious extra authority fields fail schema validation; “I paid” leaves authoritative payment records unchanged.

Unknown model, reply and follow-up outcomes recover through explicit observational reconciliation and cached results, without another paid model call or provider send. Reduced policy budgets are fenced before model admission. Pause during model execution preserves the message for resume; opt-out requires neither a model nor spending credit and is rechecked after send preparation immediately before dispatch. Gmail acceptance remains distinct from delivery.

Independent review reproduced and repaired stale-context/policy acceptance races, reminted Gmail portal links, complaint/conflict holds that could clear on a later question, opt-out during capability admission, missing Gmail follow-up headers, large Unicode context and legacy structured-quote compatibility. Tests now verify a single write transaction across final acceptance, persistent evidence-backed operator holds, unchanged accepted orders, exact full stored terms and no stale-version commitment. No additional reproducible issue remained in these reviewed paths after repairs; this is bounded local qualification, not a guarantee about arbitrary future integrations.

The actual Gmail OAuth/MIME adapter runs through the integrated tools gateway with controlled HTTP fixtures for inbox normalization, account/header constraints, threaded replies, exact reservations and send replay. Model interpretations in scenario tests are explicit structured fixtures; they validate application decisions and boundaries rather than measuring a live model's language quality.

## External setup and scope limits

No live Gmail account, real customer conversation, paid OpenAI request, PayPal transaction, Stripe transaction, customer sale or production deployment was performed. Owner-configured model/tariffs/secret, dedicated Gmail OAuth/recipient policy, receiving-header trust and public portal origin are still required. See [CUSTOMER_CONVERSATIONS.md](CUSTOMER_CONVERSATIONS.md) and [CONVERSATION_TRANSPORT.md](CONVERSATION_TRANSPORT.md).

The eleven tools skips are one browser test without the optional Playwright/Chromium installation, eight Docker tests without an explicitly provisioned pinned sandbox image, and two PNG checks without optional Pillow. The web UI Chromium test is separate and passed; no generated-code host fallback or unsandboxed SDK browser fallback was enabled.

Post-acceptance changes/revisions and complaints require an audited operator review; they do not silently alter an accepted contract or issue a refund. The default included revision allowance is zero. Unsupported, truncated or unauthenticated mail cannot establish scope or commercial consent. Quiet hours use UTC for follow-ups. Already accepted external mail cannot be recalled.

Local wheel SHA256 values (build artifacts, not published releases):

- Business `widemind-0.1.0-py3-none-any.whl`: `bb33776a26caa09254fa3d9c58dde7699212b99a8798b76cd12a23b0c4ab1b4a`.
- Tools `widemind_tools-0.1.0-py3-none-any.whl`: `16cf2e189391b7acad955e51734af2feaa37dcb41f7ff494a1a22aa94b6599f8`.

Git publication/PR status is checked separately from application qualification. The managed environment currently permits native Git but excludes `api.github.com`; no remote CI result or created PR is implied by these local tests.
