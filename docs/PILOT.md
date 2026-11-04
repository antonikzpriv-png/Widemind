# Real paid survival pilot tooling

These commands support [Milestone 7](ROADMAP.md#7-real-paid-survival-pilot). None creates a run, grants credit, contacts a payment provider or changes a balance or the ledger. All select the run from `--run <id>` or `WIDEMIND_COMMERCE_RUN_ID`.

| Command | Purpose |
| --- | --- |
| `pilot:preflight` | Read-only go-live checklist |
| `pilot:report` | Auditable run record (`--public` for a redacted version) |
| `pilot:attest` | Record or withdraw the customer-independence attestation |
| `pilot:cost` | Record costs paid outside the ledger, or correct one |
| `pilot:alerts` | Notify the operator when the run needs attention |
| `pilot:snapshot` | Save the public report over time and choose what the website shows |
| `backup`, `backup:verify`, `backup:restore` | Verified database backups ([DEPLOYMENT.md](DEPLOYMENT.md#backups-and-restore)) |

## Go-live checklist

```sh
npm run pilot:preflight
```

Prints JSON checks and exits with status 2 until every check passes:

| Check | Passes when |
| --- | --- |
| `run_configured`, `run_exists` | A valid run identity is set and the runtime run exists |
| `worker_available` | A persistent worker heartbeat is current |
| `operator_auth` | `WIDEMIND_OPERATOR_PASSWORD_HASH` is set |
| `public_https` | The public origin is HTTPS with secure cookies |
| `live_effects_enabled`, `commerce_live` | `WIDEMIND_ENABLE_LIVE_EFFECTS=true` and `WIDEMIND_COMMERCE_MODE=live` |
| `payment_provider` | The selected provider's credential is present (`WIDEMIND_PAYPAL_CLIENT_ID` or `WIDEMIND_STRIPE_API_KEY`) |
| `economy_available`, `economy_live_opening` | The economy is reachable and the run's opening is a live 5000000 micro-USD grant |
| `tools_available`, `email_capability` | The tools service is reachable and at least one `gmail.*` capability is available |
| `business_available` | The business module is available |
| `backups_recent` | A complete backup exists in `WIDEMIND_BACKUP_DIR` within `WIDEMIND_BACKUP_MAX_AGE_HOURS` |

Only the presence of a secret is inspected; values are never printed. A passing check means configuration is present, not that the provider account has been verified.

## Run record

```sh
npm run pilot:report            # private operator record
npm run pilot:report -- --public  # redacted record suitable for publishing
```

The report combines the runtime database and the economy summary:

- **Run** state and lifecycle times.
- **Interventions**: every authenticated operator control (`operator.resume`, `operator.pause`, `operator.stop`, `operator.wake`). The private view lists each with its time; the public view gives counts only.
- **Activity**: action counts by state and total attempts. The private view adds counts per tool.
- **Outcomes**: gross and net customer receipts (after processor fees, refunds and chargebacks, plus reversals), operating charges, net operating result, spendable, reserved and held amounts, and exhaustion. Exact integer micro-USD strings. Net values are `null` when the economy did not report a deduction category.
- **Outside support**: opening credit, owner funding, test funding and noncash infrastructure subsidies, kept separate from receipts.
- **Deliveries**: order counts by status from the business service, paid orders delivered and not fully refunded, delivered orders fully refunded, and overdue delivery obligations. Counts only; `null` when the business service is unavailable.
- **Attestation**: the latest customer-independence attestation (`affirmed`, `withdrawn` or `none`).
- **Outside-ledger costs**: totals by category, and `resultAfterOutsideCostsMicros` (net operating result minus those costs).
- **Criteria**: `live_mode`, `opening_exactly_5usd`, `no_test_funding`, `customer_receipts`, `independent_customer`, `delivery_costs_covered`, `no_owner_funding` and `paid_work_delivered` are required. `paid_work_delivered` stands in for "useful delivered work": at least one paid order was delivered and not fully refunded. The business service does not record customer satisfaction itself. `operating_costs_covered` and `no_overdue_deliveries` are reported but not required: the roadmap treats covering a job's delivery costs as the first milestone, and full profitability as a later one. Each is `met`, `not_met`, `unknown`, or for independence `attestation_required`.
- **Verdict**: `met` when every required criterion is met, `pending_attestation` when only the attestation is missing, otherwise `not_met`.

The public view omits the run identity, per-event times, tool names, attestation evidence and notes, and individual cost records. It never includes customer, payment, action or provider identifiers.

## Snapshots and the public pilot page

```sh
npm run pilot:snapshot                      # save a snapshot, not published
npm run pilot:snapshot -- --publish         # save and publish on the website
npm run pilot:snapshot -- --publish --every 86400   # daily published snapshots
npm run pilot:snapshot -- --list            # ids, times, hashes, publication state
npm run pilot:snapshot -- --verify          # check the hash chain (exit 2 if broken)
npm run pilot:snapshot -- --retract <id>    # remove a snapshot from the website
```

A snapshot stores the **public view** of the report, so it never holds private evidence, notes, cost records, tool names or the run identity. Each snapshot's SHA-256 hash covers the previous snapshot's hash, the run, the time and the report, so changing or removing an earlier record breaks the chain. The table is append-only; the only permitted change is retracting a publication, which keeps the snapshot and its hash. `--every` takes at least 300 seconds; cron running the plain command works too.

`/pilot` on the website (and `/api/pilot` as JSON) shows the latest published, unretracted snapshot for `WIDEMIND_COMMERCE_RUN_ID`. It shows the verdict, each criterion, money in and out, outside help, interventions, running costs, a timeline of published snapshots, whether the chain verifies, and the latest hash. It never reads the economy live, so nothing reaches the public page until you publish it. With nothing published it says so instead of estimating.

## Customer independence

Accounting can show that money arrived, not who paid it. After checking the payer is not you or someone acting for you:

```sh
npm run pilot:attest -- --independent-customer --evidence "<private reference>" [--note "..."]
npm run pilot:attest -- --withdraw --evidence "<reason>"
```

The latest entry decides; a withdrawal supersedes an earlier affirmation. Entries are append-only (database triggers reject edits and deletes) and are also written to the run's event log. The evidence reference stays private: use a pointer such as a transaction or correspondence ID, not raw customer data.

## Costs outside the ledger

Hosting, domains, subscriptions and tooling that WideMind does not pay from its balance:

```sh
npm run pilot:cost -- --category hosting --amount 6.50 --from 2026-11-01 --to 2026-11-30 --evidence "invoice 881"
npm run pilot:cost -- --corrects 1 --amount 1.50 --evidence "credit note"
npm run pilot:cost -- --list
```

Categories: `hosting`, `domain`, `subscription`, `tooling`, `other`. Amounts are USD with up to six decimals, stored as exact micro-USD. A correction is a new negative entry against an original entry, and cannot reduce it below zero. History is never edited. These entries do not change balances or spendable credit.

## Alerts

```sh
npm run pilot:alerts                    # one pass, for cron
npm run pilot:alerts -- --watch 300     # check every 300 seconds
```

Run this as its own process, separate from the worker, so it can notice the worker stopping. Run one alert process per run; several would each send the same notification.

| Condition | Severity | Active when |
| --- | --- | --- |
| `worker_down` | critical | No current persistent worker heartbeat |
| `run_failed` | critical | The run state is `failed` |
| `economy_unavailable` | critical | No accounting summary can be read (economy unreachable or failing, or the run was never opened) |
| `reconciliation_dead_letters` | critical | Payment reconciliation tasks exhausted their retries |
| `credit_exhausted` | warning | Spendable credit is zero or less, or the run is `exhausted` |
| `unresolved_actions` | warning | Actions have an unknown external outcome |
| `backup_stale` | warning | The newest backup is older than `WIDEMIND_BACKUP_MAX_AGE_HOURS`, or none exists (not observable without `WIDEMIND_BACKUP_DIR`) |

Dead letters are read from the economy's operator API when `WIDEMIND_ECONOMY_URL` and `WIDEMIND_ECONOMY_OPERATOR_TOKEN` are set; otherwise that condition is reported as not observable and never alerts. A condition that cannot be observed neither raises nor clears.

A notification is sent when a condition is raised, when it clears, and every `WIDEMIND_ALERT_REPEAT_MINUTES` (default 360, minimum 5) while it stays active. Set `WIDEMIND_ALERT_WEBHOOK_URL` (HTTPS, or HTTP on loopback; no embedded credentials) to receive a JSON POST with `text` and `content` fields, so Slack-style and Discord-style incoming webhooks work directly; `alerts` holds the structured notices. Email works through any webhook-to-email relay. Without a webhook, notices are printed only. Delivery is recorded only after a 2xx answer, so a failed delivery is retried on the next pass. Messages contain condition names, states and counts only. The webhook URL is never printed.

Exit status: 0 nothing active, 2 a condition is active, 3 delivery failed.

## Limits

Figures are local records only: they are not a bank or provider reconciliation. Independence and outside costs are operator statements; the report is only as complete as what you recorded. Delivery usefulness is not assessed here; check delivery acceptance in the business records. For accounting consistency, also run the economy's integrity audit ([reporting](ECONOMY_REPORTING.md)).

## Verification

`node --test test/pilot*.test.ts` covers snapshot hash chaining, tamper detection, retraction, the public page's publication boundary, attestation and cost recording, append-only enforcement, correction limits, exact USD parsing, verdicts with and without attestation, alert raise/repeat/clear, failed-delivery retry and secret non-disclosure, plus run selection, exact-integer outcomes, criteria for sandbox, test-funded, owner-funded and unprofitable runs, intervention and activity counting, public redaction, an offline economy, and secret non-disclosure in readiness. The last test in `test/economy-python-integration.test.ts` runs the report, a published snapshot, the `/pilot` page and alerts against the real Python economy service with a sandbox opening, including the economy being stopped. These are local fixtures and sandbox accounting, not a live pilot.
