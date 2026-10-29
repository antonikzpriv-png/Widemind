# Bounded HTTP transport

This tools slice implements a transport using Python's standard library and the existing
`HTTPResponse` and `ToolError` contracts.
Local HTTP/TLS fixture checks passed; live provider access was not attempted.

## API

```python
from widemind.http import HTTPTransport

transport = HTTPTransport(policy={"network": {"public_proxy_guarded": False}})
response = transport.request(
    "GET",
    "https://example.org/research",
    timeout=20,
    max_bytes=1_000_000,
    public_only=True,
)
```

The public interface is
`HTTPTransport(policy=None).request(method, url, headers=None, body=None,
timeout=30, max_bytes=2_000_000, cancel=None, public_only=False)`.
`body` contains bytes; response headers have lowercase names. The optional
`cancel` callback raises when execution must stop. Adapters normally pass
`ctx.check`, a bounded timeout from `ctx.remaining()`, and the operation's byte
limit. Cancellation and deadlines apply during resolution, connection, TLS,
request transmission, response headers, and body reads.

`public_only=False` supports endpoints selected by trusted owner provider
configuration. It is not a permission for tool inputs to select internal URLs.
Adapters accepting public URLs from task inputs must use `public_only=True`.
Only owner-loaded policy can grant transport exceptions; downloaded content,
tool arguments, and model output cannot alter it.

## Public destination checks

Public-only requests validate every DNS answer as a global unicast address.
Loopback, private, shared, link-local, multicast, unspecified, reserved, and
unsafe transition addresses are rejected. Checking only `is_global` is
insufficient: some multicast addresses pass that property. A mixed set of
public and prohibited DNS answers is rejected in full.

For a direct connection, the transport connects to one validated numeric
socket address without resolving the hostname again. The original canonical
hostname remains the HTTP Host authority and, for HTTPS, the TLS SNI and
certificate verification hostname. Alternate addresses are not retried after a
connection fails. Invalid URLs, user information in URLs, unsafe header values,
and caller overrides of transport-owned authority/framing headers are rejected
before sending a request.

## Environment proxies and owner policy

The transport preserves inherited proxy and certificate trust configuration.
It honors inherited `NO_PROXY` normally and never modifies environment variables
or disables a selected proxy to obtain a direct route. HTTP and HTTPS proxies
are supported; HTTPS requests use CONNECT, and HTTPS proxies require verified
TLS to the proxy plus verified TLS to the destination. Nested TLS uses
`ssl.MemoryBIO` rather than disabling verification.

A proxy resolves or connects to the destination independently of the local
resolver. Local validation therefore cannot pin the proxy's destination IP.
When a proxy is selected for a public-only request, the transport fails closed
unless the owner sets `network.public_proxy_guarded` to the Boolean `True`, or
the URL matches an exact trusted loopback fixture origin described below. A
guarded proxy setting attests that the proxy itself blocks prohibited resolved
addresses at connection time. The transport does not independently verify that
attestation.

```json
{
  "network": {
    "public_proxy_guarded": false,
    "trusted_fixture_origins": ["http://127.0.0.1:8765"]
  }
}
```

`trusted_fixture_origins` permits controlled local integration servers. Each
entry is an exact canonical origin: scheme, hostname, and effective port, with
no credentials, path, query, fragment, or wildcard. Every DNS answer for the
fixture must be loopback. An origin exception never covers another port or
host, and never changes proxy routing; inherited `NO_PROXY` determines whether
that fixture uses a direct connection. Keep the list empty outside controlled
local tests. Set `public_proxy_guarded` to `true` only when the selected proxy
actually provides the stated destination guard.

## Bounds and outcomes

Requests have one absolute monotonic deadline, capped at 600 seconds, rather
than a fresh timeout for every stage or body chunk. Request and response body
limits are capped at 64 MiB. Response headers have a 64 KiB budget; a separate
wire budget bounds framing and other bytes to four times the configured body
limit plus 1 MiB. These are resource limits, not provider upload allowances.

DNS lookup runs in daemon workers with at most 32 outstanding lookups.
Platform resolver calls cannot be forcibly killed; a timed-out or cancelled
caller returns while its resolver may continue occupying a slot. Exhausting
the bounded slots fails rather than creating unlimited threads. A resolver
worker does not connect or send a request after its caller stops waiting.

The transport performs one request attempt, follows no redirects, and performs
no automatic content decompression. A 3xx response is returned for the adapter
to handle explicitly. Bounded informational responses such as 103 Early Hints
are consumed before the final response; protocol upgrades are unsupported.
Truncated declared-length bodies and abrupt TLS termination of close-delimited
bodies fail instead of appearing complete. Response bodies remain untrusted bytes. Exceptions use
bounded safe messages without URLs, headers, bodies, proxy credentials, or
provider response contents.

A timeout, cancellation, connection loss, or byte-limit failure after dispatch
cannot prove that an external mutation did not happen. Adapters must preserve
their durable intent and reconcile before another attempt. Cancellation stops
supported local work; it does not undo an accepted email, payment, or publish.

## Local verification

Run transport fixtures without contacting public services:

```bash
PYTHONPATH=src python -m unittest discover -s tests -p 'test_http*.py' -v
```

Seventeen fixture tests cover DNS rebinding and mixed answers, original Host/SNI,
proxy selection and exact fixture exceptions, slow DNS/headers/body,
cancellation, single-attempt POST behavior, redirects, framing/header
validation, byte limits, and secret-free errors. Direct TLS, HTTP CONNECT, and
nested TLS through an HTTPS proxy use actual loopback servers and temporary
test certificates. Generating those certificates needs the `openssl` command;
the TLS test class is skipped when it is absent. Other transport checks need
only Python's standard library. Live provider connectivity
requires separately configured network access and credentials.
