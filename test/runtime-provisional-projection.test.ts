/** Synthetic accounting projections; these are not provider payment records. */
import test from "node:test";
import assert from "node:assert/strict";
import type { FinancialSummary } from "../src/contracts/index.ts";
import { publicProjection } from "../src/web/projections.ts";
import { fixture } from "./helpers.ts";

function summary(): FinancialSummary {
  return {
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
    asOf: "2026-10-29T12:00:00.000Z",
  };
}

async function project(value: FinancialSummary) {
  const f = fixture("foundation");
  try {
    f.store.create("foundation");
    f.services.economy.summary = async () => value;
    return (await publicProjection(f.store, f.services)).financial;
  } finally {
    f.close();
  }
}

function detailed(): FinancialSummary {
  return {
    ...summary(),
    postedMicros: "10397000",
    spendableMicros: "7397000",
    reservedMicros: "1000000",
    holdsMicros: "2000000",
    ownerFundingMicros: "1000000",
    customerReceiptsMicros: "3000000",
    operatingChargesMicros: "400000",
    earnedRevenueMicros: "-123",
    subsidiesMicros: "-50",
    processorFeesMicros: "-50000",
    refundsMicros: "200000",
    chargebacksMicros: "100000",
    chargebackReversalsMicros: "30000",
    paymentAdjustmentsMicros: "-10000",
    correctionsMicros: "20000",
    testFundingMicros: "2000000",
    otherCashMicros: "7000",
  };
}

test("legacy summaries keep their existing shape and private provider fields are omitted", async () => {
  const value = summary();
  const untrustedExtra = Object.assign({}, value, {
    accountRef: "private-account-canary",
    rawReceipt: "private-payment-canary",
  });
  const result = await project(untrustedExtra);
  assert.deepEqual(result, value);
  assert.doesNotMatch(
    JSON.stringify(result),
    /private-account-canary|private-payment-canary|accountRef|rawReceipt/,
  );
  assert.equal(Object.hasOwn(result!, "processorFeesMicros"), false);
});

test("the complete cash breakdown preserves fee credits and signed noncash metrics", async () => {
  assert.deepEqual(await project(detailed()), detailed());
});

test("aggregate decimal strings stay exact above both JS safe integer and int64 bounds", async () => {
  for (const posted of [
    "18014398509481991",
    "9223372036854775808",
    "9".repeat(128),
  ]) {
    const value: FinancialSummary = {
      ...summary(),
      postedMicros: posted,
      spendableMicros: (BigInt(posted) - 3n).toString(),
      ownerFundingMicros: (BigInt(posted) - 5_000_000n).toString(),
      reservedMicros: "1",
      holdsMicros: "2",
    };
    assert.deepEqual(await project(value), value);
  }
});

test("negative cash liabilities and signed performance corrections are preserved", async () => {
  const value: FinancialSummary = {
    ...summary(),
    postedMicros: "-1000000",
    spendableMicros: "-6000000",
    operatingChargesMicros: "6000000",
    reservedMicros: "2000000",
    holdsMicros: "3000000",
    earnedRevenueMicros: "-200",
    subsidiesMicros: "-300",
    processorFeesMicros: "0",
    refundsMicros: "0",
    chargebacksMicros: "0",
    chargebackReversalsMicros: "0",
    paymentAdjustmentsMicros: "0",
    correctionsMicros: "0",
    testFundingMicros: "0",
    asOf: "2026-10-29T12:00:00.123456+00:00",
  };
  assert.deepEqual(await project(value), value);
});

for (const [field, value] of [
  ["postedMicros", 9_007_199_254_740_993],
  ["postedMicros", "0001"],
  ["postedMicros", "-0"],
  ["postedMicros", "+1"],
  ["postedMicros", "1.0"],
  ["postedMicros", "1e6"],
  ["postedMicros", " 1"],
  ["postedMicros", "1 "],
  ["postedMicros", "NaN"],
  ["postedMicros", ""],
  ["postedMicros", "9".repeat(129)],
  ["ownerFundingMicros", "-1"],
  ["operatingChargesMicros", "-1"],
  ["processorFeesMicros", "-0"],
  ["refundsMicros", "-1"],
  ["chargebacksMicros", "-1"],
  ["testFundingMicros", "-1"],
  ["otherCashMicros", "1e6"],
  ["mode", "test"],
  ["currency", "EUR"],
  ["asOf", "not-a-time"],
] as const) {
  test(`invalid financial projection ${field}=${String(value).slice(0, 24)} becomes unavailable`, async () => {
    const malformed = {
      ...summary(),
      [field]: value,
    } as unknown as FinancialSummary;
    assert.equal(await project(malformed), null);
  });
}

test("a mismatched spendable equation is never published", async () => {
  assert.equal(
    await project({ ...summary(), spendableMicros: "5000001" }),
    null,
  );
});

test("a complete detailed cash equation must reconcile with the posted balance", async () => {
  assert.equal(
    await project({ ...detailed(), processorFeesMicros: "-50001" }),
    null,
  );
});
