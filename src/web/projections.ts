import { callDependency } from "../runtime/dependencies.ts";
import type { Services } from "../modules/registry.ts";
import type {
  FinancialSummary,
  PublicWebsiteData,
} from "../contracts/index.ts";
import { Store, publicRun } from "../runtime/store.ts";
const optionalCashFields = [
  "processorFeesMicros",
  "refundsMicros",
  "chargebacksMicros",
  "chargebackReversalsMicros",
  "paymentAdjustmentsMicros",
  "correctionsMicros",
  "testFundingMicros",
] as const;
const signedOptionalFields = new Set<string>([
  "processorFeesMicros",
  "chargebackReversalsMicros",
  "paymentAdjustmentsMicros",
  "correctionsMicros",
  "otherCashMicros",
]);
function canonicalSummaryMicros(
  value: unknown,
  signed = false,
): value is string {
  if (typeof value !== "string") return false;
  const digits = value.startsWith("-") ? value.slice(1) : value;
  return (
    digits.length <= 128 &&
    (signed ? /^(0|-?[1-9][0-9]*)$/ : /^(0|[1-9][0-9]*)$/).test(value)
  );
}
function financialProjection(
  summary: FinancialSummary | null,
): FinancialSummary | null {
  if (!summary || typeof summary !== "object" || Array.isArray(summary))
    return null;
  const {
    currency,
    openingCreditMicros,
    postedMicros,
    reservedMicros,
    holdsMicros,
    spendableMicros,
    customerReceiptsMicros,
    earnedRevenueMicros,
    operatingChargesMicros,
    ownerFundingMicros,
    subsidiesMicros,
    mode,
    asOf,
  } = summary;
  if (
    currency !== "USD" ||
    !["sandbox", "live"].includes(mode) ||
    ![
      openingCreditMicros,
      reservedMicros,
      holdsMicros,
      customerReceiptsMicros,
      operatingChargesMicros,
      ownerFundingMicros,
    ].every((value) => canonicalSummaryMicros(value)) ||
    ![
      postedMicros,
      spendableMicros,
      earnedRevenueMicros,
      subsidiesMicros,
    ].every((value) => canonicalSummaryMicros(value, true)) ||
    typeof asOf !== "string" ||
    !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)$/.test(
      asOf,
    ) ||
    !Number.isFinite(Date.parse(asOf))
  )
    return null;
  if (
    BigInt(spendableMicros) !==
    BigInt(postedMicros) - BigInt(reservedMicros) - BigInt(holdsMicros)
  )
    return null;
  const result: FinancialSummary = {
    currency,
    openingCreditMicros,
    postedMicros,
    reservedMicros,
    holdsMicros,
    spendableMicros,
    customerReceiptsMicros,
    earnedRevenueMicros,
    operatingChargesMicros,
    ownerFundingMicros,
    subsidiesMicros,
    mode,
    asOf,
  };
  for (const key of [...optionalCashFields, "otherCashMicros"] as const) {
    const value = summary[key];
    if (value === undefined) continue;
    if (!canonicalSummaryMicros(value, signedOptionalFields.has(key)))
      return null;
    Object.assign(result, { [key]: value });
  }
  // Legacy summaries may omit categories. Once the full breakdown is supplied,
  // require the same cash equation as the authoritative append-only ledger.
  if (optionalCashFields.every((key) => summary[key] !== undefined)) {
    const cash = (
      key: (typeof optionalCashFields)[number] | "otherCashMicros",
    ) => BigInt(summary[key] ?? "0");
    if (
      BigInt(postedMicros) !==
      BigInt(openingCreditMicros) +
        BigInt(ownerFundingMicros) +
        BigInt(customerReceiptsMicros) -
        BigInt(operatingChargesMicros) +
        cash("testFundingMicros") +
        cash("chargebackReversalsMicros") +
        cash("paymentAdjustmentsMicros") +
        cash("correctionsMicros") +
        cash("otherCashMicros") -
        cash("processorFeesMicros") -
        cash("refundsMicros") -
        cash("chargebacksMicros")
    )
      return null;
  }
  return result;
}
export async function publicProjection(
  store: Store,
  services: Services,
  dependencyMs = 5000,
): Promise<PublicWebsiteData> {
  const runs = store.runs();
  const latest = runs[0];
  const content = await callDependency(
    "website.publicContent",
    () => services.website.publicContent(),
    dependencyMs,
  ).catch(() => ({
    offerings: [],
    activity: [],
    availability: {
      available: false as const,
      reason: "Website content dependency unavailable",
    },
  }));
  return {
    version: 1,
    runs: runs.map(publicRun),
    financial: financialProjection(
      latest
        ? await callDependency(
            "economy.publicSummary",
            () => services.economy.summary(latest.id),
            dependencyMs,
          ).catch(() => null)
        : null,
    ),
    offerings: content.offerings.map((item) => ({
      id: item.id,
      title: item.title,
      description: item.description,
    })),
    activity: content.activity.map((item) => ({
      id: item.id,
      at: item.at,
      text: item.text,
    })),
    availability: content.availability,
  };
}
export function privateProjection(
  store: Store,
  services: Services,
  profile: "foundation" | "demo" = "foundation",
) {
  return {
    version: 1,
    runtime: store.runtimeStatus(profile),
    modules: Object.fromEntries(
      (["economy", "tools", "business", "website"] as const).map((id) => [
        id,
        services[id].health(),
      ]),
    ),
    runs: store.runs().map((run) => ({
      ...publicRun(run),
      desired: run.desired,
      reason: run.reason,
      lastError: run.last_error,
      pendingEvents: store.pendingEvents(run.id),
      pendingEventCount: Number(
        store.db
          .prepare(
            "SELECT COUNT(*) AS n FROM runtime_inbox WHERE run_id=? AND consumed_at IS NULL",
          )
          .get(run.id)?.n ?? 0,
      ),
      actions: store.actions(run.id).map((row) => ({
        id: row.id,
        state: row.state,
        attempts: row.attempts,
        reason: row.reason,
        action: JSON.parse(row.action_json),
        receipt: row.receipt_json ? JSON.parse(row.receipt_json) : null,
        reservation: row.reservation_json
          ? ((value) => ({
              reservationId: value.reservationId,
              limit: value.limit,
            }))(JSON.parse(row.reservation_json))
          : null,
      })),
      events: store.db
        .prepare(
          "SELECT kind,at,detail FROM runtime_events WHERE run_id=? ORDER BY id DESC LIMIT 50",
        )
        .all(run.id),
    })),
  };
}
