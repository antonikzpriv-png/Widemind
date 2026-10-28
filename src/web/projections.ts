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
    ownerFundingMicros,
    subsidiesMicros,
    mode,
    asOf,
  };
}
export async function publicProjection(
  store: Store,
  services: Services,
): Promise<PublicWebsiteData> {
  const runs = store.runs();
  const latest = runs[0];
  const content = await services.website.publicContent();
  return {
    version: 1,
    runs: runs.map(publicRun),
    financial: financialProjection(
      latest ? await services.economy.summary(latest.id) : null,
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
export function privateProjection(store: Store, services: Services) {
  return {
    version: 1,
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
