import { createHash } from "node:crypto";
import type { PublicAgentGraph } from "../contracts/index.ts";
import type { Store, ActionRow, RunRow } from "../runtime/store.ts";

/** Deliberately lossy public projection. Never forward arbitrary tool names or JSON. */
function rowObject(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}
function parse(value: string | null): Record<string, unknown> {
  try {
    return rowObject(JSON.parse(value ?? "null"));
  } catch {
    return {};
  }
}
function category(tool: unknown): PublicAgentGraph["nodes"][number]["kind"] {
  if (typeof tool !== "string") return "task";
  if (
    [
      "ai.generate",
      "ai.text",
      "ai.plan",
      "model.plan",
      "planning.reason",
    ].includes(tool)
  )
    return "thinking";
  if (["research.collect", "research.brief", "web.research"].includes(tool))
    return "research";
  if (["gmail.read", "gmail.send"].includes(tool)) return "communication";
  if (["commerce.fulfill", "publish.static"].includes(tool)) return "delivery";
  if (tool === "business.step") return "handoff";
  return "task";
}
function cost(row: ActionRow): PublicAgentGraph["nodes"][number]["cost"] {
  const receipt = parse(row.receipt_json);
  const usage = rowObject(receipt.usage);
  const charge = rowObject(usage.actualCharge);
  const valid =
    receipt.version === 1 &&
    receipt.actionId === row.id &&
    ["succeeded", "failed"].includes(String(receipt.status)) &&
    typeof usage.provisional === "boolean" &&
    charge.currency === "USD" &&
    typeof charge.micros === "string" &&
    /^(0|[1-9][0-9]{0,18})$/.test(charge.micros) &&
    BigInt(charge.micros) <= 9223372036854775807n;
  if (!valid)
    return {
      micros: null,
      status:
        row.receipt_json || ["completed", "failed"].includes(row.state)
          ? "unavailable"
          : "pending",
    };
  return {
    micros: charge.micros as string,
    status: usage.provisional
      ? "provisional"
      : ["completed", "failed"].includes(row.state)
        ? "settled"
        : "recorded",
  };
}
export function agentGraphProjection(
  store: Store,
  publicRunId?: string,
  now = Date.now(),
): PublicAgentGraph {
  const run = publicRunId
    ? store.run(publicRunId)
    : (store.db
        .prepare("SELECT * FROM runtime_runs ORDER BY created_at DESC LIMIT 1")
        .get() as unknown as RunRow | undefined);
  const rows = run
    ? (store.db
        .prepare(
          "SELECT * FROM runtime_actions WHERE run_id=? ORDER BY created_at DESC,id DESC LIMIT 48",
        )
        .all(run.id) as unknown as ActionRow[])
    : [];
  return {
    version: 1,
    runId: run?.id ?? null,
    state: run?.state ?? null,
    profile: run?.profile ?? null,
    workerConnected: !!(
      run &&
      store.db
        .prepare(
          "SELECT 1 FROM runtime_workers WHERE profile=? AND expires_at>? AND stopped_at IS NULL LIMIT 1",
        )
        .get(run.profile, now)
    ),
    observedAt: new Date(now).toISOString(),
    totalActions: run
      ? Number(
          store.db
            .prepare("SELECT COUNT(*) AS n FROM runtime_actions WHERE run_id=?")
            .get(run.id)?.n ?? 0,
        )
      : 0,
    nodes: rows.reverse().map((row) => ({
      id: createHash("sha256")
        .update(`${row.run_id}:${row.id}`)
        .digest("hex")
        .slice(0, 24),
      kind: category(parse(row.action_json).tool),
      state: row.state,
      at: new Date(row.created_at).toISOString(),
      cost: cost(row),
    })),
  };
}
