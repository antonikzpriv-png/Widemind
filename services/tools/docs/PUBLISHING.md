# Conditional HTTP artifact publishing

`PublishAdapter` exposes `publish.artifact`. It performs actual bounded HTTP PUT
and verification GET requests through the gateway transport. There is no SDK,
hidden retry, implicit redirect, task-provided destination or local fake-success
backend. The owner must provision a dedicated HTTP PUT/WebDAV collection that
honors `If-None-Match: *`, supports GET of the exact uploaded bytes, and does not
transform the object. Parent collections must already exist; the adapter does not
create WebDAV collections or alter server configuration.

## Owner configuration

Place this tool entry under `tools["publish.artifact"]` in owner configuration,
outside WideMind's writable workspace:

```json
{
  "enabled": true,
  "base_url": "https://OWNER-PUBLISH-ENDPOINT.example/dedicated-collection",
  "public_base_url": "https://OWNER-PUBLIC-SITE.example/artifacts",
  "sandbox": true,
  "paid": false,
  "publication_microusd": 0,
  "hosting_allocation": "REPLACE with the owner-approved hosting allocation and any subsidy or prepaid cost accounting",
  "secret_ref": "widemind-publish",
  "auth_scheme": "bearer",
  "suffix": ".html",
  "content_type": "text/html; charset=utf-8",
  "max_artifact_bytes": 8000000,
  "max_receipt_bytes": 64000
}
```

`auth_scheme` supports `bearer`, `basic` (the secret value is `username:password`)
or explicit `none` (omit `secret_ref`). The gateway secret store supplies values;
they never appear in arguments, action state or receipts. HTTPS is required.
For controlled localhost fixtures only, `sandbox:true` plus
`allow_local_http:true` permits HTTP to `localhost`, `127.0.0.1` or `::1`.
Credential-bearing URLs, queries, fragments and traversal paths are rejected.

`public_base_url` is optional. It is an owner-asserted public mapping of the
collection, not a task-controlled URL. Without it, results are labeled private
or sandbox and contain an object key and digest. When supplied, the returned URL
joins that configured base to the verified key. Bytes are verified at the PUT
endpoint; serving the same bytes at the public mapping is the owner's deployment
responsibility. Sandbox results retain their sandbox label.

Both `sandbox` and `paid` must be explicit booleans. Development and sandbox
runtime profiles require `sandbox:true`. **Every profile, including production,
requires owner `policy.allow_external_effects:true`.** A sandbox label is an
owner assertion that the configured collection is an authorized test destination;
it does not turn a production server into a sandbox. Configure a dedicated test
collection and scoped credentials. Missing policy, endpoint or secret leaves
this adapter unavailable/forbidden independently of other tools.

### Cost allocation

`paid:true` requires positive integer `publication_microusd`; the gateway reserves
that exact bounded amount. This is the owner's fixed **per-publication** tariff
including bounded object transfer and recovery reads, not an automatically
discovered provider invoice or a generic WebDAV billing limit. Successful verified
publication reports that configured provisional charge, publication count and
bytes. Unknown outcomes retain the reservation. A paid failed attempt may incur
charges; do not classify it as free merely because GET cannot verify the object.

`paid:false` requires `publication_microusd:0` and a nonempty explicit
`hosting_allocation`. Prepaid/shared hosting and owner subsidies must be tracked
separately in run reporting. Do not configure free execution on an unpriced
metered service. Providers whose retrieval/write charges cannot fit a documented
fixed allocation need a provider-specific cost adapter before enabling this
generic publisher. Variable invoices and later adjustments remain the economy
service's responsibility.

## Input, execution and recovery

The only accepted argument is a relative artifact file in WideMind's dedicated
workspace:

```json
{"artifact_path":"site/index.html"}
```

The workspace enforces file confinement. The publisher rejects absolute paths,
traversal, caller URLs, credentials, accounts and extra arguments. File bytes must
fit `max_artifact_bytes`. The owner controls content type and suffix. The target
filename is `SHA256(action_id)-SHA256(content)<suffix>`, so actions have unique
keys and do not replace an existing offering or unrelated file.

Before PUT, the adapter saves the source path, content SHA-256, byte count,
target, unique key and normalized configuration hash, then calls
`ctx.mark_dispatched`. PUT carries `If-None-Match: *`. A response receipt is saved
before processing or verification. A bounded GET with `Accept-Encoding:identity`
must return status 200, the exact byte count and matching SHA-256 before the
action is completed. An ETag alone is insufficient. All result data remains
untrusted private task data; the gateway controls authorization and accounting.

After timeout or restart, an existing intent **never causes another PUT**.
Reconciliation GETs only the recorded immutable target. Matching bytes prove the
effect. A 404, conflict, transformed bytes, permission error or failed GET remains
uncertain; an absent object may reflect eventual consistency and never authorizes
a retry. Changed artifact bytes, input path or owner configuration reject the
original intent rather than sending somewhere else. Preserve the original source
artifact and configuration until reconciliation finishes. If operator repair is
needed, retain the old unresolved action and its liability; do not bypass it with
a fresh action ID. Cancellation is checked before file access, dispatch and each
HTTP operation. The shared transport provides timeout, byte and redirect limits.

Owner-run commands after configuring an authorized destination and policy:

```bash
widemind --config /owner/config.json secret set widemind-publish
widemind --config /owner/config.json execute publish.artifact --run-id RUN_ID --action-id 509c5ff4-836e-4479-b44e-68f1c6a91e79 --input-file /owner/publish-request.json
widemind --config /owner/config.json reconcile 509c5ff4-836e-4479-b44e-68f1c6a91e79
```

These commands were not run against a real publishing service in this task.

## Verification and sources

```bash
python -m pytest -q tests/test_publish.py
```

Tests use synthetic responses and a controlled localhost HTTP server. The server
accepts and durably stores PUT bytes, then closes the connection without a
response. A restarted context recovers through GET digest verification with
**exactly one PUT total**. Tests also cover positive paid reservations, explicit
hosting allocation, policy, immutable intent changes, 404 uncertainty, conflicts,
URL/header/path limits, secrets and cancellation. No production publication or
external account calls were made.

Request semantics were checked against official MDN documentation through the
allowed GitHub source, without bypassing destination policy:

- [MDN PUT source](https://github.com/mdn/content/blob/main/files/en-us/web/http/reference/methods/put/index.md), referencing RFC 9110 PUT semantics.
- [MDN If-None-Match source](https://github.com/mdn/content/blob/main/files/en-us/web/http/reference/headers/if-none-match/index.md), documenting `*` conditional creation and 412 on an existing object.

These sources establish HTTP request semantics. They do not verify a configured
server's conditional-write implementation, credentials, public URL mapping,
availability or billing allocation; those remain owner deployment dependencies.
