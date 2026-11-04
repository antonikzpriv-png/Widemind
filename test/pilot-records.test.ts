import test from "node:test";
import assert from "node:assert/strict";
import type { FinancialSummary } from "../src/contracts/index.ts";
import { pilotReport } from "../src/runtime/pilot.ts";
import {
  latestAttestation,
  outsideLedgerCosts,
  recordAttestation,
  recordCost,
  usdToMicros,
} from "../src/runtime/pilot-records.ts";
import { fixture } from "./helpers.ts";

const liveSummary: FinancialSummary = {
  currency: "USD",
  openingCreditMicros: "5000000",
  postedMicros: "11800000",
  reservedMicros: "0",
  holdsMicros: "0",
  spendableMicros: "11800000",
  customerReceiptsMicros: "10000000",
  earnedRevenueMicros: "10000000",
  operatingChargesMicros: "2900000",
  ownerFundingMicros: "0",
  subsidiesMicros: "0",
  processorFeesMicros: "300000",
  refundsMicros: "0",
  chargebacksMicros: "0",
  chargebackReversalsMicros: "0",
  paymentAdjustmentsMicros: "0",
  correctionsMicros: "0",
  testFundingMicros: "0",
  mode: "live",
  asOf: "2026-11-04T12:00:00Z",
};

test("USD amounts convert to exact micro-USD", () => {
  assert.equal(usdToMicros("12.34"), "12340000");
  assert.equal(usdToMicros("0.000001"), "1");
  assert.equal(usdToMicros("7"), "7000000");
  for (const bad of ["0", "0.0", "-1", "1.2345678", "1e3", "01", " 1"])
    assert.throws(() => usdToMicros(bad), bad);
});

test("attestations and costs are append-only, and corrections cannot exceed the entry", () => {
  const f = fixture();
  try {
    const id = f.store.create("demo");
    assert.equal(latestAttestation(f.db, id), null);
    recordAttestation(f.db, {
      runId: id,
      stance: "affirmed",
      evidenceRef: "invoice-17",
    });
    recordAttestation(f.db, {
      runId: id,
      stance: "withdrawn",
      evidenceRef: "mistaken",
    });
    assert.equal(latestAttestation(f.db, id)?.stance, "withdrawn");
    assert.throws(
      () =>
        recordAttestation(f.db, {
          runId: id,
          stance: "affirmed",
          evidenceRef: " ",
        }),
      /Evidence/,
    );
    assert.throws(
      () =>
        recordAttestation(f.db, {
          runId: "missing",
          stance: "affirmed",
          evidenceRef: "x",
        }),
      /not found/,
    );

    const hosting = recordCost(f.db, {
      runId: id,
      category: "hosting",
      amountMicros: "6000000",
      periodStart: "2026-11-01",
      periodEnd: "2026-11-30",
      evidenceRef: "host-invoice-1",
    });
    recordCost(f.db, {
      runId: id,
      category: "domain",
      amountMicros: "1000000",
      periodStart: "2026-11-01",
      periodEnd: "2027-10-31",
      evidenceRef: "registrar",
    });
    const correction = recordCost(f.db, {
      runId: id,
      correctsId: hosting.id,
      amountMicros: "1000000",
      evidenceRef: "credit-note",
    });
    assert.equal(correction.amountMicros, "-1000000");
    assert.equal(correction.category, "hosting");
    assert.throws(
      () =>
        recordCost(f.db, {
          runId: id,
          correctsId: hosting.id,
          amountMicros: "5000001",
          evidenceRef: "too much",
        }),
      /exceeds/,
    );
    assert.throws(
      () =>
        recordCost(f.db, {
          runId: id,
          correctsId: correction.id,
          amountMicros: "1",
          evidenceRef: "x",
        }),
      /not found/,
    );
    assert.throws(
      () =>
        recordCost(f.db, {
          runId: id,
          category: "hosting",
          amountMicros: "1",
          periodStart: "2026-11-30",
          periodEnd: "2026-11-01",
          evidenceRef: "x",
        }),
      /precedes/,
    );
    assert.throws(
      () =>
        recordCost(f.db, {
          runId: id,
          category: "hosting",
          amountMicros: "1",
          periodStart: "2026-02-30",
          periodEnd: "2026-03-01",
          evidenceRef: "x",
        }),
      /calendar date/,
    );
    const totals = outsideLedgerCosts(f.db, id, false);
    assert.deepEqual(totals, {
      entries: 3,
      totalMicros: "6000000",
      byCategory: { hosting: "5000000", domain: "1000000" },
    });

    for (const sql of [
      "UPDATE pilot_costs SET amount_micros='1'",
      "DELETE FROM pilot_costs",
      "UPDATE pilot_attestations SET stance='affirmed'",
      "DELETE FROM pilot_attestations",
    ])
      assert.throws(() => f.db.exec(sql), /append-only/, sql);
    assert.throws(
      () =>
        f.db
          .prepare(
            "INSERT INTO pilot_costs(run_id,category,amount_micros,period_start,period_end,evidence_ref,note,recorded_at) VALUES (?,?,?,?,?,?,?,?)",
          )
          .run(id, "hosting", "-5", "2026-11-01", "2026-11-01", "x", "", 0),
      /CHECK/,
    );
  } finally {
    f.close();
  }
});

