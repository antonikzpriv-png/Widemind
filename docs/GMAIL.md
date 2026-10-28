# Dedicated Gmail adapter

Implemented: a direct Gmail REST adapter and user OAuth helpers using only the
injected bounded HTTP transport, trusted private action store, encrypted secret
store, and isolated workspace contracts. No Google SDK, inherited application
default credentials, service account, or host login is used. Tests use explicit
HTTP fixtures. No Gmail login, account verification, draft, or send was performed
during development. Live capability remains dependent on owner setup and a
controlled recipient check.

## Configuration and private setup

Enable Gmail API in an owner-controlled Google Cloud project. Configure a user
OAuth client and its exact registered redirect URI for WideMind's dedicated
account. Google consent-screen publication, restricted-scope verification,
organization policies, refresh-token lifetime, and account sending limits must
be checked against current Google documentation for that project. The current
development network policy does not allow Google's documentation/OAuth/API
domains, so those setup requirements and live access were not verified here.

Owner configuration (illustrative paths and names, no credentials):

```json
{
  "control_dir": "/owner/widemind-control",
  "workspace_dir": "/agent/workspace",
  "policy": {
    "profile": "sandbox",
    "allowed_email_recipients": ["controlled-recipient@example.com"],
    "allow_any_email_recipient": false
  },
  "tools": {
    "gmail": {
      "enabled": true,
      "client_id": "YOUR_OAUTH_CLIENT_ID",
      "redirect_uri": "http://127.0.0.1:8123/oauth/callback",
      "expected_account": "YOUR_DEDICATED_GMAIL_ADDRESS",
      "client_secret_ref": "gmail.client_secret",
      "token_ref": "gmail.user_token",
      "oauth_state_ref": "gmail.pending_oauth",
      "max_pages": 5,
      "max_recipients": 20,
      "max_message_bytes": 8000000,
      "max_attachment_bytes": 8000000,
      "max_read_bytes": 2000000
    }
  }
}
```

Use three distinct configured secret references. Install the OAuth client secret
through the trusted CLI's hidden prompt:

```sh
widemind --config /owner/config.json secret set gmail.client_secret
```

Never put client secrets, tokens, authorization codes, callback URLs, or PKCE
verifiers into request JSON, shell arguments, agent files, logs, public output,
or committed configuration. Setup requires the owner-only helpers in
`widemind.tools.gmail_oauth`: `authorization_url(ctx)` creates an expiring
600-second state and PKCE S256 attempt in `oauth_state_ref` and returns a private
URL; the owner visits that URL, then `complete_authorization(ctx, callback_url)`
consumes the callback once, exchanges the code, verifies the actual provider
grant and mailbox, and saves the token in `token_ref`. These helpers take the
trusted gateway's `ToolContext`; they do not start a browser or callback server.
The owner-only module CLI uses `Gateway.setup_context("gmail")` for this flow:

```sh
python -m widemind.tools.gmail_oauth --config /owner/config.json start
python -m widemind.tools.gmail_oauth --config /owner/config.json complete
python -m widemind.tools.gmail_oauth --config /owner/config.json verify
```

`start` prints the private authorization URL; `complete` accepts the callback in
a hidden terminal prompt. For an owner-controlled input channel use
`complete --callback-stdin`; no callback-URL command argument is supported.
The CLI never sends mail and does not start a callback server.
Run setup serially under operator control. The secret interface itself does not
provide a compare-and-swap primitive for simultaneous callback consumers.

Requested scopes are exactly `gmail.readonly` and `gmail.compose`. Before every
new API operation the adapter verifies the access token's audience equals the
configured client ID, requires those provider-reported scopes, then checks
`users.getProfile(me).emailAddress` against `expected_account` (case-insensitive,
without alias/dot rewriting). Optional owner `expected_subject` pins the Google
token's immutable subject as well. `login_hint` and saved account metadata never
authorize an account. Refresh is explicit and does not replay an email request.

`availability()` is configuration diagnostics: configured tokens still report
`not_verified`, never live readiness without provider verification. A revoked
grant, wrong audience/account, missing scopes, expired offline authorization,
or unavailable secret store produces a sanitized actionable failure.

