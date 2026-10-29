# Customer conversation qualification

Initial base: integrated main `e70220f1f7b38771cf619fba1d1241298763b922`. The branch also incorporates newer main `5b6a1a180d853cc30f8ae98913b4ab7df5b7314a` and preserves its discovery module. Final qualified implementation: `0d953403f764bfeb8f9f03bf7d61a3ba562a245e`; subsequent report-only edits do not change application behavior.

The change extends the existing PayPal business service, Gmail transport and customer portal. It adds no accounting authority, runtime scheduler, provider account or live payment claim. Stripe deployment/source paths and their credential families remain separate.

## Executed checks

| Check | Result |
| --- | --- |
| `npm run check` with the configured root fixture Python | **243 Node tests passed**, zero failed/skipped; formatting, TypeScript and compiled build passed |
| Integrated economy service suite | **414 passed**, one existing Starlette HTTPX deprecation warning |
| Integrated tools service suite | **397 passed, 81 subtests passed, 11 optional prerequisite skips** |
| Final business service suite | **200 tests passed**, including 32 realistic conversation scenarios, six additional consent-boundary regressions, nine actual host/portal cases, discovery and combined-budget/provenance regressions |
| `npm run integration:browser` | Passed against actual local economy/tools/business processes, persistent worker and desktop/phone Chromium forms; explicit local PayPal protocol fixture; one capture and one refund reconciled, worker hard restart, customer isolation and private SVG delivery verified |
| Preserved Stripe `npm run business:demo -- --fixtures` | Three scenarios passed: interrupted delivery/reconciliation, pause/unavailable tools and real central-ledger exhaustion; synthetic provider/customer/payment data only |
| Preserved discovery `npm run discovery:demo -- --fixtures --browser` | Passed with actual local services and independent worker restarts, one bounded prototype, lifetime allowance exhaustion, source provenance and desktop/phone private operator views; no real market/demand claim |
| Business/tools wheels | Both built successfully; conversation controller/schema/poller/migrations 9–10 and Gmail wrapper included. Separately installed business wheel initializes all ten business migrations, preserves Discovery and matches all 29 Python/SQL files to source. An actual schema-nine upgrade preserves stored quotes, private messages and global consent across repeated restarts |
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

The newer discovery main was merged into the feature branch before publication. Conflict resolution retains both event families, host/operator routes, provenance handling and reservation/reconciliation methods. Conversation order admission includes existing discovery allocations when enforcing the experiment allowance. Structured model result wrappers remain usable without granting authority to source/payment claims inside model data. The conversation migration is numbered 9 after Discovery's migration 8; no existing applied migration was rewritten.

The final requirement audit added useful pre-quote answers and immediate handling for additional clear opt-out phrases. It also reproduced and fixed a capability-scope issue: a newly created request claiming another customer's email could formerly use its own portal token to globally suppress that email. Additive migration 10 now persists request-local consent. Portal requests cannot alter another request's consent or block its original owner's acceptance. Six regressions cover same-email isolation, paused/no-budget opt-out, verified Gmail global suppression, late dispatch, linked-order publication, and persistence through restart/recovery/operator resolution.

## Requirement verification before main publication

| Requested behavior | Implemented and verified |
| --- | --- |
| Ordinary requests, questions, negotiation, clarification, acceptance, changes, complaints and opt-outs | Strict intent schema, policy-based initial/quoted answers and 32 realistic multi-message scenarios |
| Thread-specific durable context | Private thread/messages/proposal storage, exact replay, isolated customer tokens and bounded model excerpts |
| Complete quote proposals and bounded negotiation | Price, scope, deliverables, exclusions, turnaround, revisions, expiry and version digest; floors, capabilities, cost/experiment limits and capacity checked in code |
| Exact authenticated acceptance | Token-owned current version/digest, pending-change rejection and one transaction through final order admission |
| Follow-ups, quiet periods and immediate opt-outs | Free bounded scheduling, UTC quiet hours, expiry/counters, early clear-phrase detection and request-scoped consent rechecked immediately before dispatch |
| Private operator status, commitments and intervention | Protected views, persistent complaint/conflict hold and evidence-backed operator resolution without auto-acceptance or resubscription |
| Budget-authorized structured model outputs | Exact reservations and provider usage through the existing gateway, strict schema/code validation and no model changes to money, accounts or policy |
| Idempotency, uncertainty, pause/stop and privacy | Cached reconciliation without redispatch, durable resume, token/request isolation and no public message/history exposure |
| Realistic failures and malicious email tests | Ambiguous/changed acceptance, replay/conflict, unavailable model, injected authority fields, payment claims, consent races and restart recovery |
| Existing PayPal/Gmail/portal contracts and separate Stripe deployment | Real local PayPal browser/worker fixture flow and preserved Stripe fixture demonstration pass; payment deployment sources are unchanged |

The earlier PR API attempt was blocked by the managed network policy. The user's subsequent instruction explicitly authorizes validated publication directly to main; remote publication is verified separately from local checks. Live-provider accounts remain setup prerequisites, not fixture claims.

The actual Gmail OAuth/MIME adapter runs through the integrated tools gateway with controlled HTTP fixtures for inbox normalization, account/header constraints, threaded replies, exact reservations and send replay. Model interpretations in scenario tests are explicit structured fixtures; they validate application decisions and boundaries rather than measuring a live model's language quality.

## External setup and scope limits

No live Gmail account, real customer conversation, paid OpenAI request, PayPal transaction, Stripe transaction, customer sale or production deployment was performed. Owner-configured model/tariffs/secret, dedicated Gmail OAuth/recipient policy, receiving-header trust and public portal origin are still required. See [CUSTOMER_CONVERSATIONS.md](CUSTOMER_CONVERSATIONS.md) and [CONVERSATION_TRANSPORT.md](CONVERSATION_TRANSPORT.md).

The eleven tools skips are one browser test without the optional Playwright/Chromium installation, eight Docker tests without an explicitly provisioned pinned sandbox image, and two PNG checks without optional Pillow. The web UI Chromium test is separate and passed; no generated-code host fallback or unsandboxed SDK browser fallback was enabled.

Post-acceptance changes/revisions and complaints require an audited operator review; they do not silently alter an accepted contract or issue a refund. The default included revision allowance is zero. Unsupported, truncated or unauthenticated mail cannot establish scope or commercial consent. Quiet hours use UTC for follow-ups. Already accepted external mail cannot be recalled.

Local wheel SHA256 values (build artifacts, not published releases):

- Business `widemind-0.1.0-py3-none-any.whl`: `5785b43050fc1adb7e6186c50c2aa41e6c0f45ba0f771bbbcb38a8457b64ed18`.
- Tools `widemind_tools-0.1.0-py3-none-any.whl`: `16cf2e189391b7acad955e51734af2feaa37dcb41f7ff494a1a22aa94b6599f8`.

Git publication/PR status is checked separately from application qualification. The managed environment currently permits native Git but excludes `api.github.com`; no remote CI result or created PR is implied by these local tests.

Packaging was qualified from a clean Git source export. A preliminary local wheel built in a reused setuptools directory still contained the renamed old conversation migration and was rejected by the separate installation check. The clean wheel above contains exactly ten SQL migrations, installs in a separate environment, initializes and upgrades databases successfully and matches all packaged Python/SQL files to source. No preliminary wheel was published as a release.
