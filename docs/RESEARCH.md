# Public-web research adapters

`ResearchAdapter` (`research.fetch`) performs actual bounded HTTP GETs through
the gateway transport. `BrowserAdapter` (`browser.read`) renders actual HTML and
JavaScript with optional Playwright Chromium. Both return private **untrusted**
task data. Page instructions, links and screenshots cannot grant permissions,
select accounts, read credentials or alter budgets.

## Inputs and results

```json
{"url": "https://example.org/article", "max_text_chars": 12000, "max_links": 20}
```

The browser also accepts `"screenshot": true`. No other arguments are accepted.
There is no arbitrary JavaScript, clicking, form submission, browser profile,
cookie import or executable-selection argument. These adapters add research
capabilities; dedicated authorized adapters handle account actions.

Results contain bounded plain text, a title, safe HTTP(S) links, final URL,
redirect chain and a source reference. References include the URL, UTC retrieval
time and SHA-256 of the fetched bytes. Browser results additionally hash the
canonical JSON containing rendered title/text/links and identify the original
document reference. This preserves what was observed; it does not authenticate
the page's claims. HTTP extraction removes scripts/styles; browser extraction
uses visible rendered text. Screenshots are fixed 1280×720 JPEGs written through
`ctx.workspace` to a private `browser/<action-hash>.jpg` artifact.

Both quotes have zero provider charge and explicitly label **owner-subsidized
local compute and network**. Usage records request/byte counts; browser usage
also records observed peak process-group RSS. This is not a claim that hosting
or bandwidth has no economic cost. No paid search service is configured. The
caller supplies candidate public URLs or follows returned links in separately
authorized reads. Search-provider integration remains a separate capability.

## Trusted owner configuration

Configuration is loaded by the gateway, outside the agent's writable workspace.
All settings below are owner-only; copying them into request arguments fails.

| Setting | Default / bound |
| --- | --- |
| `max_text_chars` | 60,000; at most 200,000 |
| `max_links` | 50; at most 200 |
| `max_response_bytes` | 2,000,000 per response; at most 8,000,000 |
| `max_redirects` | 3; at most 5 |
| `allowed_hosts` | Optional list of exact public hosts |
| `public_ports` | `[80, 443]`; owner may explicitly enable other public ports |
| `allowed_redirect_origins` | Empty; cross-origin redirects require an exact allowed origin |
| `resource_origins` | Empty; browser resources default to the main document's origin |
| `trusted_fixture_origins` | Empty; exact literal-loopback HTTP origins for controlled development fixtures |
| `browser_executable` | Optional absolute path to an installed Chromium runtime |
| `browsers_path` | Playwright's installed browser directory; otherwise runtime `PLAYWRIGHT_BROWSERS_PATH` or standard Playwright cache |
| `max_requests` | Browser: 32, at most 100, including blocked requests |
| `max_total_bytes` | Browser: 8,000,000, at most 16,000,000 |
| `max_memory_mb` | Browser process-group RSS allowance: 768; range 256–2048 |
| `max_runtime_seconds` | Browser: 30; range 1–60, also capped by the gateway deadline |
| `render_ms` | Browser wait after DOMContentLoaded: 250; at most 2,000 |
| `max_screenshot_bytes` | Browser: 750,000; at most 2,000,000 |

Requests may reduce text/link limits. They cannot raise configured limits.
Fixture origins must also be explicitly enabled by the trusted transport's
network configuration. A fixture opt-in never permits a different address or
port. Private-network browsing is otherwise unsupported.

## Network boundary

Only HTTP(S) URLs are accepted. File, about, data, FTP, userinfo, common signed
or credential-bearing query parameters, internal hostnames and non-global
literal IPs are rejected. Redirects are checked explicitly, capped, and cannot
downgrade HTTPS to HTTP. An unexpected final URL from the transport fails closed.

Every fetch passes `public_only=True` to the gateway transport. The transport
must validate actual resolved destination addresses and pin the chosen address
through connection establishment while preserving TLS hostname verification.
Preflight DNS alone does not stop rebinding. With an inherited proxy, the gateway
must fail closed unless trusted owner configuration declares an egress proxy
that enforces equivalent destination restrictions. Network access can remain
unavailable under a managed environment policy; these adapters do not bypass it.

