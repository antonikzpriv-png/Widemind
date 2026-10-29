import type { FinancialSummary, Json } from "../../contracts/index.ts";
import { BusinessService } from "./engine.ts";
import { record, text } from "./services.ts";
const decode = (value: unknown) =>
  typeof value === "string" ? JSON.parse(value) : value;
const camel = (key: string) =>
  key.replace(/_([a-z])/g, (_match, letter: string) => letter.toUpperCase());
const normalized = (row: Record<string, unknown>) =>
  Object.fromEntries(
    Object.entries(row).map(([key, value]) => [
      camel(key),
      ["scope", "assessment", "document", "payment", "artifact"].includes(
        key,
      ) && value !== null
        ? decode(value)
        : value,
    ]),
  );
export function orderProjection(
  business: BusinessService,
  runId: string,
  orderId: string,
  artifact = false,
) {
  business.run(runId);
  const row = business.one(
    "SELECT * FROM business_orders WHERE run_id=? AND id=?",
    runId,
    orderId,
  );
  if (!row) throw new Error("Unknown order");
  return projectOrder(row, artifact);
}
function projectOrder(row: Record<string, unknown>, artifact = false) {
  const result = normalized(row),
    payment = record(result.payment ?? {});
  const net =
    BigInt(String(payment.receiptsMicros ?? "0")) -
    BigInt(String(payment.refundsMicros ?? "0"));
  result.deliveryObligationOpen =
    !["delivered", "completed", "cancelled", "failed"].includes(
      String(row.status),
    ) &&
    (row.delivery_due_at !== null || net > 0n);
  result.paymentSource = "cached_order_snapshot";
  result.hasArtifact = Boolean(row.artifact);
  if (!artifact) delete result.artifact;
  return result;
}
export function consoleProjection(
  business: BusinessService,
  runId: string,
  query: Record<string, string>,
  summary: FinancialSummary | null,
) {
  const owned = !business.db.isTransaction;
  if (owned) business.db.exec("BEGIN");
  try {
    const result = consoleSnapshot(business, runId, query, summary);
    if (owned) business.db.exec("COMMIT");
    return result;
  } catch (error) {
    if (owned) business.db.exec("ROLLBACK");
    throw error;
  }
}
function consoleSnapshot(
  business: BusinessService,
  runId: string,
  query: Record<string, string>,
  summary: FinancialSummary | null,
) {
  const run = business.run(runId),
    collections = ["opportunities", "orders", "effects", "events"];
  if (query.collection && !collections.includes(query.collection))
    throw new Error("Invalid collection");
  if (query.offset && !/^[0-9]{1,10}$/.test(query.offset))
    throw new Error("Invalid offset");
  const limit = 500,
    requested = Number(query.offset ?? "0"),
    pagination: Record<string, unknown> = {};
  const rows: Record<string, Record<string, unknown>[]> = {};
  for (const collection of collections) {
    const offset = query.collection === collection ? requested : 0;
    const table = {
      opportunities: "business_opportunities",
      orders: "business_orders",
      effects: "runtime_actions",
      events: "runtime_inbox",
    }[collection]!;
    const total = Number(
      business.one(`SELECT COUNT(*) n FROM ${table} WHERE run_id=?`, runId)
        ?.n ?? 0,
    );
    const sort =
      collection === "opportunities"
        ? "CASE WHEN status IN ('completed','ordered','expired') THEN 1 ELSE 0 END,created_at DESC,id"
        : collection === "orders"
          ? "CASE WHEN status IN ('completed','cancelled','failed') THEN 1 ELSE 0 END,created_at DESC,id"
          : collection === "effects"
            ? "CASE WHEN state IN ('completed','failed') THEN 1 ELSE 0 END,created_at DESC,id"
            : "CASE WHEN consumed_at IS NULL THEN 0 ELSE 1 END,received_at DESC,id";
    const records = business.all(
      `SELECT * FROM ${table} WHERE run_id=? ORDER BY ${sort} LIMIT ? OFFSET ?`,
      runId,
      limit,
      offset,
    );
    pagination[collection] = {
      offset,
      limit,
      total,
      hasMore: offset + records.length < total,
      hasPrevious: offset > 0,
    };
    rows[collection] = records.map((row) =>
      collection === "orders"
        ? projectOrder(row)
        : collection === "effects"
          ? {
              id: row.id,
              jobId: decode(row.action_json).jobId ?? null,
              action: decode(row.action_json).tool,
              workKey: row.work_key,
              state: row.state,
              reason: row.reason,
              attempt: Number(row.attempts),
              createdAt: new Date(Number(row.created_at)).toISOString(),
            }
          : collection === "events"
            ? {
                id: row.id,
                kind: row.kind,
                state: row.consumed_at === null ? "queued" : "done",
                dueAt: new Date(Number(row.received_at)).toISOString(),
                error: null,
              }
            : normalized(row),
    );
  }
  const count = (sql: string) => Number(business.one(sql, runId)?.n ?? 0);
  const orderCounts = business.one(
    `SELECT COUNT(*) total,
    SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) completed,
    SUM(CASE WHEN status IN ('creating','readyForCommitment','ready','awaitingAcknowledgment','delivered','paymentHold') THEN 1 ELSE 0 END) fulfillment,
    SUM(CASE WHEN status IN ('accepted','awaitingPayment') THEN 1 ELSE 0 END) awaiting,
    SUM(CASE WHEN status NOT IN ('completed','delivered','cancelled','failed') AND (delivery_due_at IS NOT NULL OR
      LENGTH(COALESCE(json_extract(payment,'$.receiptsMicros'),'0'))>LENGTH(COALESCE(json_extract(payment,'$.refundsMicros'),'0')) OR
      (LENGTH(COALESCE(json_extract(payment,'$.receiptsMicros'),'0'))=LENGTH(COALESCE(json_extract(payment,'$.refundsMicros'),'0')) AND COALESCE(json_extract(payment,'$.receiptsMicros'),'0')>COALESCE(json_extract(payment,'$.refundsMicros'),'0'))) THEN 1 ELSE 0 END) obligations
    FROM business_orders WHERE run_id=?`,
    runId,
  )!;
  const customerIds = new Set(
    [...rows.opportunities!, ...rows.orders!]
      .map((row) => row.customerId)
      .filter((id): id is string => typeof id === "string"),
  );
  const customers = customerIds.size
    ? business
        .all(
          `SELECT * FROM business_customers WHERE run_id=? AND id IN (${[...customerIds].map(() => "?").join(",")}) ORDER BY created_at DESC`,
          runId,
          ...customerIds,
        )
        .map(normalized)
    : [];
  const opportunityIds = new Set(rows.opportunities!.map((row) => row.id));
  const quotes = opportunityIds.size
    ? business
        .all(
          `SELECT q.* FROM business_quotes q JOIN business_opportunities o ON o.id=q.opportunity_id WHERE o.run_id=? AND o.id IN (${[...opportunityIds].map(() => "?").join(",")}) ORDER BY q.expires_at DESC`,
          runId,
          ...[...opportunityIds].map(String),
        )
        .map(normalized)
    : [];
  return {
    schemaVersion: 1,
    runId,
    runtimeState: run.state,
    executionProfile: "foundation",
    usesFixtures: false,
    policy: business.policy(runId),
    summary,
    counts: {
      openCommitments: Number(orderCounts.obligations ?? 0),
      opportunities: count(
        "SELECT COUNT(*) n FROM business_opportunities WHERE run_id=?",
      ),
      orders: Number(orderCounts.total),
      completedOrders: Number(orderCounts.completed ?? 0),
      inFulfillment: Number(orderCounts.fulfillment ?? 0),
      awaitingPayment: Number(orderCounts.awaiting ?? 0),
      unresolvedEffects: count(
        "SELECT COUNT(*) n FROM runtime_actions WHERE run_id=? AND state IN ('running','reserved','unresolved')",
      ),
      blockedEvents: count(
        "SELECT COUNT(*) n FROM business_commands WHERE run_id=? AND outcome LIKE '%\"recorded\":false%'",
      ),
    },
    pagination,
    opportunities: rows.opportunities,
    orders: rows.orders,
    effects: rows.effects,
    events: rows.events,
    quotes,
    customers,
    memories: business
      .all(
        "SELECT * FROM business_memory WHERE run_id=? ORDER BY created_at DESC,rowid DESC LIMIT 100",
        runId,
      )
      .map(normalized),
    integrations: [
      {
        name: "Business",
        status: "configured",
        detail:
          "Receipt-driven local selection; provider qualification remains required",
      },
      {
        name: "Economy",
        status: summary ? "configured" : "unavailable",
        detail: summary
          ? "Authoritative protected economy"
          : "Current authoritative financial records unavailable",
      },
    ],
    ...(query.orderId
      ? {
          selectedOrder: orderProjection(
            business,
            runId,
            text(query.orderId, 200),
            true,
          ),
        }
      : {}),
  };
}
export function asJson(value: unknown): Json {
  return JSON.parse(JSON.stringify(value)) as Json;
}
