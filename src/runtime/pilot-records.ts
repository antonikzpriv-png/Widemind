/**
 * Operator-recorded pilot facts that accounting cannot establish: customer
 * independence and costs paid outside the ledger. Both tables are append-only
 * (enforced by database triggers). Neither changes a balance or the ledger.
 */
import type { DatabaseSync } from "node:sqlite";
import { transaction } from "../db/database.ts";
import { Store } from "./store.ts";

export const COST_CATEGORIES = [
  "hosting",
  "domain",
  "subscription",
  "tooling",
  "other",
] as const;
export type CostCategory = (typeof COST_CATEGORIES)[number];

function text(value: unknown, name: string, min: number, max: number): string {
  if (
    typeof value !== "string" ||
    value.trim().length < min ||
    value.length > max ||
    /[\x00-\x1f\x7f]/.test(value)
  )
    throw new Error(
      `${name} must be ${min ? "non-empty " : ""}text of at most ${max} characters without control characters`,
    );
  return value.trim();
}
function date(value: unknown, name: string): string {
  if (
    typeof value !== "string" ||
    !/^\d{4}-\d\d-\d\d$/.test(value) ||
    new Date(value + "T00:00:00Z").toISOString().slice(0, 10) !== value
  )
    throw new Error(`${name} must be a calendar date (YYYY-MM-DD)`);
  return value;
}
function existingRun(db: DatabaseSync, runId: string) {
  if (!new Store(db).run(runId)) throw new Error("Pilot run not found");
}

/** Parse a USD amount such as "12.34" into exact micro-USD without floats. */
export function usdToMicros(value: string): string {
  const match = /^(0|[1-9][0-9]{0,11})(?:\.([0-9]{1,6}))?$/.exec(value);
  if (!match) throw new Error("Amount must be USD such as 12.34");
  const micros =
    BigInt(match[1]!) * 1_000_000n + BigInt((match[2] ?? "").padEnd(6, "0"));
  if (micros <= 0n) throw new Error("Amount must be greater than zero");
  return String(micros);
}

export interface Attestation {
  id: number;
  stance: "affirmed" | "withdrawn";
  evidenceRef: string;
  note: string;
  recordedAt: string;
}
export function recordAttestation(
  db: DatabaseSync,
  input: {
    runId: string;
    stance: "affirmed" | "withdrawn";
    evidenceRef: string;
    note?: string;
    now?: number;
  },
): Attestation {
  if (input.stance !== "affirmed" && input.stance !== "withdrawn")
    throw new Error("Stance must be affirmed or withdrawn");
  const evidenceRef = text(input.evidenceRef, "Evidence reference", 1, 500);
  const note = text(input.note ?? "", "Note", 0, 1000);
  return transaction(db, () => {
    existingRun(db, input.runId);
    const now = input.now ?? Date.now();
    const result = db
      .prepare(
        "INSERT INTO pilot_attestations(run_id,subject,stance,evidence_ref,note,recorded_at) VALUES (?,'independent_customer',?,?,?,?)",
      )
      .run(input.runId, input.stance, evidenceRef, note, now);
    new Store(db).event(
      input.runId,
      `pilot.attestation.${input.stance}`,
      "Operator recorded customer independence attestation",
      now,
    );
    return {
      id: Number(result.lastInsertRowid),
      stance: input.stance,
      evidenceRef,
      note,
      recordedAt: new Date(now).toISOString(),
    };
  });
}
/** The latest attestation decides; a withdrawal supersedes an earlier affirmation. */
export function latestAttestation(
  db: DatabaseSync,
  runId: string,
): Attestation | null {
  const row = db
    .prepare(
      "SELECT id,stance,evidence_ref,note,recorded_at FROM pilot_attestations WHERE run_id=? AND subject='independent_customer' ORDER BY id DESC LIMIT 1",
    )
    .get(runId) as
    | {
        id: number;
        stance: "affirmed" | "withdrawn";
        evidence_ref: string;
        note: string;
        recorded_at: number;
      }
    | undefined;
  return row
    ? {
        id: Number(row.id),
        stance: row.stance,
        evidenceRef: row.evidence_ref,
        note: row.note,
        recordedAt: new Date(Number(row.recorded_at)).toISOString(),
      }
    : null;
}

