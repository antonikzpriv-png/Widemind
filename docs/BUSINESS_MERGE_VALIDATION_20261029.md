# Business integration and merge validation — 29 October 2026

The audited Python application branch `codex/widemind-complete` diverged from the merged Node/TypeScript foundation. This changeset ports its business workflow and hardened providers onto main baseline `63f8b1f`; it does not merge the incompatible root Python package, WSGI host, alternate runtime or ledger. The current src-layout Python package, standalone SDK, protected economy and core Node runtime remain the foundation.

## Integrated behavior

- A discoverable business module owns namespaced opportunity/customer/quote/order/memory migrations, configurable goals/capabilities/experiment budgets, immutable scoped quotes, prioritization, customer acceptance and receipt-driven fulfillment. Free selection proposes Candidates; core authorization, outbox, execution, receipts and settlement remain authoritative.
- The registered business tools bridge supports actual dedicated Gmail and bounded sourced OpenAI research, plus guarded commerce delegation. It reuses the shared HTTP transport and central economy verification. No development wallet becomes operating funds. Durable private journals preserve original prices, actual dispatch time, usage and uncertain outcomes.
- Commerce wraps the existing protected ledger and Stripe service with exact-string order projections. It preserves immutable Checkout identity, separates receipts from earned revenue/obligations, requires authenticated persisted delivery evidence, and appends earned corrections atomically after verified payment/refund/dispute changes.
- The authored monochrome public site and six-view private console use the existing cookie, Origin, CSRF and script-free CSP. Namespaced private rendering is authenticated and bounded. Pages/counts share a WAL read snapshot, show older commitments through pagination, and label cached order payments.
- A trusted one-shot inbox scheduling CLI queues durable synchronization without a browser, funding, Resume or a paid planning loop. Separate worker processes continue to own event-driven execution and reconciliation.

## Audit repairs verified during the port

Independent reviews and regression tests repaired stale cached-payment execution; mismatched commerce modes; insufficient-funds delivery promises; deadlines incorrectly anchored to old unfunded plans; lost sync requests/cursors; cross-run Gmail deduplication; hidden hold controls; historical orders missing later refunds; starvation of historical payment updates; inconsistent paginated totals; rank/allocation disagreement; unsupported source/pricing offers; known charges lost after quality failures; and missing-journal recovery that could otherwise assume zero cost.

The final delivery guard requires an actual attachment, an authenticated intended recipient, the original message thread and an explicit acknowledgment naming the exact action. A clarification or complaint cannot recognize delivery/revenue. Actual dispatch timestamps survive lost responses and delayed reconciliation. Ordinary inbox events and Wake no longer cancel an authorized request; durable pause/stop/resume controls still fence the earlier attempt. Unknown actions are observed, never blindly resent or regenerated.

No additional reproduced material defect remained in the locally reviewed paths after these repairs. This is implementation/test evidence, not a claim that every future adapter or deployment is defect-free.

## Executed local checks

| Check | Result |
| --- | --- |
| `npm ci`, `npm run check` | Formatting, TypeScript, **209 Node tests** and production build passed; zero Node failures/skips |
| Full Python suite | **872 tests plus 75 subtests passed**, zero failures; nine explicit runtime-prerequisite skips |
| Compiled production smoke | Migration, independent web/worker startup, login, controls, durable actions/events and worker liveness passed |
| Foundation browser smoke | Actual desktop/phone login, run controls and event form passed, zero page errors |
| Business browser smoke | Actual app and independent worker; all six views at 1280/390/320, visible mobile navigation, intake/control forms, public privacy and no scripts/page errors passed |
| Explicit business demonstration | Actual BusinessService/AgentRuntime, protected Python HTTP and separate SQLite ledger; synthetic Gmail/model/Stripe boundaries; complete request/acceptance/payment/research/acknowledgment/fulfillment/refund flow passed |
| Recovery/financial edge cases | Duplicate events, unavailable tools, actual competing central reservation, stopped settlement, lost research/send responses, seven-day-old unfunded plan, delayed recovery and post-completion refund passed |
| Packaging | Fresh wheel; all **42 Python/SQL files** byte-matched source; separately installed outside checkout; existing CLI entrypoints, migrations, new bridge/commerce imports and `pip check` passed |
| Local consistency | Python compilation, local documentation links and `git diff --check` passed |

Final wheel SHA256: `b943991ae49111838f44d48c391a2a095d3d3758eea96282b348d04b511252b5` (local artifact, package version 0.3.0). The nine skips are eight optional Docker checks requiring a provisioned pinned image and one standalone SDK browser check requiring its OS sandbox. The web UI browser checks ran separately; they do not qualify sandboxed browser/code tools. An existing Starlette/httpx deprecation warning remains.

Reproduce with Python 3.12 and Node 24:

```sh
python -m pip install -r requirements.lock -e .
npm ci
npm run check
python -m pytest -q -ra
npm run smoke
npm run browser:smoke
npm run business:browser
npm run business:demo -- --fixtures
python -m pip wheel --no-deps .
```

Set `WIDEMIND_TEST_PYTHON` to the installed virtual-environment interpreter when Node tests use a separate Python environment. Set `WIDEMIND_CHROMIUM` when the browser executable is not discoverable. No remote CI outcome is asserted by local checks.

## Operation and remaining prerequisites

Enable `WIDEMIND_BUSINESS_ENABLED=true` in the shared trusted web/worker environment for `/operator/modules/brief-business/console`. Configure accounts and the separately opt-in provider bridge before real execution. See [business setup](BUSINESS_MODULE.md), [provider setup](BUSINESS_TOOLS.md), [economy commerce](ECONOMY_RUNTIME_BRIDGE.md), [UI](BUSINESS_UI.md), [scheduled mail](BUSINESS_SCHEDULING.md) and [explicit demo](BUSINESS_DEMO.md).

Stop web/worker services, correctly back up their database/WAL and the separate protected economy/journal state, migrate, then run all components at the same revision. Existing core/economy migrations are preserved; business applies its own namespace. Never import old fixture/alternate-wallet balances or reinterpret a sandbox run as live.

No actual provider mail, paid inference, Stripe sandbox/live payment, independent customer delivery or profitable outcome was created during validation. Live operation still needs owner-provisioned dedicated Gmail OAuth and controlled recipient, OpenAI project/model/verified prices, Stripe account/test key/webhook/return URLs, matching central tool profiles, allowed provider/source networking, protected OS deployment and controlled qualification. The standalone graphics/code/browser/publishing SDK is preserved but is not automatically registered as autonomous paid business execution. Development/hosting costs remain outside the demonstrated tool-level contribution metric. Git publication is verified separately against the remote main ref; tests alone do not establish a merge or deployment.
