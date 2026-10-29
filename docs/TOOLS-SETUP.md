# Tools runtime setup

This guide covers the retained standalone Python tools gateway. For the web app, persistent business worker and authoritative shared economy, use [INTEGRATED_SETUP.md](INTEGRATED_SETUP.md) and [TOOLS_INTEGRATION.md](TOOLS_INTEGRATION.md). Install these variants in separate virtual environments. The standalone control store is for its development/adapter workflow; it is not the integrated application's ledger.


This slice selects Python 3.11+, Linux/POSIX filesystem controls, SQLite, and
optional Pillow, Playwright, and Docker. It is an operator-driven tools gateway;
it does not run an autonomous scheduler, website, payments, or a customer queue.
There is no timer that makes paid model calls while idle.

## Install and first local deliverable

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip==26.2.2
python -m pip install -e '.[test,graphics,browser]'
python -m pytest -q
```

Copy [the example](../examples/owner-config.example.json) to a private operator
directory outside the checkout and outside the dedicated workspace. Change
`control_dir` and `workspace_dir` to absolute, disjoint paths you own. Keep the
config outside both trees. Use a private service user; the generated-code
container must never receive these paths, credentials, the Docker socket, or
the host environment.

```sh
umask 077
mkdir -p /tmp/widemind-demo
cp examples/owner-config.example.json /tmp/widemind-demo/owner.json
python - <<'PY'
import json
from pathlib import Path
p = Path('/tmp/widemind-demo/owner.json')
c = json.loads(p.read_text())
c['control_dir'] = '/tmp/widemind-demo/control'
c['workspace_dir'] = '/tmp/widemind-demo/workspace'
p.write_text(json.dumps(c))
p.chmod(0o600)
Path('/tmp/widemind-demo/request.json').write_text(json.dumps({
    'operation': 'create', 'path': 'deliverables/hello.txt',
    'text': 'A real local WideMind deliverable.'
}))
PY
widemind --config /tmp/widemind-demo/owner.json status
widemind --config /tmp/widemind-demo/owner.json init --run-id local-demo
widemind --config /tmp/widemind-demo/owner.json execute files --run-id local-demo --action-id local-file-1 --input-file /tmp/widemind-demo/request.json
widemind --config /tmp/widemind-demo/owner.json balance local-demo
```

Directories are created with mode 0700; pre-existing private/workspace roots
must already have that mode and belong to the service user. The config must be
a private regular file (0600), and path components must not be symlinks. Do not
use the checkout or a directory containing unrelated files as the workspace.

The receipt is private JSON containing status, action/reservation IDs, measured
usage, artifacts and references. It may include customer data: do not publish
CLI output or the SQLite database. This slice has no public projection.
The top-level `succeeded` status means the adapter completed and its usage was
settled. Inspect operation-specific results too: generated Python exposes its
actual `exit_code` and `data.status`, including nonzero program failures.

## Enable providers independently

Each entry under owner `tools` must explicitly set `enabled: true`. One missing
credential does not prevent other tools from running. `status` inspects local
setup; it does not make paid requests or prove account access. Gmail reports
`not_verified` until the actual operation verifies its grants and account.

| Setup | What the owner supplies |
| --- | --- |
| [AI and images](AI-PROVIDERS.md) | Dedicated API key secret reference, supported model, context/output ceilings and verified tariffs; images additionally require a certified charge bound |
| [Research/browser](RESEARCH.md) | Public egress policy; Playwright and a properly sandboxed Chromium for browser automation |
| [Gmail](GMAIL.md) | Dedicated Google account, OAuth client/scopes, verified account identity, controlled recipients |
| [Workspace/graphics](WORKSPACE.md) | Quotas, optional Pillow for PNG |
| [Generated Python](CODE-EXECUTION.md) | Docker, approved immutable Python image, resource limits |
| [Publishing](PUBLISHING.md) | Dedicated sandbox PUT destination, secret reference, public verification URL if applicable |
| [HTTP transport](HTTP-TRANSPORT.md) | Trusted egress proxy if public research uses a proxy, or direct public-IP validation |

Use `widemind --config /owner/config.json secret set NAME` for a hidden prompt,
or `--from-env VARIABLE_NAME` for a privately supplied environment variable.
No secret value goes in command arguments or committed configuration. The
Fernet key and encrypted store live in the trusted control directory. Encryption
does not protect against a compromised service user who can read that key;
OS/container separation is the boundary against generated code.

Provider protocol checks use pinned official SDK/discovery sources linked in
each adapter document. This environment denied provider/documentation hosts,
so those sources do not establish live availability, current account pricing,
or successful account onboarding. No live credentials were configured.

## Action identity, interruption, and recovery

Use a stable UUID for each intended operation. Repeat `execute` with the same
tool/input/run/job/action ID to obtain its durable receipt. Changed input under
the same ID is rejected. Successful actions do not call the provider again.
`uncertain` means **do not create a new ID to retry the same intended effect**.

```sh
widemind --config /owner/config.json reconcile ACTION_ID
widemind --config /owner/config.json cancel ACTION_ID
widemind --config /owner/config.json pause RUN_ID
widemind --config /owner/config.json resume RUN_ID
```

There are no implicit HTTP retries. Intent precedes email/publishing dispatch.
After a worker lease expires (action timeout plus 10 seconds), recovery first
marks the attempt uncertain; it does not replay it. Reconciliation uses the
original account/configuration snapshot. A live worker's lease cannot be
stolen. Cancellation is polled during network/browser/container operations;
reconciliation remains available for cleanup and evidence collection.

Gmail correlation IDs are not provider-enforced idempotency keys. Empty or
ambiguous search results do not prove a send failed. Publishing verifies the
intended immutable object's bytes. If evidence cannot resolve the outcome, the
receipt remains uncertain for operator investigation, without another send/PUT.

Each paid request needs a positive atomic reservation before dispatch. Usage is
actual provider units priced using configured integer micro-USD tariffs and
labelled provisional, not a verified invoice. Known partial charges post once;
unknown remaining liability keeps the rest of the reservation. New paid calls
in that run stop until the uncertainty is resolved; free unrelated tools work.
If actual liability exceeds the quote, the full charge is posted even if the
balance becomes negative. New paid reservations then stop.

The opening $5 is labelled owner funding. No tool can credit claimed revenue.
Local compute, storage, network, Gmail subscription and hosting allocations are
owner subsidies/unallocated costs in this slice. It cannot support an honest
profit/survival claim until those and settlement/invoice reconciliation exist.

## Real-provider smoke procedure

[AI-PROVIDERS.md](AI-PROVIDERS.md#owner-run-real-provider-smoke) documents a small,
real billable content request, including setup, a conservative reservation and
usage inspection. [GMAIL.md](GMAIL.md) documents OAuth and an explicitly
controlled-recipient smoke. These are owner-run procedures and were **not run**
during implementation. Never use real customer data or production publishing
to validate development. Local tests use synthetic fixtures and loopback
destinations; optional Docker checks run generated code in the actual sandbox.

## Python interface

```python
from widemind.gateway import Gateway
gw = Gateway.from_config('/owner/config.json')
gw.store.create_run('run-1')  # privileged owner funding, idempotent
receipt = gw.execute('files', {'operation': 'list'}, run_id='run-1',
                     action_id='2026-local-list-1')
recovered = gw.reconcile(receipt['action_id'])
```

Constructing the gateway, owner funding, and `setup_context('gmail')` are trusted
operator APIs. They must not be exposed as model tools or a public HTTP API.
`execute` arguments can select only the documented operation/content within
owner-configured capabilities. Tool results are marked untrusted: webpages,
emails, attachments, files and generated plans cannot modify policy, secrets,
accounts, tariffs or budgets. There is deliberately no automatic plan executor.

The root package retains the current main central-accounting authority checks: standalone paid SDK execution is blocked when central runtime configuration is present. It does not provide an alternate wallet for the web/worker. See [MAIN_AUDIT_20261029.md](MAIN_AUDIT_20261029.md).
