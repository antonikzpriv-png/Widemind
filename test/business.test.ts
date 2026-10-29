import test from "node:test";
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { writeFileSync } from "node:fs";
import { openDatabase } from "../src/db/database.ts";
import type {
  ActionView,
  BusinessSnapshot,
  Candidate,
  FinancialSummary,
  Json,
  ToolAction,
} from "../src/contracts/index.ts";
import { BusinessService } from "../src/modules/business/engine.ts";
import type { CommerceReader } from "../src/modules/business/engine.ts";
import { migrations } from "../src/modules/business/migrations.ts";
import { configuredScopeValidator } from "../src/modules/business/readiness.ts";
import { defaultPolicy } from "../src/modules/business/policy.ts";
import { researchBrief } from "../src/modules/business/services.ts";
import {
  consoleProjection,
  orderProjection,
} from "../src/modules/business/views.ts";
import { migrate } from "../src/db/migrate.ts";
import { fixture, receipt } from "./helpers.ts";

const scope = {
  topic: "Explicit offline business fixture",
  sections: ["Overview"],
  minSources: 2,
};
const financial: FinancialSummary = {
  currency: "USD",
  openingCreditMicros: "5000000",
  postedMicros: "5000000",
  reservedMicros: "0",
  holdsMicros: "0",
  spendableMicros: "5000000",
  customerReceiptsMicros: "0",
  earnedRevenueMicros: "0",
  operatingChargesMicros: "0",
  ownerFundingMicros: "0",
  subsidiesMicros: "0",
  mode: "sandbox",
  asOf: new Date().toISOString(),
};
function setup() {
  const f = fixture("foundation");
  migrate(f.db, [{ id: "business", contractVersion: 1, migrations }]);
  const runId = f.store.create("foundation");
  f.store.control(runId, "resume");
  let unavailable = false;
  const payments = new Map<string, Record<string, unknown>>();
  const commerce: CommerceReader = {
    async order({ runId, jobId }) {
      return unavailable
        ? { kind: "unavailable", reason: "Explicit offline dependency failure" }
        : {
            kind: "available",
            order: payments.get(jobId) ?? payment(runId, jobId),
          };
    },
  };
  const business = new BusinessService(f.db, commerce),
    actions: ActionView[] = [];
  const snapshot = (): BusinessSnapshot => ({
    runId,
    now: new Date().toISOString(),
    financial,
    actions,
    capabilities: [
      "gmail.read",
      "gmail.send",
      "research.collect",
      "commerce.checkout",
      "commerce.fulfill",
    ].map((id) => ({ id, availability: { available: true } })),
    events: f.store.pendingEvents(runId, 100),
  });
  const select = () => business.selectWork(snapshot());
  function finish(candidate: Candidate, result: Json, charge = "0") {
    const action: ToolAction = {
        ...candidate,
        version: 1,
        runId,
        actionId: randomUUID(),
      },
      r = receipt(action);
    r.result = result;
    r.usage.actualCharge.micros = charge;
    actions.push({ action, state: "completed", receipt: r });
    return action;
  }
  function seedOrder(
    id: string,
    status = "creating",
    createdAt = "2026-01-01T00:00:00.000Z",
  ) {
    const customer = business.customer(
      runId,
      "controlled@example.invalid",
      createdAt,
    );
    f.db
      .prepare(
        "INSERT INTO business_opportunities VALUES(?,?,?,?,?,?,?,?,?,?,?)",
      )
      .run(
        "op-" + id,
        runId,
        customer,
        "research-brief",
        JSON.stringify(scope),
        "inbound",
        "fixture",
        1,
        "ordered",
        "{}",
        createdAt,
      );
    f.db
      .prepare("INSERT INTO business_quotes VALUES(?,?,?,?,?,?,?,?)")
      .run(
        "quote-" + id,
        "op-" + id,
        JSON.stringify(scope),
        "2000000",
        "250000",
        86400,
        "2099-01-01T00:00:00.000Z",
        "accepted",
      );
    const p = payment(runId, id);
    f.db
      .prepare(
        "INSERT INTO business_orders(id,run_id,quote_id,customer_id,service,scope,status,price_micros,cost_micros,payment,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
      )
      .run(
        id,
        runId,
        "quote-" + id,
        customer,
        "research-brief",
        JSON.stringify(scope),
        status,
        "2000000",
        "250000",
        JSON.stringify(p),
        createdAt,
      );
    payments.set(id, p);
    return p;
  }
  return {
    ...f,
    runId,
    business,
    actions,
    snapshot,
    select,
    finish,
    seedOrder,
    payments,
    unavailable(value: boolean) {
      unavailable = value;
    },
  };
}
function payment(runId: string, jobId: string): Record<string, unknown> {
  return {
    runId,
    jobId,
    mode: "sandbox",
    currency: "USD",
    requestId: "fixture-" + jobId,
    paymentId: "pi_fixture",
    checkoutUrl: "https://checkout.example.invalid/fixture",
    priceMicros: "2000000",
    paymentStatus: "settled",
    revision: "1",
    receiptsMicros: "2000000",
    refundsMicros: "0",
    chargebacksMicros: "0",
    chargebackReversalsMicros: "0",
    processorFeesMicros: "50000",
    earnedRevenueMicros: "0",
    heldMicros: "2000000",
    pendingLiabilityMicros: "0",
    releasedMicros: "0",
    costsFinal: true,
    deliveryObligation: "unfulfilled",
    deliveryReference: null,
  };
}

