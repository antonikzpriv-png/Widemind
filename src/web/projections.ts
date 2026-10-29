import { callDependency } from "../runtime/dependencies.ts";
import type { Services } from "../modules/registry.ts";
import type {
  FinancialSummary,
  PublicWebsiteData,
} from "../contracts/index.ts";
import { Store, publicRun } from "../runtime/store.ts";
function financialProjection(
  summary: FinancialSummary | null,
): FinancialSummary | null {
  if (!summary) return null;
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
    processorFeesMicros,
    refundsMicros,
    adjustmentsMicros,
    ownerFundingMicros,
    subsidiesMicros,
    mode,
    asOf,
  } = summary;
  return {
    currency,
    openingCreditMicros,
    postedMicros,
    reservedMicros,
    holdsMicros,
    spendableMicros,
    customerReceiptsMicros,
    earnedRevenueMicros,
    operatingChargesMicros,
    ...(processorFeesMicros !== undefined ? { processorFeesMicros } : {}),
    ...(refundsMicros !== undefined ? { refundsMicros } : {}),
    ...(adjustmentsMicros !== undefined ? { adjustmentsMicros } : {}),
    ownerFundingMicros,
    subsidiesMicros,
    mode,
    asOf,
  };
}
export async function publicProjection(
  store: Store,
  services: Services,
  dependencyMs = 5000,
  publicRunId?: string,
): Promise<PublicWebsiteData> {
  const runs = store.runs();
  const latest = publicRunId
    ? runs.find((run) => run.id === publicRunId)
    : runs[0];
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
    financialRunId: latest?.id ?? null,
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
