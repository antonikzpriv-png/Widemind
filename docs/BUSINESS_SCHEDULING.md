# Scheduled incoming mail without a paid planning loop

The web process does not drive business execution. Run the existing independent worker, protected economy HTTP service and payment reconciliation worker using the setup documents. Mailbox synchronization is a bounded, centrally authorized `gmail.read` action proposed after an authenticated inbox-sync command; waiting never invokes a model to search for work.

For a trusted host scheduler or verified Gmail push integration, queue one sync command from the deployment account that already owns the protected runtime database:

```sh
npm run business:sync-inbox -- --run-id EXISTING_FOUNDATION_RUN_UUID
```

Use the same operator-owned environment as the worker, including `WIDEMIND_BUSINESS_ENABLED=true` and `WIDEMIND_PROFILE=foundation`. The command performs no provider request, funding, fulfillment or Resume. It validates the existing run, records a durable event, and exits. Paused/stopped runs retain the event until explicitly resumed. The worker checks actual configured capabilities and central authorization before reading mail. Missing providers remain unavailable.

Invoke that one-shot command through cron or a systemd timer every five minutes; do not run a shell or model polling loop. Its default request identity is derived from the run and current five-minute UTC interval, so duplicate invocations in that interval deduplicate without losing future intervals. `--interval-seconds` accepts 60–86400 for a matching externally configured cadence. For provider push, use `--request-id VERIFIED_PROVIDER_EVENT_ID`; authenticate and deduplicate the provider notification in the trusted ingress before invoking the command. This is a scheduler entrypoint, not an implemented unauthenticated Gmail webhook.

Run a compiled deployment with `node --env-file-if-exists=.env dist/modules/business/sync-main.js` and the same arguments. Keep the source/build, environment, database and scheduler account outside the writable agent workspace. This CLI grants no new remote authority or generic event type; it can queue only inbox synchronization.

Inbox requests and continuation cursors persist independently of other opportunities and orders, so acknowledging the scheduling event cannot silently drop a sync. Gmail messages are independently authenticated by the adapter; their content cannot change capabilities, business policy or accounting. Delivery requires the explicit authenticated acknowledgment tied to the actual private-delivery action. Processor status and earned revenue remain economy-owned.
