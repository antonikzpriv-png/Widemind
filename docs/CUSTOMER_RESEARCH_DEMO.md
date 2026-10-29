# Offline customer research demonstration

This repeatable local demonstration runs the actual integrated research workflow,
gateway, HTTP search/fetch/AI adapters, encrypted secret store, SQLite action
journal and confined file writer. It produces distinct research briefs for a
dental appointment business and a bicycle repair business.

**All providers and finances are controlled fixtures.** Search, source pages and
OpenAI Responses are served over actual loopback HTTP. The model responses are
human-written synthetic answers selected only after the fixture provider reads
the actual retrieved evidence in the request. No model inference, live search,
live account, customer payment, invoice settlement or autonomous revenue is
verified. The fixture tariff and API token are synthetic.

The in-process `ReservationFamilyFixture` models only parent/child budget
admission, starts, provisional usage and idempotent settlement. It does not create
a wallet, opening credit, revenue or production ledger. Its output prominently
labels financial verification **UNVERIFIED**. Production must use the separate
authenticated PayPal service-family economy and its authoritative child budget
API. No Stripe or standalone wallet path is selected by this demo.

Use the isolated tools environment after installing the integrated tools package:

```sh
python3 -m venv .venvs/tools
.venvs/tools/bin/python -m pip install -e 'services/tools[test]'
demo_output=$(mktemp -d /tmp/widemind-customer-research.XXXXXX)
env -u WIDEMIND_RUNTIME_DATABASE WIDEMIND_TOOLS_ACCOUNT=dedicated-tools \
  .venvs/tools/bin/python scripts/customer-research-demo.py --output "$demo_output"
```

The output directory must be fresh, empty and private (0700). The demo starts
only controlled loopback fixture endpoints, explicitly enables their exact
origins in owner and transport configuration, and sets `NO_PROXY` for that
loopback address for the fixture's lifetime. No external hostname is fetched.
The root output contains `offline-summary.json`; each customer directory holds
its own control journal and private workspace. The summary links to each actual
Markdown brief, private evidence manifest, frozen questions, validation checks
and paid child receipts. Keep these artifacts private; do not commit them.

The dental brief covers the fixture's subscription price and calendar/staff
rollout. The bicycle brief covers workshop booking fees and CSV export
portability. They contain different answers, not just changed headings. The
provider fixture asserts customer-specific retrieved spans were supplied; the
workflow independently checks citation IDs, source offsets, lexical/numeric
support, customer requirements and originality before creating the deliverable.
These deterministic checks do not prove factual truth or publisher reliability.

The end-to-end regression suite also exercises:

- Unrelated and insufficient sources stopping before a paid synthesis request.
- Fabricated citations and omitted conflicts blocking completed delivery.
- Conflicting prices remaining explicitly unresolved in the brief.
- A bounded correction with two distinct paid child reservations and receipts.
- A parent cap too small for initial generation or another correction.
- Operator interruption after generation usage was saved, then restart and
  observational reconciliation without another AI POST.
- Distinct child IDs, durable replay and actual Markdown/manifest bytes, with no
  second wallet database and no duplicate parent charge for paid children.

```sh
PYTHONPATH=services/tools/src .venvs/tools/bin/python -m pytest -q \
  services/tools/tests/test_customer_research_workflow.py
```

This test uses a faithful accounting fixture rather than production settlement.
The separate economy service's child-family API, credential binding and real
provider billing remain independently tested deployment requirements. A passing
offline run is evidence of the local workflow behavior, not a live customer job.
