# Business slice validation — 2026-10-28

## Local evidence

Validated on Linux, Python 3.12.14:

- `python -m unittest discover -s tests -v`: 42 passing tests.
- `python -m compileall -q widemind tests`: passed.
- `python -m widemind demo --directory /tmp/widemind-business-qualified`: completed; repeated invocation retained the same order and effect identities.
- Built an installable wheel with `python -m pip wheel . --no-deps --no-build-isolation`; installed into an isolated target and ran the demonstration twice from outside the source tree. Packaged SQL migrations were present and applied.
- `git diff --check`: passed.

The suite includes full Gmail request → quote → acceptance → economy payment-link request → settlement notification → reservation → commitment → research → scope checks → private delivery → fulfillment recognition. Additional tests cover a registered writing service, cash/earned-revenue separation, insufficient funds, explicit bounded retries, unavailable/disabled tools, process death after a tool action, duplicate intake/events/acceptance, uncertain delivery, delivery/refund races, partial/full refunds, disputes, post-completion/cancellation payment changes, stale snapshots, pause/stop, frozen plans, persisted policy reloads, deadlines, customer identity/quote expiry, opt-outs, prioritization, malformed mail isolation, private/public access, concurrent migrations, worker exclusion, and zero provider calls while idle.

During validation, fixes addressed concurrent WAL initialization, policy refresh across API and worker, durable deadline scheduling with order status, immutable plans, fresh reservation rounds after a payment hold, delivery reconciliation before payment-obligation changes, stale/concurrent financial snapshots, and isolation of malformed inbox entries.

## Evidence boundaries

All external adapters in these checks are explicit test/demo fixtures. No real Gmail messages were sent, no real customer or payment was created, no live research was collected, and no real earnings or profitability were demonstrated. The fixture economy is not a production ledger or budget enforcer.

The GitHub workflow declares Python 3.11/3.12/3.13 on Linux and Windows. Those remote jobs and Windows behavior are not claimed as locally verified. Live provider qualification requires the host clients and prerequisites in [BUSINESS.md](BUSINESS.md).

## Follow-up audit

The follow-up audit reproduced failures against the original implementation before fixing them. The full suite now has **56 passing tests**, including 14 additional regression cases. Compilation, repeatable offline demonstrations, wheel installation outside the checkout, and the migration review path are checked again for this revision. Live services and Windows are still not claimed as qualified.

| Finding | Correction |
| --- | --- |
| Stored runs could reopen in another execution mode | Pin modes durably; explicitly review/classify legacy runs; reject promotion of known fixture payments |
| Failed paid work could retry with a consumed reservation | Reject generic paid retries; require a fresh approved plan and budget authorization |
| Malformed reconciled mail evidence could complete an order | Share schema/size checks between execution and reconciliation; require valid provider references |
| Ordinary reconciliation could rewrite confirmed terminal success | Preserve confirmed facts; evolve only unresolved outcomes and unconfirmed mail |
| A completed creation result could be lost across interruption, refund, and resume | Recover and validate the result before the financial hold; reuse the deliverable without another creation reservation |
| Stopped runs kept stale payment/refund information | Permit read-only financial reconciliation, while deferring all further execution |
| Whitespace in run identifiers broke settings lookup | Normalize once before every persisted run binding/query |
| Reassessment retained only the original lesson | Preserve distinct assessment outcomes in private memory |
| Cancelled/delivered work could receive false overdue failures | Check actual obligation state; distinguish missing confirmation from proven unfinished delivery |
| Mail metadata and source URLs admitted controls or invalid ports | Validate single-line addresses, attachment metadata, provider IDs, and URL syntax |

At that audit revision, the visual UI and provider/runtime clients were outstanding. The subsequent full build implements those modules; current evidence and remaining live prerequisites are tracked in [COMPLETION_STATUS.md](COMPLETION_STATUS.md). Live-provider evidence and automatic budgeted paid rework still require additional work. These are not concealed by the fixture demonstration. GitHub API access remains subject to the cloud network policy.
