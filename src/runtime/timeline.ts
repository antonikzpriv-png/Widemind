/**
 * Plain-language activity: what the agent did, why, what it cost, and what
 * it will do next. Built from the runtime's own records; it shows tool names,
 * states and amounts, never customer content or tool output.
 */
import type { DatabaseSync } from "node:sqlite";
import { Store } from "./store.ts";

export interface TimelineEntry {
  at: string;
  kind: "work" | "money" | "owner" | "system";
  title: string;
  detail: string;
  costMicros: string | null;
  state: string | null;
}
export interface Timeline {
  now: {
    state: string;
    reason: string;
    nextWakeAt: string | null;
    pendingEvents: number;
    unresolved: number;
  };
  entries: TimelineEntry[];
  spentMicros: string;
}

const BUSINESS_STEPS: Record<string, string> = {
  select: "Reviewed a new request",
  offer: "Prepared and sent a quote",
  advance: "Moved an order forward",
  "payment.changed": "Checked a payment update",
  deadline: "Checked a delivery deadline",
  conversation: "Answered a customer message",
  "conversation.followup": "Followed up with a customer",
  "gmail.sync": "Checked the mailbox",
  "discovery.run": "Scanned sources for opportunities",
  "discovery.observe": "Reviewed discovery results",
  "experiment.step": "Worked on an experiment",
  "experiment.deadline": "Checked an experiment deadline",
  reconcile: "Reconciled an order",
};
const TOOLS: Record<string, string> = {
  "graphics.create": "Created a graphic",
  "software.create": "Built a software starter",
  "research.collect": "Researched sources",
  "gmail.send": "Sent an email",
  "gmail.read": "Read the mailbox",
  "demo.write-note": "Ran the demonstration task",
};
const EVENTS: Record<string, [TimelineEntry["kind"], string]> = {
  created: ["system", "Agent run created"],
  "agent.created": ["owner", "You created the agent"],
  "agent.updated": ["owner", "You changed the agent's settings"],
  "agent.plan.approved": ["owner", "You approved the first-hour plan"],
  "operator.resume": ["owner", "You started the agent"],
  "operator.pause": ["owner", "You paused the agent"],
  "operator.stop": ["owner", "You stopped the agent"],
  "operator.wake": ["owner", "You woke the agent"],
  "inbox.received": ["system", "A new event arrived"],
  "pilot.attestation.affirmed": ["owner", "You attested customer independence"],
  "pilot.attestation.withdrawn": [
    "owner",
    "You withdrew the independence attestation",
  ],
  "pilot.cost.recorded": ["money", "You recorded a running cost"],
  "pilot.cost.corrected": ["money", "You corrected a running cost"],
  "pilot.snapshot.published": ["owner", "You published a pilot snapshot"],
  "pilot.snapshot.taken": ["system", "A pilot snapshot was saved"],
  "pilot.snapshot.retracted": ["owner", "You retracted a pilot snapshot"],
  "pilot.rehearsal.passed": ["system", "Sandbox rehearsal passed"],
  "pilot.rehearsal.failed": ["system", "Sandbox rehearsal failed"],
  "growth.lead.contacted": ["owner", "You sent a reply to a lead"],
  "growth.sample.published": ["owner", "You published a sample"],
  "growth.sample.retracted": ["owner", "You took a sample down"],
};

function json(text: string | null): Record<string, unknown> {
  try {
    const value = text ? JSON.parse(text) : null;
    return value && typeof value === "object" && !Array.isArray(value)
      ? value
      : {};
  } catch {
    return {};
  }
}
const iso = (ms: number) => new Date(ms).toISOString();

/** Narrow an already-parsed value to a plain object without re-serializing it. */
function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