Eventual live network access requires the supported environment configuration
workflow for `accounts.google.com`, `oauth2.googleapis.com`, and
`gmail.googleapis.com`; no proxy bypass is used. TLS and injected proxy trust
remain enabled.

## Tool schema

Register `GmailAdapter` from `widemind.tools.gmail`; `spec.name` is `gmail` and
its effect is `external`. Unknown argument fields are rejected.

| `operation` | Arguments and result |
| --- | --- |
| `profile` | No other arguments; verifies configured audience/scopes/mailbox and returns private account status. |
| `list` | Optional `query`, `max_results` (1–500, default 100), `max_pages` (default 1, owner cap), `page_token`, `include_spam_trash`, `label_ids`. Returns message/thread IDs, page count, next token, and explicit truncation. |
| `read` | `message_id`; returns headers, decoded text MIME parts, attachment metadata, labels, and thread ID. HTML remains untrusted text. |
| `thread` | `thread_id`; returns the conversation's normalized messages. |
| `attachment` | `message_id`, `attachment_id`, `output_path`; retrieves bounded bytes and writes a new file through the isolated workspace API. Existing files are not overwritten. |
| `draft` | `to`, `subject`, `text` and/or `html`; optional `cc`, `bcc`, `attachments`, threading headers below. Creates an actual Gmail draft and returns separate draft/message IDs. |
| `send` | Same composition schema as `draft`; sends a newly composed message directly and records provider acceptance. Sending an existing Gmail draft, updating/deleting drafts, label mutations, and mailbox push/watch synchronization are outside this adapter version. |

Addresses are lists of bare mailbox strings; display names/groups are rejected.
Attachments are workspace objects `{ "path": "deliverable.pdf", "filename":
"deliverable.pdf", "content_type": "application/pdf" }`, with optional filename
and content type (default `application/octet-stream`). The workspace enforces
traversal/symlink confinement. Header control characters and oversized bodies,
attachments, reference chains, and MIME trees are rejected or bounded.

A reply additionally supplies `thread_id`, `in_reply_to` as an RFC Message-ID
with angle brackets, and `references` as a list of such IDs. The caller must use
the original matching Subject. Gmail's returned `thread_id` is authoritative;
the adapter does not claim Gmail threaded a message merely because a caller
requested a thread. Customer mail and HTML cannot change owner policy.

Every To/Cc/Bcc recipient for draft creation and sending must be in the owner's
allowlist. `development` and `sandbox` always enforce that list. `production`
allows arbitrary recipients only with owner `allow_any_email_recipient: true`;
otherwise the allowlist still applies. Tool arguments cannot change this policy.

## Persistence, recovery, usage, and limits

The trusted gateway must hold its exclusive action-ID lock across execute and
reconcile, including across processes. The adapter freezes exact private MIME
bytes, Date, boundaries, attachment content hashes, intended account, argument
hash, deterministic action-specific RFC Message-ID, and content fingerprint
before any mutation. It marks the action dispatched before the HTTP POST. Reusing
the same action with different arguments/account fails. No token enters receipts.

Gmail has no send idempotency key. The deterministic Message-ID is correlation,
not provider deduplication. A timeout, interruption, 5xx, malformed response,
or unknown outcome after dispatch never causes an automatic second POST.
Reconciliation searches `rfc822msgid:` with pagination and spam/trash included,
then fetches exactly one candidate's raw MIME and verifies content/recipients;
send reconciliation also requires the `SENT` label. Draft and message IDs are
distinct. Missing, multiple, truncated, modified, or inaccessible candidates
remain uncertain. Search absence and a missing draft never prove failure.
Explicit authenticated provider 4xx rejection is recorded separately.

A successful send is provider acceptance, not delivery or reading confirmation.
Provider rewriting of Date/Bcc/header/body representation may conservatively
prevent automated reconciliation; operator investigation is then required.
Separate action IDs remain separate authorized messages: callers must reuse the
original action ID for recovery, rather than trying a new action to bypass an
uncertain send. Pause/stop must preserve reconciliation access.

Each HTTP request has a remaining-deadline timeout, cancellation callback, and
response size cap; no automatic redirect or HTTP retry is requested. Once Gmail
accepts email, local cancellation cannot withdraw it. Default bounds are listed
in the config example; hard owner maxima are 50 pages, 100 recipients, 25 MB
MIME, 20 MB attachments, and 20 MB read response. MIME parsing also caps depth
at 20 and parts at 500. These local bounds are not Gmail account quota promises.