Browser HTTP requests are intercepted and relayed over a bounded private pipe
to the parent's same transport. Browser cookies, authorization headers,
referrers and page-selected headers are never forwarded. Only GET is allowed.
Service workers, WebSockets, frames, popups, downloads, form actions, worker
scripts and unsolicited main-frame navigation are blocked. Browser traffic
missed by interception encounters an unresolvable fail-closed proxy; Chromium
DNS overrides, disabled QUIC and non-proxied WebRTC restrictions provide further
defense. Cross-origin resources require exact owner configuration. Returning a
link does not fetch it.

## Browser runtime and isolation

```sh
python -m pip install '.[browser,test]'
python -m playwright install chromium
PYTHONPATH=src python -m pytest -q tests/test_research.py tests/test_browser.py
```

The worker runs in a disposable directory with a temporary HOME and profile,
a fixed environment whitelist, and no inherited proxy credentials, service
credentials, host browser profile or control-plane handles. The Chromium OS
sandbox remains enabled. Missing Playwright, missing Chromium or a broken Linux
sandbox returns actionable `unavailable`; HTTP research and unrelated adapters
remain usable. A trusted `browser_executable` may select a compatible installed
Chromium. No fallback disables its sandbox.

One browser runs per gateway process. The parent checks authorization,
cancellation, deadline and pipe size; an independent supervisor samples
process-group RSS and deadline even while the parent waits for HTTP. The parent
kills the isolated process group on exit or error. Chromium gets a bounded
JavaScript heap and renderer count; the child also limits CPU, open files, core
dumps and file size. RSS is sampled every 50 ms, so a sudden
allocation can overshoot before termination. Deploy the gateway in a dedicated
OS/container sandbox with an aggregate memory/process limit for a hard kernel
ceiling and protection against a compromised browser. No in-process adapter
can replace that host isolation. Transport calls themselves use short bounded
timeouts and the gateway's cancellation callback.

## Verification and limitations

Tests use actual HTTP against an explicitly trusted loopback fixture, with no
internet provider or paid call. They verify extraction, hashes, redirect guards,
output limits, cancellation/revocation, credential/private URL rejection and
browser subrequest policy. The browser integration test uses actual Playwright
and actual fixture HTTP when a working sandboxed Chromium is installed; it skips
with its dependency error otherwise. A real deliberately stalled child process
checks cancellation and cleanup. Other tests of browser policy are unit
tests and are not evidence of a live browser render.

In the managed development environment on 2026-10-28, the Playwright Python
package was present. Its official Chromium download was denied by the network
policy (`403 Domain forbidden` for `cdn.playwright.dev`). Installed
`/usr/bin/chromium` could not start with its OS sandbox because its SUID sandbox
helper was not configured. Browser live verification therefore remains blocked
on a usable sandboxed runtime; no unsafe fallback was used. The implementation
was checked against the official Playwright Python generated API:
https://github.com/microsoft/playwright-python/blob/main/playwright/sync_api/_generated.py

A further local probe on 2026-10-29 found that the executor has
`NoNewPrivs: 1`, the installed setuid helper is owned by UID 65534 rather than
root, and private user-namespace UID-map writes fail with a read-only filesystem
error. Copying or chmodding that helper cannot establish a usable host sandbox.
An existing local Docker image successfully created a user namespace and its
private UID mapping when run as UID 1000 with the
[official Playwright seccomp profile](https://github.com/microsoft/playwright/blob/main/utils/docker/seccomp_profile.json).
That probe retained `--network=none`, dropped all capabilities, enabled
`no-new-privileges`, used a read-only root filesystem, and bounded memory and
process count. The profile enables namespace syscalls while retaining its
default-deny action; no privileged mode, host networking or unconfined seccomp
was used. There was no local Chromium-containing container image, so this is
namespace feasibility evidence, not a verified container browser render. A
dedicated browser runner with an installed Chromium runtime and these sandbox
prerequisites remains an external deployment dependency.

HTML/JSON/text/CSV extraction is supported by HTTP; browser main documents are
HTML or plain text. PDF/binary extraction, logged-in browsing, uploads and
search-provider credentials are not implemented. JavaScript-heavy sites may
need owner-approved resource origins or more rendering time within the bounds.
HTTP GETs can still be observed by the destination; never put private task data
or secrets in research URLs. Interrupted reads return `uncertain` during
reconciliation because no provider operation status proves their outcome. A
fresh read always requires its own gateway authorization.
