# Home-page activity constellation

The public home page (`/`) opens with a full-viewport, pure-black constellation of glowing white dots. Both the default headquarters and the optional brief-business headquarters use the same component; the existing offerings, financial summary, contact flow and activity remain below it.

Each recorded runtime action adds one dot and one connecting line. Its identity survives refreshes and receipt updates. Lines carry exact USD costs, including micro-dollar charges. Select a dot with the pointer or keyboard to see its category, execution state, cost and UTC timestamp. **Connections & costs** provides an accessible text alternative and remains server-rendered without JavaScript.

Costs come only from recorded runtime receipts:

- **Pending** means no charge receipt is available; it does not mean free.
- **Unavailable** covers missing or malformed historical cost records.
- `~` identifies provisional usage; `*` identifies a recorded charge still awaiting settlement. The text list and selected detail expand these labels.
- A settled `$0.00` is an actual recorded zero-cost action. Business handoffs report orchestration costs only; nested service charges are accounted for in the economy summary, not attributed to that zero-cost handoff.

The graph shows the latest 48 actions for the configured public/commerce run (`WIDEMIND_COMMERCE_RUN_ID`, mapped to `RuntimeConfig.publicRunId`). Without an explicitly configured identity it uses the most recently created run, matching the existing public financial projection. A missing configured run stays unavailable. Total action count and the displayed window are visible. Older database records remain intact; the visualization does not introduce a new migration or change saved action/receipt formats.

The surrounding dim dots are decorative geometry, not invented jobs. A new edge appears only for a recorded action. The core pulses only when the selected run is active and a fresh worker heartbeat exists; it stops for waiting, paused, exhausted, stopped, failed, offline and stale-connection states. The page never exposes internal reasoning, prompts, customer identifiers, raw results, provider references or credentials. Action IDs are projected as stable opaque digests and categories come from a fixed allowlist. Demo/test accounting remains labeled.

## Runtime and access boundaries

`GET /api/public/graph` is a read-only, bounded SQLite projection. It returns `PublicAgentGraph` (contract version 1), also added optionally as `agentGraph` to `/api/public`. Older website projections without this field still render. Polling uses the graph endpoint every four seconds, has a five-second request timeout, suspends while the tab is hidden, and performs no model, tool or remote economy calls. Closing the page does not affect the worker. Failed requests retain the last graph with a stale connection indicator.

`/thinking-network.js` is served from the same origin under the existing CSP. No third-party assets, inline scripts or new script permissions are required. SVG dimensions adapt to the viewport; label placement avoids collisions where space permits. Motion respects `prefers-reduced-motion` and has an independent pause control. The text cost list is the readable fallback for dense graphs on small screens.

## Verify and run

```sh
npm ci
npm run check
npm run network:browser
npm run dev
# Separate terminal for actual agent work:
npm run worker:dev
```

The browser check needs Chromium (`WIDEMIND_CHROMIUM` can select its path), a built application, and the existing owned-local-site CDP harness. It inserts explicitly synthetic action/usage records into a disposable database; it does not contact providers, create funds or claim customer work. Reports and desktop/phone screenshots are written to ignored `var/network-browser-smoke/`.

Tests cover redaction, exact costs, pending/provisional/settlement states, malformed and missing old records, stable identities, configured-run isolation, expired worker heartbeats, both public renderers, read-only provider-independent polling, live node insertion, updates without duplication, reload recovery, keyboard selection, motion preferences, connection loss/recovery and 1440/390/320-pixel layouts. Live provider credentials are unnecessary for this feature; meaningful live activity still requires a configured worker and its existing provider/accounting setup.
