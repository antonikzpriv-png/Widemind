/**
 * Pilot snapshots: the public view of the run report, saved over time.
 * Each snapshot is hash-chained to the previous one for its run, so a
 * changed or removed record is detectable. Snapshots are append-only; the
 * only permitted change is retracting a publication. Only snapshots the
 * operator explicitly published are shown on the website.
 */
import { createHash } from "node:crypto";
import type { DatabaseSync } from "node:sqlite";
import type { Services } from "../modules/registry.ts";
import { transaction } from "../db/database.ts";
import { pilotReport, type PilotReport } from "./pilot.ts";
import { Store } from "./store.ts";

interface SnapshotRow {
  id: number;
  run_id: string;
  taken_at: number;
  report_json: string;
  prev_hash: string | null;
  hash: string;
  published: number;
  retracted_at: number | null;
}
export interface SnapshotInfo {
  id: number;
  takenAt: string;
  hash: string;
  published: boolean;
  retractedAt: string | null;
}
const info = (row: SnapshotRow): SnapshotInfo => ({
  id: Number(row.id),
  takenAt: new Date(Number(row.taken_at)).toISOString(),
  hash: row.hash,
  published: Number(row.published) === 1,
  retractedAt:
    row.retracted_at === null
      ? null
      : new Date(Number(row.retracted_at)).toISOString(),
});

export function snapshotHash(
  prevHash: string | null,
  runId: string,
  takenAt: number,
  reportJson: string,
): string {
  return createHash("sha256")
    .update(`${prevHash ?? ""}\n${runId}\n${takenAt}\n${reportJson}`)
    .digest("hex");
}

export async function takeSnapshot(input: {
  db: DatabaseSync;
  services: Services;
  runId: string;
  publish: boolean;
  now?: number;
}): Promise<SnapshotInfo> {
  const now = input.now ?? Date.now();
  // The report reads the economy before the write transaction starts.
  const report = await pilotReport({
    db: input.db,
    services: input.services,
    runId: input.runId,
    view: "public",
    now,
  });
  const reportJson = JSON.stringify(report);
  return transaction(input.db, () => {
    const previous = input.db
      .prepare(
        "SELECT hash FROM pilot_snapshots WHERE run_id=? ORDER BY id DESC LIMIT 1",
      )
      .get(input.runId) as { hash: string } | undefined;
    const prevHash = previous?.hash ?? null;
    const hash = snapshotHash(prevHash, input.runId, now, reportJson);
    const result = input.db
      .prepare(
        "INSERT INTO pilot_snapshots(run_id,taken_at,report_json,prev_hash,hash,published) VALUES (?,?,?,?,?,?)",
      )
      .run(input.runId, now, reportJson, prevHash, hash, input.publish ? 1 : 0);
    new Store(input.db).event(
      input.runId,
      input.publish ? "pilot.snapshot.published" : "pilot.snapshot.taken",
      hash,
      now,
    );
    return info(
      input.db
        .prepare("SELECT * FROM pilot_snapshots WHERE id=?")
        .get(Number(result.lastInsertRowid)) as unknown as SnapshotRow,
    );
  });
}

export function retractSnapshot(
  db: DatabaseSync,
  runId: string,
  id: number,
  now = Date.now(),
): SnapshotInfo {
  return transaction(db, () => {
    const row = db
      .prepare("SELECT * FROM pilot_snapshots WHERE id=? AND run_id=?")
      .get(id, runId) as SnapshotRow | undefined;
    if (!row) throw new Error("Snapshot not found");
    if (Number(row.published) !== 1 || row.retracted_at !== null)
      throw new Error("Snapshot is not currently published");
    db.prepare(
      "UPDATE pilot_snapshots SET published=0,retracted_at=? WHERE id=?",
    ).run(now, id);
    new Store(db).event(runId, "pilot.snapshot.retracted", row.hash, now);
    return info(
      db
        .prepare("SELECT * FROM pilot_snapshots WHERE id=?")
        .get(id) as unknown as SnapshotRow,
    );
  });
}

export interface ChainVerification {
  ok: boolean;
  snapshots: number;
  /** First snapshot id whose link or content does not verify. */
  brokenAt: number | null;
}
export function verifyChain(
  db: DatabaseSync,
  runId: string,
): ChainVerification {
  const rows = db
    .prepare("SELECT * FROM pilot_snapshots WHERE run_id=? ORDER BY id")
    .all(runId) as unknown as SnapshotRow[];
  let prev: string | null = null;
  for (const row of rows) {
    if (
      row.prev_hash !== prev ||
      snapshotHash(prev, runId, Number(row.taken_at), row.report_json) !==
        row.hash
    )
      return { ok: false, snapshots: rows.length, brokenAt: Number(row.id) };
    prev = row.hash;
  }
  return { ok: true, snapshots: rows.length, brokenAt: null };
}

export function listSnapshots(db: DatabaseSync, runId: string, limit = 50) {
  return (
    db
      .prepare(
        "SELECT * FROM pilot_snapshots WHERE run_id=? ORDER BY id DESC LIMIT ?",
      )
      .all(runId, limit) as unknown as SnapshotRow[]
  ).map(info);
}

export interface TimelinePoint {
  takenAt: string;
  hash: string;
  verdict: PilotReport["verdict"];
  spendableMicros: string | null;
  grossCustomerReceiptsMicros: string | null;
  netOperatingResultMicros: string | null;
  resultAfterOutsideCostsMicros: string | null;
  interventions: number;
}
export interface PublishedPilot {
  latest:
    | (PilotReport & { snapshot: { takenAt: string; hash: string } })
    | null;
  timeline: TimelinePoint[];
  chain: { ok: boolean; publishedSnapshots: number };
}

/** The website's view: only published, unretracted snapshots. */
export function publishedPilot(
  db: DatabaseSync,
  runId: string,
  limit = 100,
): PublishedPilot {
  const rows = (
    db
      .prepare(
        "SELECT * FROM pilot_snapshots WHERE run_id=? AND published=1 AND retracted_at IS NULL ORDER BY id DESC LIMIT ?",
      )
      .all(runId, limit) as unknown as SnapshotRow[]
  ).reverse();
  const parsed = rows.flatMap((row) => {
    try {
      return [{ row, report: JSON.parse(row.report_json) as PilotReport }];
    } catch {
      return [];
    }
  });
  const timeline = parsed.map(({ row, report }) => ({
    takenAt: new Date(Number(row.taken_at)).toISOString(),
    hash: row.hash,
    verdict: report.verdict,
    spendableMicros: report.outcomes?.spendableMicros ?? null,
    grossCustomerReceiptsMicros:
      report.outcomes?.grossCustomerReceiptsMicros ?? null,
    netOperatingResultMicros: report.outcomes?.netOperatingResultMicros ?? null,
    resultAfterOutsideCostsMicros: report.resultAfterOutsideCostsMicros,
    interventions: report.interventions.total,
  }));
  const last = parsed.at(-1);
  return {
    latest: last
      ? {
          ...last.report,
          snapshot: {
            takenAt: new Date(Number(last.row.taken_at)).toISOString(),
            hash: last.row.hash,
          },
        }
      : null,
    timeline,
    chain: {
      ok: verifyChain(db, runId).ok,
      publishedSnapshots: parsed.length,
    },
  };
}
