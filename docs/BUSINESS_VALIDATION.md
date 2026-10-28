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
