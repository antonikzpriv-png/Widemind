# Reproduce the business workflow without external accounts

This demonstration exercises the real Node business module, durable runtime and separate protected Python economy HTTP service/SQLite ledger. Gmail messages, model results/usage and Stripe API responses are explicitly synthetic provider fixtures. No real customer, message, charge, payment, delivery, revenue or profitable outcome is created.

Use Node 24 and Python 3.12+, then run from the repository root:

```sh
npm ci
python -m venv /tmp/widemind-business-demo-venv
/tmp/widemind-business-demo-venv/bin/python -m pip install -r requirements.lock -e .
WIDEMIND_TEST_PYTHON=/tmp/widemind-business-demo-venv/bin/python node scripts/business-demo.mjs --fixtures
```

The script requires `--fixtures`; it does not select or initialize a live runtime. It launches maintained integration tests, creates private temporary databases and receipt journals, binds the fixture economy service to loopback, and stops that owned process/removes temporary state at completion. Its fixture payment adapter uses HTTPX MockTransport, so no Stripe request leaves the host. The test backend is only `test/business-provider-fixture.py`, requires its own explicit fixture flag and is excluded from the application package.

The successful workflow reads an authenticated Gmail request fixture, freezes a quote, sends a fixture offer and reads exact customer acceptance from a second authenticated Gmail fixture. Duplicate messages cannot create a second opportunity or order; an unverified sender is ignored. A Checkout link creates no cash. A signed synthetic Stripe event triggers the actual reconciliation queue and authenticated provider reads; only then does the single central ledger post the receipt and hold. Core authorization reserves the research ceiling before one charged research action. Its response is deliberately lost, and a restarted runtime reconciles its persisted result while stopped without starting new work. The completed brief is checked against the agreed sections and citations, and a firm delivery commitment follows operating authorization.

Delivery also loses its first response. The runtime recovers that same send without sending again. Transport acceptance leaves earned revenue at zero. An explicitly authenticated recipient-acknowledgment fixture then supplies delivery evidence; the commerce module records fulfillment through the protected economy and releases the permitted customer hold. A later signed refund event reverses earned revenue and receipt cash while retaining processor fees and research costs. Duplicate customer/payment events cannot duplicate the order, Checkout, charge or revenue.

Two additional cases prove that paused runs retain intake without executing, unavailable research creates no offer/effect, and an actual competing central reservation prevents research dispatch despite a settled customer receipt. The unfunded research plan is then aged seven days in the explicit test database; after credit becomes available, the promise starts from the trusted gateway's actual execution timestamp, not the old plan time. Response-loss recovery preserves that original dispatch timestamp. Customer receipts, earned revenue, liabilities and operating spend remain separate throughout. The demonstration does not require a continuously polling model or paid search loop.

Run only these maintained checks with:

```sh
WIDEMIND_TEST_PYTHON=/tmp/widemind-business-demo-venv/bin/python node --test --test-concurrency=1 test/brief-business-integration.test.ts
```

Local fixture success does not establish provider readiness. Live operation requires the dedicated Gmail OAuth account, explicit OpenAI model/rates/limits, Stripe account/key/webhook ingress, matching economy tool profiles, approved network access, a controlled recipient and deployment isolation described in the setup documents. Those prerequisites must be verified separately; fixture balances must never be imported into a live run.
