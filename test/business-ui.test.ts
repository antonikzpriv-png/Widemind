import assert from "node:assert/strict";
import test from "node:test";
import {
  renderBusinessPage,
  renderBusinessPublic,
} from "../src/modules/brief-business/ui.ts";
import { css, operatorFormErrorPage } from "../src/web/pages.ts";
import type { PublicWebsiteData } from "../src/contracts/index.ts";

const privateData = {
  schemaVersion: 1,
  runId: "00000000-0000-4000-8000-000000000001",
  runtimeState: "paused",
  executionProfile: "foundation",
  usesFixtures: false,
  counts: {
    opportunities: 501,
    orders: 1,
    openCommitments: 1,
    completedOrders: 0,
    inFulfillment: 1,
    awaitingPayment: 0,
    unresolvedEffects: 1,
    blockedEvents: 2,
  },
  pagination: {
    opportunities: {
      offset: 0,
      limit: 500,
      total: 501,
      hasMore: true,
      hasPrevious: false,
    },
    orders: {
      offset: 0,
      limit: 500,
      total: 1,
      hasMore: false,
      hasPrevious: false,
    },
  },
  summary: null,
  policy: {
    goals: ["Useful work"],
    capabilities: ["research"],
    budgetMicros: "250000",
  },
  opportunities: [
    {
      id: "opp-1",
      customerId: "customer-1",
      status: "offered",
      scope: {
        topic: "Private <script>alert(1)</script> scope",
        sections: ["Overview"],
      },
      service: "research-brief",
      origin: "inbound",
      priority: 1,
      assessment: { feasible: true },
    },
  ],
  quotes: [
    {
      id: "quote-1",
      opportunityId: "opp-1",
      status: "offered",
      scope: { topic: "Quoted scope" },
      priceMicros: "2000000",
      costMicros: "250000",
      expiresAt: "2026-10-30T00:00:00Z",
    },
  ],
  customers: [{ id: "customer-1", email: "private-customer@example.invalid" }],
  orders: [
    {
      id: "order-1",
      customerId: "customer-1",
      status: "ready",
      service: "research-brief",
      scope: { topic: "Private order" },
      priceMicros: "2000000",
      costMicros: "250000",
      payment: { status: "settled" },
      paymentObservedAt: "2026-10-29T00:00:00Z",
      paymentSource: "cached_order_snapshot",
      deliveryObligationOpen: true,
      hasArtifact: true,
    },
  ],
  effects: [
    {
      id: "effect-1",
      state: "unresolved",
      action: "gmail.send",
      reason: "Awaiting evidence",
    },
  ],
  events: [
    {
      id: "event-1",
      kind: "payment.changed",
      state: "pending",
      receivedAt: "2026-10-29T00:00:00Z",
    },
  ],
  integrations: [
    {
      name: "Gmail",
      status: "unavailable",
      detail: "Dedicated account not configured",
    },
  ],
};

test("six server-rendered business views are navigable and escape private task content", () => {
  for (const view of [
    "overview",
    "opportunities",
    "orders",
    "finances",
    "recovery",
    "settings",
  ]) {
    const html = renderBusinessPage(privateData, 'private-csrf-"<&', { view });
    assert.match(html, /WideMind workspace/);
    assert.match(html, new RegExp(`view=${view}`));
    assert.match(html, /aria-current="page"/);
    assert.doesNotMatch(
      html,
      /<script|localStorage|sessionStorage|onclick|Bearer /,
    );
    assert.doesNotMatch(html, /<script>alert/);
  }
  const opportunities = renderBusinessPage(privateData, "csrf", {
    view: "opportunities",
  });
  assert.match(
    opportunities,
    /Private &lt;script&gt;alert\(1\)&lt;\/script&gt; scope/,
  );
  assert.match(opportunities, /name="requestId" value="[a-f0-9-]{36}"/);
  assert.match(opportunities, /name="csrf" value="csrf"/);
  assert.match(
    opportunities,
    /action="\/api\/modules\/brief-business\/command"/,
  );
  assert.match(opportunities, /collection=opportunities&amp;offset=500/);
  assert.match(opportunities, /of 501/);
});

