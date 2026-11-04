import test from "node:test";
import assert from "node:assert/strict";
import type { FinancialSummary } from "../src/contracts/index.ts";
import {
  pilotCriteria,
  pilotOutcomes,
  pilotReadiness,
  pilotReport,
  pilotRunId,
} from "../src/runtime/pilot.ts";
import { fixture } from "./helpers.ts";

function summary(
  overrides: {
    [K in keyof FinancialSummary]?: FinancialSummary[K] | undefined;
  } = {},
): FinancialSummary {
  const result = {
    currency: "USD",
    openingCreditMicros: "5000000",
    postedMicros: "11800000",
    reservedMicros: "300000",
    holdsMicros: "0",
    spendableMicros: "11500000",
    customerReceiptsMicros: "10000000",
    earnedRevenueMicros: "10000000",
    operatingChargesMicros: "2900000",
    ownerFundingMicros: "0",
    subsidiesMicros: "1200000",
    processorFeesMicros: "300000",
    refundsMicros: "0",
    chargebacksMicros: "0",
    chargebackReversalsMicros: "0",
    paymentAdjustmentsMicros: "0",
    correctionsMicros: "0",
    testFundingMicros: "0",
    mode: "live",
    asOf: "2026-11-04T12:00:00Z",
    ...overrides,
  };
  for (const key of Object.keys(result) as (keyof typeof result)[])
    if (result[key] === undefined) delete result[key];
  return result as FinancialSummary;
}

test("pilot run identity comes from --run or the commerce setting and is validated", () => {
  assert.equal(pilotRunId(["--run", "run-1"], {}), "run-1");
  assert.equal(
    pilotRunId([], { WIDEMIND_COMMERCE_RUN_ID: "commerce-run" }),
    "commerce-run",
  );
  assert.equal(pilotRunId(["--run", "bad id"], {}), null);
  assert.equal(pilotRunId([], {}), null);
});

test("outcomes use exact integers and keep subsidies and owner funding apart from receipts", () => {
  const { outcomes, outsideSupport } = pilotOutcomes(summary());
  assert.equal(outcomes.netCustomerReceiptsMicros, "9700000");
  assert.equal(outcomes.netOperatingResultMicros, "6800000");
  assert.equal(outcomes.exhausted, false);
  assert.equal(outsideSupport.subsidiesMicros, "1200000");
  assert.equal(outsideSupport.ownerFundingMicros, "0");
  const big = pilotOutcomes(
    summary({
      customerReceiptsMicros: "123456789012345678901234567890",
      processorFeesMicros: "1",
    }),
  ).outcomes;
  assert.equal(big.netCustomerReceiptsMicros, "123456789012345678901234567889");
  const unknown = pilotOutcomes(summary({ refundsMicros: undefined })).outcomes;
  assert.equal(unknown.netCustomerReceiptsMicros, null);
  assert.equal(unknown.netOperatingResultMicros, null);
});

const delivered = {
  version: 1 as const,
  orders: 1,
  byStatus: { completed: 1 },
  paidDeliveredNotRefunded: 1,
  deliveredFullyRefunded: 0,
  overdue: 0,
};

test("criteria require an attestation for independence and fail sandbox, test or owner-funded runs", () => {
  const met = pilotCriteria(summary(), {
    attestation: null,
    outsideCosts: { entries: 0, totalMicros: "0", byCategory: {} },
    deliveries: delivered,
  });
  assert.ok(
    met
      .filter((item) => item.required)
      .every(
        (item) =>
          item.status === "met" ||
          (item.id === "independent_customer" &&
            item.status === "attestation_required"),
      ),
  );
  assert.equal(
    met.find((item) => item.id === "operating_costs_covered")?.status,
    "unknown",
  );
  const status = (s: FinancialSummary, id: string) =>
    pilotCriteria(s).find((item) => item.id === id)?.status;
  assert.equal(status(summary({ mode: "sandbox" }), "live_mode"), "not_met");
  assert.equal(
    status(summary({ openingCreditMicros: "5000001" }), "opening_exactly_5usd"),
    "not_met",
  );
  assert.equal(
    status(summary({ testFundingMicros: "1" }), "no_test_funding"),
    "not_met",
  );
  assert.equal(
    status(summary({ testFundingMicros: undefined }), "no_test_funding"),
    "unknown",
  );
  assert.equal(
    status(summary({ ownerFundingMicros: "1000000" }), "no_owner_funding"),
    "not_met",
  );
  assert.equal(
    status(summary({ customerReceiptsMicros: "0" }), "delivery_costs_covered"),
    "not_met",
  );
  assert.equal(
    status(
      summary({ operatingChargesMicros: "9700001" }),
      "delivery_costs_covered",
    ),
    "not_met",
  );
  assert.equal(pilotCriteria(null)[0]!.status, "not_met");
  const delivery = (deliveries: typeof delivered | null) =>
    pilotCriteria(summary(), {
      attestation: null,
      outsideCosts: { entries: 0, totalMicros: "0", byCategory: {} },
      deliveries,
    });
  assert.equal(
    delivery(null).find((item) => item.id === "paid_work_delivered")?.status,
    "unknown",
  );
  const refundedOnly = delivery({
    ...delivered,
    paidDeliveredNotRefunded: 0,
    deliveredFullyRefunded: 1,
    overdue: 2,
  });
  assert.equal(
    refundedOnly.find((item) => item.id === "paid_work_delivered")?.status,
    "not_met",
  );
  const overdue = refundedOnly.find(
    (item) => item.id === "no_overdue_deliveries",
  );
  assert.equal(overdue?.status, "not_met");
  assert.equal(overdue?.required, false);
});