test("report reaches a met verdict only with an affirmed attestation and shows outside costs", async () => {
  const f = fixture();
  try {
    const id = f.store.create("demo");
    f.services.economy.health = () => ({ available: true });
    f.services.economy.summary = async () => liveSummary;
    f.services.business.deliveryOutcomes = async () => ({
      version: 1,
      orders: 1,
      byStatus: { completed: 1 },
      paidDeliveredNotRefunded: 1,
      deliveredFullyRefunded: 0,
      overdue: 0,
    });
    const before = await pilotReport({
      db: f.db,
      services: f.services,
      runId: id,
      view: "private",
    });
    assert.equal(before.verdict, "pending_attestation");
    assert.equal(before.attestation.status, "none");

    recordAttestation(f.db, {
      runId: id,
      stance: "affirmed",
      evidenceRef: "private-evidence-canary",
      note: "private-note-canary",
    });
    recordCost(f.db, {
      runId: id,
      category: "hosting",
      amountMicros: "8000000",
      periodStart: "2026-11-01",
      periodEnd: "2026-11-30",
      evidenceRef: "private-invoice-canary",
    });
    const report = await pilotReport({
      db: f.db,
      services: f.services,
      runId: id,
      view: "private",
    });
    assert.equal(report.verdict, "met");
    assert.equal(report.attestation.evidenceRef, "private-evidence-canary");
    assert.equal(report.outsideLedgerCosts.records?.length, 1);
    // 10.0 receipts - 0.3 fees - 2.9 charges - 8.0 hosting
    assert.equal(report.resultAfterOutsideCostsMicros, "-1200000");
    const costs = report.criteria.find(
      (item) => item.id === "operating_costs_covered",
    );
    assert.equal(costs?.status, "not_met");
    assert.equal(costs?.required, false);

    const published = await pilotReport({
      db: f.db,
      services: f.services,
      runId: id,
      view: "public",
    });
    const text = JSON.stringify(published);
    for (const canary of [
      "private-evidence-canary",
      "private-note-canary",
      "private-invoice-canary",
      id,
    ])
      assert.equal(text.includes(canary), false, canary);
    assert.equal(published.outsideLedgerCosts.totalMicros, "8000000");
    assert.equal(published.attestation.status, "affirmed");

    recordAttestation(f.db, {
      runId: id,
      stance: "withdrawn",
      evidenceRef: "doubt",
    });
    const withdrawn = await pilotReport({
      db: f.db,
      services: f.services,
      runId: id,
      view: "public",
    });
    assert.equal(withdrawn.verdict, "pending_attestation");

    f.services.economy.summary = async () => ({
      ...liveSummary,
      mode: "sandbox",
    });
    recordAttestation(f.db, {
      runId: id,
      stance: "affirmed",
      evidenceRef: "again",
    });
    f.services.business.deliveryOutcomes = async () => {
      throw new Error("business offline");
    };
    const offline = await pilotReport({
      db: f.db,
      services: f.services,
      runId: id,
      view: "public",
    });
    assert.equal(offline.deliveries, null);
    assert.equal(offline.verdict, "not_met");
    const sandbox = await pilotReport({
      db: f.db,
      services: f.services,
      runId: id,
      view: "public",
    });
    assert.equal(sandbox.verdict, "not_met");
  } finally {
    f.close();
  }
});

test("business delivery outcomes accept only the aggregate counts shape", async () => {
  const { deliveryOutcomes } = await import(
    "../src/modules/business/module.ts"
  );
  const good = {
    version: 1,
    orders: 2,
    byStatus: { completed: 1, payment_hold: 1 },
    paidDeliveredNotRefunded: 1,
    deliveredFullyRefunded: 0,
    overdue: 0,
  };
  assert.deepEqual(deliveryOutcomes(good), good);
  for (const bad of [
    null,
    [],
    { ...good, version: 2 },
    { ...good, orders: -1 },
    { ...good, overdue: 1.5 },
    { ...good, byStatus: { "Private Customer": 1 } },
    { ...good, byStatus: { completed: "1" } },
    { ...good, paidDeliveredNotRefunded: undefined },
  ])
    assert.equal(deliveryOutcomes(bad), null, JSON.stringify(bad));
  const extra = deliveryOutcomes({ ...good, customerEmail: "x@example.com" });
  assert.equal(JSON.stringify(extra).includes("example.com"), false);
});
