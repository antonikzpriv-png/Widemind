# Integrated conversation transport and model reservations

This is the integrated PayPal/business service path under `services/business` and
`services/tools`. It does not change or share state with the separate root Stripe
deployment or `brief-business` module. Keep service virtual environments and
credentials separate as described in [INTEGRATED_SETUP.md](INTEGRATED_SETUP.md).

## Authorizing a conversation tool

`EconomyClient.reserve_conversation` reserves the actual durable tool action ID
without requiring or inventing a customer order. Required arguments are `run_id`,
`conversation_id`, `action_id` and integer `maximum_micro_usd`. Optional `tool`
defaults to `ai.generate`; optional `max_tokens` defaults to 2000 (1–16000).
The service checks the selected run mode and binds the reservation to the exact
tool, dedicated tools account, output-token ceiling, 120-second duration and
64-KiB output ceiling. AI requires a positive cap. Gmail read/list/thread/send
uses a genuine zero direct-charge reservation. No opening balance or alternate
wallet is created here.

The normal tools service verifies and starts the authoritative reservation,
enforces its adapter's quote and records actual usage. Provisional/unknown costs
remain reserved for reconciliation; a failed call is not free. An unknown
reservation response is recovered by observing the exact action/tool/account/run
binding rather than reserving another action. The business reservation effect's
private request stores `actual_action_id`, `tool` and `maximum_micro_usd` for this
recovery. Model pricing, keys, context limits and allowed effects remain private
owner configuration. An insufficient cap or unavailable model fails closed.

## Gmail setup and identity

Complete the existing dedicated Gmail OAuth/account verification in
[GMAIL.md](GMAIL.md) and the isolated tool setup in
[TOOLS_INTEGRATION.md](TOOLS_INTEGRATION.md). During development, configure only
controlled recipients and provider fixtures. For the optional business inbox
normalizer, the private owner Gmail adapter object may include:

```json
{
  "conversation_trusted_authserv_id": "mx.google.com",
  "conversation_batch_size": 5
}
```

The trust setting is an explicit assertion about the receiving Gmail mailbox's
header boundary. Enable it only after verifying that `mx.google.com` is its
receiving authentication service and that upstream/customer-supplied headers
cannot impersonate that boundary. The parser requires exactly one
`Authentication-Results` header from that selected service, an aligned
`dmarc=pass` result for the single From domain, and no resent identity. It rejects
ambiguous From/authentication headers and messages labelled sent/draft. A From
address or a claim inside the body alone never establishes identity. Without
this owner selection or suitable receiver evidence, `sender_verified` is false.

This header evidence is for routine conversational triage, not commercial
acceptance. The normalized record explicitly says
`commercial_acceptance: "authenticated-portal-required"`. Customers must use
their authenticated private portal to accept the exact current quote version.
Forwarded, HTML-only, ambiguous or unverifiable messages can require portal or
operator clarification. No customer text can enable this trust setting or change
recipient permissions.

## Inbox and replies

The existing `gmail.read` alias without a `message_id` reads one bounded inbox
page and fetches the actual messages through the existing Gmail adapter. The
default page holds five messages, never more than 20 or the action's derived
output bound. It checkpoints the exact Gmail IDs and next cursor before reading
message bodies, then checkpoints each completed read. Interrupted recovery reads
only the remaining frozen IDs; it does not advance the cursor past an unfinished
page, resend email or invoke a model. Explicit `gmail.read` by message ID and the
other existing Gmail operations retain their native behavior.

Normalized private task data contains message/thread IDs, From, subject, bounded
plain body, a truncation flag, sender-verification status/reason, and safe RFC
reply metadata. Body text is capped at 6000 encoded JSON bytes; HTML is not
executed or treated as instructions. The `reply` object supplies `thread_id`,
matching `subject`, `in_reply_to` and `references` for the existing `gmail.send`
alias. Gmail send intent/MIME and durable action ID are recorded before dispatch.
Replaying the same action obtains the existing receipt. A response lost after a
send remains uncertain until the existing observational reconciliation resolves
it; it is never blindly sent again. Provider acceptance remains distinct from
delivery or customer reading.

No additional polling worker is introduced. The existing runtime scheduler or
authenticated inbox notification schedules `gmail.sync`; idle scheduling does
not authorize paid model calls. Local compute, transport and hosting allocations
remain owner-provided costs, not claimed autonomous revenue.

## Fixture verification

`services/business/tests/test_conversation_transport.py` verifies exact model and
zero-cost Gmail reservations, mode rejection, input bounds and lost-response
recovery. `services/tools/tests/test_conversation_gmail.py` verifies sender trust,
bounded private body/reply data and checkpointed inbox recovery.
`test_conversation_gmail_integration.py` runs the existing OAuth/MIME adapter
through the actual integrated gateway with a controlled HTTP fixture, including
one threaded send across repeated same-ID dispatch. These are synthetic provider
responses; no real account, model inference, payment or email send was verified.