test("report records interventions and actions, and the public view omits private identities", async () => {
  const f = fixture();
  try {
    const id = f.store.create("demo", Date.parse("2026-11-01T00:00:00Z"));
    f.store.control(id, "resume");
    assert.equal(await f.runtime.tick(), true);
    f.store.control(id, "pause");
    f.store.control(id, "resume");
    f.services.economy.health = () => ({ available: true });
    f.services.business.deliveryOutcomes = async () => delivered;
    f.services.economy.summary = async (runId) => {
      assert.equal(runId, id);
      return summary();
    };

    const report = await pilotReport({
      db: f.db,
      services: f.services,
      runId: id,
      view: "private",
    });
    assert.equal(report.run.runId, id);
    assert.equal(report.interventions.total, 3);
    assert.deepEqual(report.interventions.byKind, {
      "operator.resume": 2,
      "operator.pause": 1,
    });
    assert.equal(report.interventions.events?.length, 3);
    assert.equal(report.activity.actions, 1);
    assert.deepEqual(report.activity.byState, { completed: 1 });
    assert.ok(report.activity.byTool);
    assert.equal(report.outcomes?.netOperatingResultMicros, "6800000");
    assert.equal(report.verdict, "pending_attestation");

    const published = await pilotReport({
      db: f.db,
      services: f.services,
      runId: id,
      view: "public",
    });
    const text = JSON.stringify(published);
    assert.equal(text.includes(id), false);
    assert.equal(published.interventions.events, undefined);
    assert.equal(published.activity.byTool, undefined);
    assert.equal(published.interventions.total, 3);

    f.services.economy.summary = async () => {
      throw new Error("economy offline");
    };
    const offline = await pilotReport({
      db: f.db,
      services: f.services,
      runId: id,
      view: "private",
    });
    assert.equal(offline.outcomes, null);
    assert.equal(offline.verdict, "not_met");
    await assert.rejects(
      pilotReport({
        db: f.db,
        services: f.services,
        runId: "missing",
        view: "private",
      }),
      /not found/,
    );
  } finally {
    f.close();
  }
});

test("readiness lists every local blocker without printing secret values", async () => {
  const f = fixture();
  try {
    const id = f.store.create("demo");
    const secret = "paypal-client-secret-canary";
    const blocked = await pilotReadiness({
      env: { WIDEMIND_PAYPAL_CLIENT_ID: secret },
      config: f.config,
      operatorPasswordConfigured: false,
      services: f.services,
      db: f.db,
      runId: id,
    });
    assert.equal(blocked.ready, false);
    assert.equal(JSON.stringify(blocked).includes(secret), false);
    const ok = (result: typeof blocked, check: string) =>
      result.checks.find((item) => item.id === check)?.ok;
    assert.equal(ok(blocked, "run_exists"), true);
    assert.equal(ok(blocked, "payment_provider"), true);
    for (const check of [
      "operator_auth",
      "public_https",
      "live_effects_enabled",
      "commerce_live",
      "economy_live_opening",
      "worker_available",
    ])
      assert.equal(ok(blocked, check), false, check);

    const missing = await pilotReadiness({
      env: { WIDEMIND_PAYMENT_PROVIDER: "stripe" },
      config: f.config,
      operatorPasswordConfigured: true,
      services: f.services,
      db: f.db,
      runId: null,
    });
    assert.equal(ok(missing, "run_configured"), false);
    assert.equal(ok(missing, "payment_provider"), false);
    assert.equal(ok(missing, "operator_auth"), true);
  } finally {
    f.close();
  }
});