export function activityTimeline(
  db: DatabaseSync,
  runId: string,
  limit = 100,
): Timeline {
  const store = new Store(db);
  const run = store.run(runId);
  if (!run) throw new Error("Agent run not found");
  const entries: TimelineEntry[] = [];
  // Only the newest actions are shown; totals come from aggregate queries.
  const rows = db
    .prepare(
      "SELECT state,action_json,receipt_json,reason,attempts,updated_at FROM runtime_actions WHERE run_id=? ORDER BY updated_at DESC,id DESC LIMIT ?",
    )
    .all(runId, limit) as {
    state: string;
    action_json: string;
    receipt_json: string | null;
    reason: string | null;
    attempts: number;
    updated_at: number;
  }[];
  for (const row of rows) {
    const action = json(row.action_json);
    const receipt = json(row.receipt_json);
    const charge = record(record(receipt.usage).actualCharge);
    const micros =
      typeof charge.micros === "string" && /^\d{1,20}$/.test(charge.micros)
        ? charge.micros
        : null;
    const tool = typeof action.tool === "string" ? action.tool : "unknown";
    const result = record(receipt.result);
    let title =
      tool === "business.step"
        ? (BUSINESS_STEPS[String(result.eventKind)] ??
          "Handled a business step")
        : (TOOLS[tool] ?? `Used ${tool}`);
    const maximum = record(record(action.limit).maximum).micros;
    const why =
      row.state === "completed"
        ? receipt.status === "failed"
          ? "Finished with a failure the provider still charged for"
          : "Done"
        : row.state === "failed"
          ? `Failed: ${row.reason ?? "no reason recorded"}`
          : row.state === "unresolved"
            ? "Outcome unknown; it will be confirmed before any retry"
            : "In progress";
    if (row.state === "failed") title = `${title} (failed)`;
    entries.push({
      at: iso(Number(row.updated_at)),
      kind: "work",
      title,
      detail: `${why}${typeof maximum === "string" && maximum !== "0" ? ` · budget up to ${maximum} micro-USD` : ""}${Number(row.attempts) > 1 ? ` · ${row.attempts} attempts` : ""}`,
      costMicros: micros,
      state: row.state,
    });
  }
  const events = db
    .prepare(
      "SELECT kind,at,detail FROM runtime_events WHERE run_id=? AND kind<>'action.planned' ORDER BY id DESC LIMIT ?",
    )
    .all(runId, limit * 2) as { kind: string; at: number; detail: string }[];
  for (const event of events) {
    const known = EVENTS[event.kind];
    if (!known && !event.kind.startsWith("operator.")) continue;
    entries.push({
      at: iso(Number(event.at)),
      kind: known?.[0] ?? "owner",
      title: known?.[1] ?? event.kind,
      detail:
        event.kind.startsWith("pilot.snapshot") ||
        event.kind === "inbox.received"
          ? ""
          : event.detail.slice(0, 200),
      costMicros: null,
      state: null,
    });
  }
  entries.sort((a, b) => (a.at < b.at ? 1 : a.at > b.at ? -1 : 0));
  const totals = db
    .prepare(
      "SELECT COALESCE(SUM(CASE WHEN json_valid(receipt_json) THEN CAST(json_extract(receipt_json,'$.usage.actualCharge.micros') AS INTEGER) END),0) AS spent, COALESCE(SUM(state='unresolved'),0) AS unresolved FROM runtime_actions WHERE run_id=?",
    )
    .get(runId) as { spent: number | bigint; unresolved: number };
  return {
    now: {
      state: run.state,
      reason: run.reason,
      nextWakeAt:
        run.next_wake_at === null ? null : iso(Number(run.next_wake_at)),
      pendingEvents: Number(
        (
          db
            .prepare(
              "SELECT COUNT(*) AS n FROM runtime_inbox WHERE run_id=? AND consumed_at IS NULL",
            )
            .get(runId) as { n: number }
        ).n,
      ),
      unresolved: Number(totals.unresolved),
    },
    entries: entries.slice(0, limit),
    spentMicros: String(totals.spent),
  };
}