export interface CostEntry {
  id: number;
  category: CostCategory;
  amountMicros: string;
  periodStart: string;
  periodEnd: string;
  evidenceRef: string;
  note: string;
  correctsId: number | null;
  recordedAt: string;
}
interface CostRow {
  id: number;
  category: CostCategory;
  amount_micros: string;
  period_start: string;
  period_end: string;
  evidence_ref: string;
  note: string;
  corrects_id: number | null;
  recorded_at: number;
}
const costEntry = (row: CostRow): CostEntry => ({
  id: Number(row.id),
  category: row.category,
  amountMicros: row.amount_micros,
  periodStart: row.period_start,
  periodEnd: row.period_end,
  evidenceRef: row.evidence_ref,
  note: row.note,
  correctsId: row.corrects_id === null ? null : Number(row.corrects_id),
  recordedAt: new Date(Number(row.recorded_at)).toISOString(),
});

/**
 * Record a cost paid outside the ledger, or a correction that reduces an
 * earlier entry. Corrections are new negative entries; history is never edited.
 */
export function recordCost(
  db: DatabaseSync,
  input: {
    runId: string;
    amountMicros: string;
    evidenceRef: string;
    note?: string;
    now?: number;
  } & (
    | {
        category: CostCategory;
        periodStart: string;
        periodEnd: string;
        correctsId?: undefined;
      }
    | { correctsId: number }
  ),
): CostEntry {
  if (!/^[1-9][0-9]{0,19}$/.test(input.amountMicros))
    throw new Error("Amount must be a positive integer of micro-USD");
  const evidenceRef = text(input.evidenceRef, "Evidence reference", 1, 500);
  const note = text(input.note ?? "", "Note", 0, 1000);
  return transaction(db, () => {
    existingRun(db, input.runId);
    const now = input.now ?? Date.now();
    let category: CostCategory, start: string, end: string, amount: string;
    let correctsId: number | null = null;
    if (input.correctsId !== undefined) {
      const original = db
        .prepare(
          "SELECT * FROM pilot_costs WHERE id=? AND run_id=? AND corrects_id IS NULL",
        )
        .get(input.correctsId, input.runId) as CostRow | undefined;
      if (!original) throw new Error("Corrected cost entry not found");
      const corrected = db
        .prepare("SELECT amount_micros FROM pilot_costs WHERE corrects_id=?")
        .all(original.id) as { amount_micros: string }[];
      const remaining =
        BigInt(original.amount_micros) +
        corrected.reduce((sum, row) => sum + BigInt(row.amount_micros), 0n);
      if (BigInt(input.amountMicros) > remaining)
        throw new Error(
          `Correction exceeds the entry's remaining ${remaining} micro-USD`,
        );
      ({ category, period_start: start, period_end: end } = original);
      amount = "-" + input.amountMicros;
      correctsId = original.id;
    } else {
      if (!COST_CATEGORIES.includes(input.category))
        throw new Error(
          `Category must be one of ${COST_CATEGORIES.join(", ")}`,
        );
      category = input.category;
      start = date(input.periodStart, "Period start");
      end = date(input.periodEnd, "Period end");
      if (end < start) throw new Error("Period end precedes period start");
      amount = input.amountMicros;
    }
    const result = db
      .prepare(
        "INSERT INTO pilot_costs(run_id,category,amount_micros,period_start,period_end,evidence_ref,note,corrects_id,recorded_at) VALUES (?,?,?,?,?,?,?,?,?)",
      )
      .run(
        input.runId,
        category,
        amount,
        start,
        end,
        evidenceRef,
        note,
        correctsId,
        now,
      );
    new Store(db).event(
      input.runId,
      correctsId === null ? "pilot.cost.recorded" : "pilot.cost.corrected",
      "Operator recorded a cost outside the ledger",
      now,
    );
    return costEntry(
      db
        .prepare("SELECT * FROM pilot_costs WHERE id=?")
        .get(Number(result.lastInsertRowid)) as unknown as CostRow,
    );
  });
}

export interface OutsideLedgerCosts {
  entries: number;
  totalMicros: string;
  byCategory: Partial<Record<CostCategory, string>>;
  records?: CostEntry[];
}
export function outsideLedgerCosts(
  db: DatabaseSync,
  runId: string,
  includeRecords: boolean,
): OutsideLedgerCosts {
  const rows = db
    .prepare("SELECT * FROM pilot_costs WHERE run_id=? ORDER BY id")
    .all(runId) as unknown as CostRow[];
  const totals = new Map<CostCategory, bigint>();
  let total = 0n;
  for (const row of rows) {
    const amount = BigInt(row.amount_micros);
    total += amount;
    totals.set(row.category, (totals.get(row.category) ?? 0n) + amount);
  }
  return {
    entries: rows.length,
    totalMicros: String(total),
    byCategory: Object.fromEntries(
      [...totals].map(([key, value]) => [key, String(value)]),
    ),
    ...(includeRecords ? { records: rows.map(costEntry) } : {}),
  };
}
