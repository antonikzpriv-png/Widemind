# Tools validation record — 2026-10-29

Verified on Linux with Python 3.12.14. This record distinguishes local checks
from provider/account verification; it does not claim an autonomous business run.

## Integrated checks

```sh
WIDEMIND_TEST_CODE_IMAGE='python@sha256:5789aa194f757888f41fa3d48e14b6feaa1b57c0af0b56fcb1372d6474d3bff0' python -m pytest -q -rs
```

Result: **356 passed, 1 skipped, 75 subtests passed in 31.23 seconds**.
The one skip is actual Chromium rendering: the available OS sandbox cannot
start. The tests never disable that sandbox. The Docker image was explicitly
provisioned for these development checks; adapters do not pull it automatically.

Verified behavior includes concurrent budget exhaustion, exact integer charges,
idempotent action replay, worker leases/restart, known and unknown billable
failures, retained charges despite oversized/deep results, private-state
isolation, encrypted-secret concurrency/limits, workspace confinement/quotas,
actual SVG/PNG rendering, OAuth/account/recipient policy fixtures, interrupted
attachment writes, source extraction, proxy/TLS/SSRF guards, and cancellation.

Actual loopback HTTP tests exercise gateway + SQLite + encrypted secrets +
provider adapters. They prove reservations precede paid fixture requests and
measured fixture usage is posted once. Accepted publication followed by dropped
TCP response recovers through GET with exactly one PUT. Gmail's accepted-send
loss/restart fixture makes no real network call and never repeats a send.

The focused Docker battery passed **41 tests**, including **8 real Docker
cases**: nonroot/read-only/no-network execution, selected read-only inputs,
real artifacts, unsafe links/special files, output/time/cancellation limits,
memory/tmpfs/PID exhaustion, metadata rejection and cleanup. An explicit label
query after the tests confirmed no owned code containers remained. CPU quota is
enforced; actual CPU consumption is not claimed as a measured usage metric.

Additional checks passed:

- Bytecode compilation and `git diff --check`.
- Build of both wheel and source distribution; setup docs, capability table and
  owner config example are included in the source archive.
- Installed-wheel CLI smoke in a separate environment **without** optional
  Pillow/Playwright: local file creation and SVG availability work, while
  unrelated providers stay disabled.
- Repository-local Markdown link checks.

CI is configured for Python 3.11/3.12. This session ran 3.12; remote CI results
and a separate 3.11 execution are not claimed here.

## Explicitly unverified

No live API credentials were configured. No paid model/image generation,
Google OAuth login, actual email send, or external/production publication was
performed. [Official provider source references](AI-PROVIDERS.md) and
[Google discovery/OAuth references](GMAIL.md) verify protocol shapes, not live
accounts, tariffs, billing ceilings, deliverability or current model access.
Direct provider/documentation hosts were unavailable under session egress policy.

Public browser rendering still needs a supported sandboxed Chromium runtime.
Proxy-mediated public research requires a trusted SSRF-enforcing egress proxy;
the transport fails closed without owner configuration. Image generation needs
a genuine exact-settings charge-bound certification because the API has no
max-charge parameter. Provider tariff costs remain provisional until invoices
are reconciled. Hosting/storage/network/compute allocations are disclosed owner
subsidies, and payment settlement/customer delivery authorization are outside
this tools slice. See [capability status](CAPABILITIES.md) and
[owner-run real-provider smoke instructions](AI-PROVIDERS.md#owner-run-real-provider-smoke).