test("business commands are durable, duplicate-safe and remain queued while paused", async () => {
  const f = setup();
  try {
    f.store.control(f.runId, "pause");
    const command = {
      command: "intake",
      service: "research-brief",
      scope,
      customerEmail: "controlled@example.invalid",
    };
    const first = f.business.enqueue(f.runId, "fixture-request", command),
      second = f.business.enqueue(f.runId, "fixture-request", command);
    assert.equal(second.duplicate, true);
    assert.equal(first.eventId, second.eventId);
    assert.throws(
      () =>
        f.business.enqueue(f.runId, "fixture-request", {
          ...command,
          customerEmail: "different@example.invalid",
        }),
      /conflict/i,
    );
    assert.equal((await f.select()).kind, "wait");
    assert.equal(
      f.business.all("SELECT * FROM business_opportunities").length,
      0,
    );
    f.store.control(f.runId, "resume");
    const selected = await f.select();
    assert.equal(selected.kind, "work");
    assert.equal(
      f.business.all("SELECT * FROM business_opportunities").length,
      1,
    );
    await f.select();
    assert.equal(f.business.all("SELECT * FROM business_quotes").length, 1);
  } finally {
    f.close();
  }
});
test("paid-cache unavailability and stop during observation cannot dispatch fulfillment", async () => {
  const f = setup();
  try {
    f.seedOrder("active");
    f.unavailable(true);
    assert.equal((await f.select()).kind, "wait");
    assert.equal(
      f.business.one(
        "SELECT delivery_due_at FROM business_orders WHERE id='active'",
      )?.delivery_due_at,
      null,
    );
    const stopped = new BusinessService(f.db, {
      async order() {
        f.store.control(f.runId, "stop");
        return { kind: "available", order: payment(f.runId, "active") };
      },
    });
    assert.equal((await stopped.selectWork(f.snapshot())).kind, "wait");
    assert.equal(
      f.business.one("SELECT status FROM business_orders WHERE id='active'")
        ?.status,
      "creating",
    );
  } finally {
    f.close();
  }
});
test("research authorization precedes the firm deadline and commitment", async () => {
  const f = setup();
  try {
    f.seedOrder("active");
    const selected = await f.select();
    assert.equal(selected.kind, "work");
    if (selected.kind !== "work") return;
    assert.equal(selected.candidate.tool, "research.collect");
    assert.equal(selected.candidate.limit.maxTokens, 30000);
    if (process.env.WIDEMIND_TEST_CANDIDATE_OUTPUT)
      writeFileSync(
        process.env.WIDEMIND_TEST_CANDIDATE_OUTPUT,
        JSON.stringify(selected.candidate),
      );
    assert.ok(
      f.business.one(
        "SELECT document FROM business_settings WHERE run_id=?",
        f.runId,
      ),
    );
    assert.equal(
      f.business.one(
        "SELECT delivery_due_at FROM business_orders WHERE id='active'",
      )?.delivery_due_at,
      null,
    );
    const sources = [
      { url: "https://example.com/a", title: "Source A", excerpt: "Fixture A" },
      { url: "https://example.org/b", title: "Source B", excerpt: "Fixture B" },
    ];
    const executionStartedAt = new Date().toISOString();
    const completed = f.finish(
      selected.candidate,
      {
        sources,
        executionStartedAt,
        sections: [
          {
            heading: "Overview",
            text: "Explicit fixture summary",
            sourceUrls: sources.map((source) => source.url),
          },
        ],
      },
      "125000",
    );
    f.db
      .prepare(
        "INSERT INTO runtime_actions(id,run_id,work_key,state,action_json,created_at,updated_at) VALUES(?,?,?,'completed',?,?,?)",
      )
      .run(
        completed.actionId,
        f.runId,
        completed.workKey,
        JSON.stringify(completed),
        Date.now() - 3 * 86400000,
        Date.now(),
      );
    const commitment = await f.business.selectWork({
      ...f.snapshot(),
      now: new Date(Date.now() + 2 * 86400000).toISOString(),
    });
    assert.equal(commitment.kind, "work");
    if (commitment.kind !== "work") return;
    assert.match(commitment.candidate.workKey, /:commitment$/);
    assert.ok(
      f.business.one(
        "SELECT delivery_due_at FROM business_orders WHERE id='active'",
      )?.delivery_due_at,
    );
    const due = f.business.one(
      "SELECT delivery_due_at FROM business_orders WHERE id='active'",
    )?.delivery_due_at;
    assert.equal(
      due,
      new Date(Date.parse(executionStartedAt) + 86400000).toISOString(),
    );
    await f.select();
    assert.equal(
      f.business.one(
        "SELECT delivery_due_at FROM business_orders WHERE id='active'",
      )?.delivery_due_at,
      due,
    );
  } finally {
    f.close();
  }
});
test("pending inbox work and cursors survive selection alongside another opportunity", async () => {
  const f = setup();
  try {
    f.business.enqueue(f.runId, "intake", {
      command: "intake",
      service: "research-brief",
      scope,
      customerEmail: "controlled@example.invalid",
    });
    f.business.enqueue(f.runId, "sync", { command: "sync-inbox" });
    const first = await f.select();
    assert.equal(first.kind, "work");
    if (first.kind !== "work") return;
    assert.equal(first.candidate.tool, "gmail.read");
    f.finish(first.candidate, { messages: [], nextCursor: "same-cursor" });
    const next = await f.select();
    assert.equal(next.kind, "work");
    if (next.kind !== "work") return;
    assert.deepEqual(next.candidate.input, { cursor: "same-cursor" });
    assert.notEqual(next.candidate.workKey, first.candidate.workKey);
    f.finish(next.candidate, { messages: [], nextCursor: null });
    const offer = await f.select();
    assert.equal(offer.kind, "work");
    if (offer.kind !== "work") return;
    assert.match(offer.candidate.workKey, /:offer$/);
    assert.equal(
      f.business.all("SELECT * FROM business_inbox_requests").length,
      2,
    );
  } finally {
    f.close();
  }
});
test("unsupported source scope and sub-cent price never produce offers", async () => {
  const validate = configuredScopeValidator({
    WIDEMIND_RESEARCH_SOURCE_URLS:
      '["https://example.com/a","https://example.org/b"]',
    OPENAI_INPUT_MICRO_USD_PER_MILLION_TOKENS: "400000",
    OPENAI_OUTPUT_MICRO_USD_PER_MILLION_TOKENS: "1600000",
  });
  assert.throws(() =>
    validate("research-brief", { ...scope, minSources: 3 }, "250000"),
  );
  assert.throws(() =>
    validate(
      "research-brief",
      {
        ...scope,
        sourceUrls: ["https://unapproved.example/a", "https://example.org/b"],
      },
      "250000",
    ),
  );
  validate("research-brief", scope, "250000");
  const f = setup();
  try {
    f.business.enqueue(f.runId, "price-policy", {
      command: "policy",
      policy: JSON.parse(
        JSON.stringify({
          ...defaultPolicy,
          prices: { "research-brief": "1000001" },
        }),
      ),
    });
    f.business.enqueue(f.runId, "intake", {
      command: "intake",
      service: "research-brief",
      scope,
      customerEmail: "controlled@example.invalid",
    });
    assert.equal((await f.select()).kind, "wait");
    assert.equal(f.business.all("SELECT * FROM business_quotes").length, 0);
  } finally {
    f.close();
  }
});
test("combined contribution score allocates the last quote budget before lower-scored work", async () => {
  const f = setup();
  try {
    const expensive = { ...researchBrief, name: "higher-contribution" };
    const business = new BusinessService(
      f.db,
      {
        async order() {
          return { kind: "unavailable", reason: "unused" };
        },
      },
      { services: [researchBrief, expensive] },
    );
    business.enqueue(f.runId, "policy", {
      command: "policy",
      policy: JSON.parse(
        JSON.stringify({
          ...defaultPolicy,
          priorityWeight: 0,
          experimentBudgetMicros: "250000",
          prices: {
            "research-brief": "1000000",
            "higher-contribution": "4000000",
          },
        }),
      ),
    });
    business.enqueue(f.runId, "low", {
      command: "intake",
      service: "research-brief",
      scope,
      customerEmail: "controlled@example.invalid",
      priority: 100,
    });
    business.enqueue(f.runId, "high", {
      command: "intake",
      service: "higher-contribution",
      scope,
      customerEmail: "controlled@example.invalid",
      priority: 0,
    });
    const selected = await business.selectWork(f.snapshot());
    assert.equal(selected.kind, "work");
    assert.equal(
      business.one(
        "SELECT service FROM business_opportunities WHERE status='offering'",
      )?.service,
      "higher-contribution",
    );
    assert.equal(
      business.one(
        "SELECT service FROM business_opportunities WHERE status='budgetWait'",
      )?.service,
      "research-brief",
    );
  } finally {
    f.close();
  }
});
test("old terminal history cannot postpone the next active paid candidate", async () => {
  const f = setup();
  try {
    for (let i = 0; i < 501; i++)
      f.seedOrder(`history-${String(i).padStart(3, "0")}`, "completed");
    f.seedOrder("z-active");
    const selected = await f.select();
    assert.equal(selected.kind, "work");
    if (selected.kind !== "work") return;
    assert.equal(selected.candidate.jobId, "z-active");
    assert.equal(selected.candidate.tool, "research.collect");
    const view = consoleProjection(f.business, f.runId, {}, financial);
    assert.equal(view.counts.orders, 502);
    assert.equal(
      view.pagination.orders &&
        (view.pagination.orders as { hasMore: boolean }).hasMore,
      true,
    );
    assert.equal(
      (view.orders ?? []).some((order) => order.id === "z-active"),
      true,
    );
    f.db
      .prepare(
        "UPDATE business_orders SET status='delivered',delivery_due_at=? WHERE id='z-active'",
      )
      .run(new Date().toISOString());
    assert.equal(
      orderProjection(f.business, f.runId, "z-active").deliveryObligationOpen,
      false,
    );
    assert.equal(
      consoleProjection(f.business, f.runId, {}, financial).counts
        .openCommitments,
      0,
    );
  } finally {
    f.close();
  }
});
test("terminal refunds refresh historical snapshots and append measured evaluations", async () => {
  const f = setup();
  try {
    const p = f.seedOrder("done", "completed");
    p.deliveryObligation = "fulfilled";
    p.earnedRevenueMicros = "2000000";
    await f.select();
    p.refundsMicros = "2000000";
    p.earnedRevenueMicros = "0";
    p.paymentStatus = "refunded";
    p.revision = "2";
    await f.select();
    const observed = orderProjection(f.business, f.runId, "done");
    assert.equal(
      (observed.payment as Record<string, unknown>).refundsMicros,
      "2000000",
    );
    assert.equal(
      f.business.all("SELECT * FROM business_memory WHERE kind='evaluation'")
        .length,
      2,
    );
    f.db
      .prepare("UPDATE business_orders SET status='cancelled' WHERE id='done'")
      .run();
    p.revision = "3";
    p.refundsMicros = "1000000";
    await f.select();
    assert.equal(
      (
        orderProjection(f.business, f.runId, "done").payment as Record<
          string,
          unknown
        >
      ).revision,
      "3",
    );
  } finally {
    f.close();
  }
});
test("terminal refund reconciliation is fair while an unpaid order remains open", async () => {
  const f = setup();
  try {
    const done = f.seedOrder("a-done", "completed"),
      waiting = f.seedOrder("z-wait", "awaitingPayment");
    done.paymentStatus = "refunded";
    done.refundsMicros = "2000000";
    done.revision = "9";
    waiting.paymentStatus = "pending";
    waiting.receiptsMicros = "0";
    for (let i = 0; i < 4; i++) await f.select();
    assert.equal(
      (
        orderProjection(f.business, f.runId, "a-done").payment as Record<
          string,
          unknown
        >
      ).revision,
      "9",
    );
  } finally {
    f.close();
  }
});
test("authenticated Gmail message deduplication is scoped to its business run", async () => {
  const f = setup();
  try {
    const result = {
      messages: [
        {
          messageId: "same-provider-message",
          threadId: "fixture-thread",
          from: "controlled@example.invalid",
          senderVerified: true,
          command: { type: "request", service: "research-brief", scope },
        },
      ],
    };
    const action = f.finish(
      {
        workKey: "business:sync-first:inbox",
        tool: "gmail.read",
        input: {},
        limit: {
          maximum: { currency: "USD", micros: "0" },
          maxDurationMs: 1000,
          maxOutputBytes: 100000,
        },
      },
      result,
    );
    await f.select();
    const second = f.store.create("foundation");
    f.store.control(second, "resume");
    const secondAction = {
        ...action,
        runId: second,
        actionId: randomUUID(),
        workKey: "business:sync-second:inbox",
      },
      r = receipt(secondAction);
    r.result = result;
    await f.business.selectWork({
      ...f.snapshot(),
      runId: second,
      events: [],
      actions: [{ action: secondAction, state: "completed", receipt: r }],
    });
    assert.equal(
      f.business.all(
        "SELECT * FROM business_opportunities WHERE run_id=?",
        f.runId,
      ).length,
      1,
    );
    assert.equal(
      f.business.all(
        "SELECT * FROM business_opportunities WHERE run_id=?",
        second,
      ).length,
      1,
    );
  } finally {
    f.close();
  }
});
test("console counts and rows retain one WAL observation during a concurrent state change", () => {
  const f = setup();
  let writer: ReturnType<typeof openDatabase> | undefined;
  try {
    f.seedOrder("active", "ready");
    f.db
      .prepare("UPDATE business_orders SET delivery_due_at=? WHERE id='active'")
      .run(new Date().toISOString());
    writer = openDatabase(f.config.database);
    const original = f.business.all.bind(f.business);
    let changed = false;
    f.business.all = (sql, ...args) => {
      const result = original(sql, ...args);
      if (!changed && sql.startsWith("SELECT * FROM business_orders")) {
        changed = true;
        writer!
          .prepare(
            "UPDATE business_orders SET status='completed' WHERE id='active'",
          )
          .run();
      }
      return result;
    };
    const view = consoleProjection(f.business, f.runId, {}, financial);
    assert.equal(view.orders?.[0]?.status, "ready");
    assert.equal(view.counts.openCommitments, 1);
    assert.equal(view.counts.completedOrders, 0);
    assert.equal(
      f.business.one("SELECT status FROM business_orders WHERE id='active'")
        ?.status,
      "completed",
    );
  } finally {
    writer?.close();
    f.close();
  }
});
test("pending offer mail retains combined goal and margin priority when both quotes fit", async () => {
  const f = setup();
  try {
    const higher = { ...researchBrief, name: "higher-contribution" };
    const business = new BusinessService(
      f.db,
      {
        async order() {
          return { kind: "unavailable", reason: "unused" };
        },
      },
      { services: [researchBrief, higher] },
    );
    business.enqueue(f.runId, "policy", {
      command: "policy",
      policy: JSON.parse(
        JSON.stringify({
          ...defaultPolicy,
          priorityWeight: 0,
          experimentBudgetMicros: "500000",
          prices: {
            "research-brief": "1000000",
            "higher-contribution": "4000000",
          },
          goalWeights: { "research-brief": 1, "higher-contribution": 3 },
        }),
      ),
    });
    business.enqueue(f.runId, "low", {
      command: "intake",
      service: "research-brief",
      scope,
      customerEmail: "controlled@example.invalid",
      priority: 1,
    });
    business.enqueue(f.runId, "high", {
      command: "intake",
      service: "higher-contribution",
      scope,
      customerEmail: "controlled@example.invalid",
      priority: 1,
    });
    const selected = await business.selectWork(f.snapshot());
    assert.equal(selected.kind, "work");
    if (selected.kind !== "work") return;
    const chosen = business.one(
      "SELECT service FROM business_opportunities WHERE id=?",
      selected.candidate.jobId!,
    );
    assert.equal(chosen?.service, "higher-contribution");
    assert.equal(business.all("SELECT * FROM business_quotes").length, 2);
  } finally {
    f.close();
  }
});
test("legacy research receipts preserve checked work without inventing a dispatch deadline", async () => {
  const f = setup();
  try {
    f.seedOrder("legacy");
    const selected = await f.select();
    assert.equal(selected.kind, "work");
    if (selected.kind !== "work") return;
    const sources = [
      { url: "https://example.com/a", title: "A", excerpt: "A" },
      { url: "https://example.org/b", title: "B", excerpt: "B" },
    ];
    f.finish(
      selected.candidate,
      {
        sources,
        sections: [
          {
            heading: "Overview",
            text: "Checked fixture",
            sourceUrls: sources.map((item) => item.url),
          },
        ],
      },
      "125000",
    );
    assert.equal((await f.select()).kind, "wait");
    const order = orderProjection(f.business, f.runId, "legacy", true);
    assert.equal(order.status, "needsReview");
    assert.equal(order.deliveryDueAt, null);
    assert.ok(order.artifact);
    assert.equal(f.actions[0]?.receipt?.usage.actualCharge.micros, "125000");
  } finally {
    f.close();
  }
});