test("financial absence stays unavailable and exact micro-USD never becomes invented cash", () => {
  const absent = renderBusinessPage(privateData, "csrf", { view: "finances" });
  assert.match(absent, /Accounting unavailable/);
  assert.doesNotMatch(absent, /\$5\.00/);
  const html = renderBusinessPage(
    {
      ...privateData,
      summary: {
        spendableMicros: "100001",
        customerReceiptsMicros: "2000000",
        earnedRevenueMicros: "0",
        operatingChargesMicros: "1",
        mode: "sandbox",
        asOf: "2026-10-29T00:00:00Z",
      },
    },
    "csrf",
    { view: "finances" },
  );
  assert.match(html, /\$0\.100001/);
  assert.match(html, /\$0\.000001/);
  assert.match(html, /Cash receipts and earned revenue are separate/);
  assert.match(html, /Sandbox totals represent test funds/);
});

test("private deliverable details render only an explicitly selected order and remain inert text", () => {
  const ordinary = renderBusinessPage(privateData, "csrf", { view: "orders" });
  assert.match(ordinary, /Open private deliverable/);
  assert.match(ordinary, /orderId=order-1/);
  assert.doesNotMatch(ordinary, /private-artifact-marker/);
  const selected = renderBusinessPage(
    {
      ...privateData,
      selectedOrder: {
        scope: { topic: "Selected work" },
        artifact: {
          filename: "brief.md",
          mediaType: "text/markdown",
          content: "private-artifact-marker <img onerror='bad'>",
          checks: { scope: true },
        },
      },
    },
    "csrf",
    { view: "orders", orderId: "order-1" },
  );
  assert.match(selected, /private-artifact-marker &lt;img/);
  assert.doesNotMatch(selected, /<img onerror/);
  assert.match(selected, /Validated content/);
});

test("public website uses only curated content, provides optional user-sent mailto, and never serializes private fields", async () => {
  const data: PublicWebsiteData = {
    version: 1,
    runs: [],
    financial: null,
    offerings: [
      {
        id: "research",
        title: "Sourced brief",
        description: "An agreed scope",
      },
    ],
    activity: [],
    availability: { available: false, reason: "Not configured" },
  };
  const html = await renderBusinessPublic(
    Object.assign({}, data, {
      private: "private-customer@example.invalid",
      artifacts: ["private-artifact-marker"],
    }),
    "widemind@example.invalid",
  );
  assert.match(html, /One mind/);
  assert.match(html, /mailto:widemind%40example.invalid/);
  assert.match(html, /Nothing is sent without your own explicit send action/);
  assert.doesNotMatch(html, /private-customer|private-artifact-marker|<script/);
  const unavailable = await renderBusinessPublic(
    data,
    "bad\r\nBcc:someone@example.invalid",
  );
  assert.doesNotMatch(unavailable, /mailto:/);
  assert.match(unavailable, /Customer contact not connected/);
});

test("form errors are escaped and the shared visual system stays monochrome and responsive", () => {
  const html = operatorFormErrorPage(
    { error: "<script>provider failure</script>" },
    "/operator/modules/brief-business/console",
  );
  assert.match(html, /&lt;script&gt;provider failure/);
  assert.doesNotMatch(html, /<script>/);
  assert.match(css, /--bg:#000/);
  assert.match(css, /#242424/);
  assert.match(css, /max-width:520px/);
  assert.match(css, /focus-visible/);
});

test("paymentHold recovery controls and camelCase stages remain visible in the real engine schema", () => {
  const html = renderBusinessPage(
    {
      ...privateData,
      orders: [{ ...privateData.orders[0], status: "paymentHold" }],
    },
    "csrf",
    { view: "orders" },
  );
  assert.match(html, /name="command" value="resolve-hold"/);
  assert.match(html, /Queue hold resolution/);
  assert.match(html, /payment Hold/);
  const offered = renderBusinessPage(privateData, "csrf", {
    view: "opportunities",
  });
  assert.doesNotMatch(offered, /Queue reassessment/);
  const review = renderBusinessPage(
    {
      ...privateData,
      opportunities: [
        { ...privateData.opportunities[0], status: "needsReview" },
      ],
    },
    "csrf",
    { view: "opportunities" },
  );
  assert.match(review, /Queue reassessment/);
});
