# Real paid survival pilot tooling

Two read-only commands support [Milestone 7](ROADMAP.md#7-real-paid-survival-pilot). Neither creates a run, grants credit, contacts a payment provider or changes state. Both select the run from `--run <id>` or `WIDEMIND_COMMERCE_RUN_ID`.

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
- **Criteria** for the milestone acceptance: `live_mode`, `opening_exactly_5usd`, `no_test_funding`, `customer_receipts`, `delivery_costs_covered`, `no_owner_funding`, each `met`, `not_met` or `unknown`. `independent_customer` is always `attestation_required`.
- **Verdict**: `pending_attestation` when every accounting criterion is met, otherwise `not_met`. The tool never reports the pilot as passed: customer independence needs a separate attestation.

The public view omits the run identity, per-event times and tool names. It never includes customer, payment, action or provider identifiers.

## Limits

Figures are local records only: they are not a bank or provider reconciliation. Hosting, subscriptions and other costs outside the ledger are excluded unless recorded as charges. For accounting consistency, also run the economy's integrity audit ([reporting](ECONOMY_REPORTING.md)).

## Verification

`node --test test/pilot.test.ts` covers run selection, exact-integer outcomes, criteria for sandbox, test-funded, owner-funded and unprofitable runs, intervention and activity counting, public redaction, an offline economy, and secret non-disclosure in readiness. These are local fixtures, not a live pilot.
