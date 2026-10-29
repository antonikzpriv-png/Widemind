# Integrated research search

`research.search` uses an owner-configured real HTTP endpoint. It accepts only
`{"query":"topic to investigate","max_results":5}`. The query is bounded to
400 characters, results to 20 (or the lower owner maximum), and each action sends
at most one request. Search returns private untrusted task data, never authority
to change accounts, capabilities or budgets. The integrated tools service uses
the authenticated economy reservation; this adapter creates no wallet or credit.

Choose one provider in the private owner tools configuration. Owner-hosted
SearXNG is the default recommendation when its hosting and upstream traffic are
explicitly subsidized:

```json
{
  "research.search": {
    "enabled": true,
    "provider": "searxng",
    "endpoint": "https://search.owner.example/search",
    "request_microusd": 0,
    "allocation": "Owner-subsidized SearXNG hosting, compute, network and upstream queries",
    "max_results": 10,
    "max_response_bytes": 256000,
    "max_output_bytes": 128000,
    "request_timeout_seconds": 15
  }
}
```

SearXNG must enable the JSON format in its server settings. The adapter uses
GET with `q`, `format=json`, `categories=general`, and `pageno=1`; it caps the
returned list locally. A protected owner proxy may additionally use
`secret_ref` for a bearer token. No public SearXNG instance or working deployment
is selected automatically.

Brave Search is an optional paid integration:

```json
{
  "research.search": {
    "enabled": true,
    "provider": "brave",
    "endpoint": "https://api.search.brave.com/res/v1/web/search",
    "secret_ref": "dedicated-brave-search-key",
    "request_microusd": 5000,
    "allocation": "EXAMPLE ONLY: replace with an owner-certified upper bound and fixed allocation per request",
    "max_results": 10
  }
}
```

The example amount is synthetic, not a verified Brave price. Replace it using
the provisioned account's actual contract before enabling the capability. The
positive integer request maximum is quoted before execution and reserved by the
gateway. The adapter sends `X-Subscription-Token` from the private secret store,
with GET `q`, `count`, `result_filter=web`, and `text_decorations=false`. API keys
never appear in arguments, URLs, result data or saved intent. The provider can
observe the query, so task data must not put unrelated credentials in it.

Owner `policy.allow_external_effects` must permit network use. Every request
uses the shared transport with `public_only=True`, bounded bytes and timeout,
and cancellation checks. Redirects are never followed. HTTPS is required except
for explicitly owner-approved controlled loopback HTTP origins: both
`allow_local_http:true` and exact `trusted_fixture_origins` are needed, along
with the transport's separate `policy.network.trusted_fixture_origins`.
Untrusted arguments cannot enable these settings. Inherited proxy policy is
preserved; public-only requests require direct DNS-pinned validation or an
owner-certified SSRF-enforcing egress proxy. Missing setup is unavailable.

The maximum response is 512,000 bytes; the default is 256,000. Output defaults
to 128,000 bytes, with a hard configuration ceiling of 1,000,000. Each result
contains `url`, bounded plain-text `title` and `snippet`, plus optional
`published_date`/`date_basis`. Invalid, credential-bearing and private literal
URLs are filtered without fetching them. Exact controlled fixture URLs require
the same owner opt-in. DNS is checked only if a result is later fetched through
`research.fetch`; search never fetches result pages itself.

References identify both the hashed raw search response and each hashed result
record, with UTC retrieval time and `kind:search_result`. These are search-index
observations, not evidence that a source page was read. SearXNG publication dates
use `date_basis:searxng:publishedDate`. Brave's ISO page-age field is labelled
`brave:page_age_unverified`; it is weaker date evidence. Fetch timestamps,
relative text such as "yesterday", and malformed dates do not become publication
dates. `research.fetch` separately exposes validated HTML publication meta or
`time datetime` values with their explicit source basis. Generic HTML time/date
fields can describe events; all such dates remain untrusted. Last-Modified is
never labelled publication evidence.

Before dispatch, the adapter durably records the exact request/config hash and
unknown usage for paid requests. After a complete HTTP response it saves request
ID, bounded raw response, hash and measured request/byte counts. Known fixed
request allocation is saved before raw-body storage, so parsing or storage
failure cannot erase measured liability. Usage is provisional and explicitly
does not claim invoice verification. Unknown transport outcomes retain unknown
paid usage. Restart/reconciliation reads saved receipts only; it never repeats
HTTP, searches by guessed ID, or interprets a failed lookup as proof of no charge.
There is no provider invoice lookup in this slice, so unresolved charges require
the gateway/operator's existing reconciliation process.

Verified protocol sources (official GitHub repositories):

- [SearXNG Search API](https://github.com/searxng/searxng/blob/master/docs/dev/search_api.rst)
- [Brave API endpoint, parameters and authentication](https://github.com/brave/brave-search-mcp-server/blob/main/src/BraveAPI/index.ts)
- [Brave web result types and date semantics](https://github.com/brave/brave-search-mcp-server/blob/main/src/tools/web/types.ts)

Run in the isolated integrated tools environment, never the root standalone
package namespace:

```sh
PYTHONPATH=services/tools/src .venvs/tools/bin/python -m pytest -q services/tools/tests/test_search.py services/tools/tests/test_research.py
```

Tests use actual bounded HTTP against controlled loopback endpoints, a real
SQLite action journal and encrypted secret store. Provider payloads, credentials
and tariffs are explicitly synthetic. They verify output/date handling,
credential/URL boundaries, cancellation, paid allocation, lost responses,
receipt-storage failure and restart without another HTTP request. No live search
provider, account, payment or independent revenue has been verified.