Gmail request quota is not monetary spend. Quotes are zero micro-USD for these
API calls. Usage records cumulative per-action HTTP/Gmail/OAuth attempts,
send/draft attempts, and complete response bytes, saved as private `gmail_usage`
before each call so failed/unknown attempts remain visible. They are not claimed
Google quota units or invoice records. Hosting/network/subscription allocation
belongs to the trusted operating-cost accounting boundary. No numeric current
Google project/user/daily-sending quotas were verified here.

## Verification and future controlled smoke

Local fixtures only:

```sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_gmail*.py' -v
```

Fixtures cover OAuth state/PKCE/tamper/expiry, actual grant/account checks,
redacted failures, recipient policy including Bcc, workspace attachment bounds,
read/thread MIME, pagination, private intent committed before HTTP, accepted
sends with dropped responses followed by SQLite restart/reconciliation, and
zero repeat sends when search is absent or conflicting.

After owner OAuth setup, network access, and controlled-recipient configuration,
run the gateway's actual CLI with owner-private input files. These commands are
a future smoke procedure; they were not run against Google during development:

```sh
widemind --config /owner/config.json init --run-id gmail-controlled-check
widemind --config /owner/config.json execute gmail --run-id gmail-controlled-check --action-id gmail-profile-1 --input-file /owner/gmail-profile.json
widemind --config /owner/config.json execute gmail --run-id gmail-controlled-check --action-id gmail-controlled-send-1 --input-file /owner/gmail-send.json
widemind --config /owner/config.json reconcile gmail-controlled-send-1
```

`gmail-profile.json`: `{ "operation": "profile" }`.
`gmail-send.json`: `{ "operation": "send", "to":
["controlled-recipient@example.com"], "subject": "WideMind controlled check",
"text": "Owner-authorized integration check." }`.
Then confirm receipt in that controlled inbox and read/thread/attachment behavior
through the same gateway. Preserve private receipts; report provider acceptance
and independently checked delivery separately. Do not switch to production or
send to uncontrolled recipients as part of this procedure.

## Official references used

- [Pinned Google Gmail discovery schema (revision 20261005)](https://github.com/googleapis/google-api-python-client/blob/c1bdb24533614d6fcffeb7b9a93b2fc836c4e4e0/googleapiclient/discovery_cache/documents/gmail.v1.json): scope/method fields, MIME/raw encoding, attachments, pagination/search, thread conditions, draft IDs, no send idempotency parameter.
- [Official Google Python MIME send example](https://github.com/googleworkspace/python-samples/blob/afb9e229ef04ad714a5166685eed0bf0e44abfe3/gmail/snippet/send%20mail/send_message.py) and [attachment draft example](https://github.com/googleworkspace/python-samples/blob/afb9e229ef04ad714a5166685eed0bf0e44abfe3/gmail/snippet/send%20mail/create_draft_with_attachment.py).
- [Official Google OAuth source](https://github.com/googleapis/google-auth-library-nodejs/blob/a95b8272197f35453d1647eed85be475d51474c9/src/auth/oauth2client.ts#L1251): tokeninfo POST with bearer header, granted scopes, audience/subject fields.
- [Official Google Python credentials](https://github.com/googleapis/google-auth-library-python/blob/2ea24b03436765fa3cf279ce148482ff6332136b/google/oauth2/credentials.py): requested versus granted scopes, refresh persistence, account metadata; [authorized transport replay behavior](https://github.com/googleapis/google-auth-library-python/blob/2ea24b03436765fa3cf279ce148482ff6332136b/google/auth/transport/requests.py). This adapter avoids that implicit replay path.
- [Google sending guide](https://developers.google.com/workspace/gmail/api/guides/sending), [drafts guide](https://developers.google.com/workspace/gmail/api/guides/drafts), [OAuth web-server guide](https://developers.google.com/identity/protocols/oauth2/web-server), and [quota guide](https://developers.google.com/workspace/gmail/api/reference/quota) are operator setup references; current contents were unavailable under this environment's network policy and are not represented as verified.
