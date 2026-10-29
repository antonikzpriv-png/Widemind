# Verified calculator fulfillment

The PayPal-integrated tools service adds `creative.software`. Its first enhanced software category is a dependency-free interactive arithmetic calculator. A model designs customer-specific expressions and presentation against a frozen scope. A trusted compiler generates the application and the **actual generated JavaScript** is tested by Node inside the existing networkless `CodeAdapter` container. A feature list is never treated as a working application.

The original `software.create` starter remains a static template offering. It does not acquire the verification or functional claims of this enhanced category.

## Frozen scope and design

`creative.software` accepts exactly `{ "scope": ..., "design": ... }`. Model/account/provider configuration is never accepted from a brief.

The scope has these required fields:

- `title`, `description`, `category: "calculator"`, `requirements` (1–12 bounded strings), and `max_revisions` (0–2).
- `inputs`: 1–8 unique `{id,label,min,max,default}` numeric inputs. IDs are safe lowercase names, values are finite and within ±1e9, and defaults fit a nonempty agreed range.
- `outputs`: 1–8 unique `{id,label}` results.
- `acceptance_cases`: 2–16 independently supplied `{name,inputs,expected,tolerance}` checks, covering at least two distinct input combinations. Each case specifies every agreed input/output; the adapter permits absolute tolerance between 0 and 1, while the integrated business offering caps it at 0.01. The integrated offering also restricts `requirements` to its six implemented calculation/interface features. These expected results are frozen before the model is called and are never replaced by the model's opinion.

The model design is `{outputs:[{id,expression}],accent?,heading?,description?}`. Output IDs and order exactly match scope. Expressions contain finite numeric literals, `{ "input": "hours" }`, or `{ "op": "multiply", "args": [ ... ] }`. Operations are `add`, `subtract`, `multiply`, `divide`, `min`, `max`, `pow`. Trees are bounded to depth 8 and 120 nodes per output, with 2–8 arguments; subtraction/division/power require exactly two, and power requires a literal exponent between -8 and 8. The model cannot supply JavaScript, HTML, file paths, shell commands, credentials, libraries or network destinations. Accent is a six-digit hex color; textual presentation is escaped.

The included synthetic example under [examples/creative/software/project-estimator](../examples/creative/software/project-estimator/) implements hours × hourly rate, including fractional hours, zero hours, input validation and interactive updates. The example is development output, not customer work or evidence of payment.

## Artifacts and checks

Successful verification returns `verified: true`, `filename`, `media_type`, `content`, flat `checks`, `files`, `preview`, and the validated `design`. Files carry `filename`, `media_type`, `encoding: "utf-8"`, `content`, `sha256`, byte `size` and a role. They are:

- `index.html`: self-contained functional application and interactive preview.
- `calculator.js`, `calculator.css`: editable implementation source.
- `README.md`: opening, usage, safe embedding and numeric limitations.
- `scope.json`: the agreed inputs, outputs and acceptance cases.

Every customer-supplied acceptance case runs against the compiled JS source in Node. The independent harness also tests defaults, rejects missing/additional/non-numeric/non-finite/out-of-range inputs, and exercises each input's minimum and maximum. Undefined arithmetic is an explicit handled application error. The verification receipt identifies the SHA-256 of the source actually executed. A wrong formula returns `verified: false`, failed checks and an explanation, with no accepted deliverable or preview. Source stages remain private for review. Business orchestration may schedule a separately authorized bounded repair; the tool never starts a paid model call or unbounded retry itself.

The generated UI recalculates on input, has explicit Calculate/Reset controls, labels and live result/error announcements, and makes no network requests. This category uses standard JavaScript numeric precision; it is not the economy ledger or exact monetary accounting. Arithmetic calculators do not imply support for accounts, arbitrary applications, data uploads, arbitrary libraries or external APIs.

## Isolation and configuration

Enable the enhanced offering only when the paid model adapter, files capability and code/creative-software sandbox are all available. The owner config for `creative.software` requires `enabled: true`, `verification_runtime: "node"`, and `code_settings` copied from the separately reviewed `code.execute` owner settings. The integration layer should inject that copy; task input must never select it.

Build and inspect the minimal Python + Node image:

```sh
docker --host=unix:///var/run/docker.sock build -f docker/creative-sandbox.Dockerfile -t widemind-creative-sandbox .
docker --host=unix:///var/run/docker.sock image inspect widemind-creative-sandbox --format '{{.Id}}'
```

Configure the resulting immutable `sha256:...` image ID, not the tag. Follow the managed cloud Docker skill's proxy and certificate instructions when a build needs network access. The runtime never pulls images or installs dependencies. The image must declare no volumes or secrets and only the Python-safe environment allowed by `CodeAdapter`.

Example owner-controlled code settings are `owner_compute_subsidy: true`, `wall_seconds: 30`, `memory_bytes: 134217728`, `cpu_millis: 1000`, `pids: 32`, `max_code_bytes: 65536`. Existing supported upper bounds (512 MiB, 128 PIDs) remain available. The existing gateway still enforces the real economy reservation and overall deadline/output limits. Local compute and storage are explicitly owner-provided/unallocated; paid generation has a separate authoritative reservation/receipt rather than an invented free model charge.

Availability checks the pinned image and performs a hardened, networkless `node --version` capability probe without customer data, workspace, credentials or a host-execution fallback. Verification uses the existing resource-limited, nonroot, read-only, capability-free Docker code runner with no network and no operator sessions/secrets. The trusted Python verifier invokes `/usr/bin/node` inside that container, with a minimal environment. It never runs generated software as the service or host user.

The preview integration must use a separate origin or an opaque `sandbox="allow-scripts"` frame, without `allow-same-origin`, form/navigation/popup permissions or operator cookies. A `srcdoc` frame inherits its parent's CSP, so a host with `script-src 'self'` needs a separate isolated preview document/CSP; do not loosen the operator application's script policy merely to execute a preview. Generated files include a restrictive self-contained CSP denying connections, images, forms and base-URI changes. Never advertise the live preview as functional if its frame policy blocks scripts.

## Recovery and verification

Expected file hashes and the original arguments digest are persisted before local writes. File writes and code execution use separate namespaced child journals. Completed files are retained, and a repeated original action does not rewrite them. Conflicting bytes or changed scope/design remain uncertain. An interrupted code action delegates to `CodeAdapter.reconcile`, which observes/cleans only its owned container. A completed sandbox receipt can reconstruct the aggregate receipt without a second execution. An absent sandbox intent proves execution never began; existing source stages may then be completed under the same immutable action. Uncertain effects are never blindly repeated.

```sh
PYTHONPATH=services/tools/src .venvs/tools/bin/python -m pytest services/tools/tests/test_creative_software.py -q
# After provisioning the approved image:
PYTHONPATH=services/tools/src WIDEMIND_CREATIVE_IMAGE=sha256:<image-id> \
  .venvs/tools/bin/python -m pytest services/tools/tests/test_creative_software.py -q
```

The second command executes both a correct calculator and an intentionally incorrect formula through the real Docker adapter. Unit fixtures explicitly label their synthetic sandbox receipts; they alone do not establish isolated behavior or live provider operation. No live credentials or payments are required for compiler/sandbox validation.
