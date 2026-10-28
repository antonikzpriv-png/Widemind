import { randomUUID } from "node:crypto";
import type { DatabaseSync } from "node:sqlite";
import type {
  ActionState,
  ActionView,
  PublicRun,
  RunState,
  ToolAction,
} from "../contracts/index.ts";
import { transaction } from "../db/database.ts";
export interface RunRow {
  id: string;
  profile: "foundation" | "demo";
  state: RunState;
  desired: "run" | "pause" | "stop";
  reason: string;
  created_at: number;
  updated_at: number;
  next_wake_at: number | null;
  last_error: string | null;
  revision: number;
}
export interface ActionRow {
  id: string;
  run_id: string;
  work_key: string;
  state: ActionState;
  action_json: string;
  reservation_json: string | null;
  receipt_json: string | null;
  reason: string | null;
  attempts: number;
  created_at: number;
  updated_at: number;
}
export interface Lease {
  runId: string;
  owner: string;
  fence: number;
  until: number;
}
export class Store {
  readonly db: DatabaseSync;
  constructor(db: DatabaseSync) {
    this.db = db;
  }
  run(id: string) {
    return this.db
      .prepare("SELECT * FROM runtime_runs WHERE id=?")
      .get(id) as unknown as RunRow | undefined;
  }
  runs() {
    return this.db
      .prepare("SELECT * FROM runtime_runs ORDER BY created_at DESC")
      .all() as unknown as RunRow[];
  }
  actions(runId: string) {
    return this.db
      .prepare(
        "SELECT * FROM runtime_actions WHERE run_id=? ORDER BY created_at,id",
      )
      .all(runId) as unknown as ActionRow[];
  }
  views(runId: string): ActionView[] {
    return this.actions(runId).map((row) => ({
      action: JSON.parse(row.action_json),
      state: row.state,
      receipt: row.receipt_json ? JSON.parse(row.receipt_json) : null,
    }));
  }
  create(profile: "foundation" | "demo", now = Date.now()) {
    const id = randomUUID();
    transaction(this.db, () => {
      this.db
        .prepare(
          "INSERT INTO runtime_runs(id,profile,state,desired,reason,created_at,updated_at) VALUES (?,?,'paused','pause','Awaiting operator start; funding unverified',?,?)",
        )
        .run(id, profile, now, now);
      this.event(id, "created", "Run created without a funded balance", now);
    });
    return id;
  }
  event(runId: string, kind: string, detail: string, now = Date.now()) {
    this.db
      .prepare(
        "INSERT INTO runtime_events(run_id,kind,at,detail) VALUES (?,?,?,?)",
      )
      .run(runId, kind, now, detail.slice(0, 2000));
  }
  control(
    id: string,
    control: "resume" | "pause" | "stop" | "wake",
    now = Date.now(),
  ) {
    transaction(this.db, () => {
      const run = this.run(id);
      if (!run) throw new Error("Unknown run");
      if (control === "wake" && run.desired !== "run")
        throw new Error("Resume a paused or stopped run before waking it");
      const desired =
        control === "pause" ? "pause" : control === "stop" ? "stop" : "run";
      const state =
        desired === "pause"
          ? "paused"
          : desired === "stop"
            ? "stopped"
            : "active";
      this.db
        .prepare(
          "UPDATE runtime_runs SET desired=?,state=?,reason=?,updated_at=?,revision=revision+1,next_wake_at=? WHERE id=?",
        )
        .run(desired, state, `Operator ${control}`, now, now, id);
      this.db
        .prepare(
          "INSERT INTO runtime_jobs(run_id,available_at) VALUES (?,?) ON CONFLICT(run_id) DO UPDATE SET available_at=excluded.available_at",
        )
        .run(id, now);
      this.event(
        id,
        `operator.${control}`,
        "Authenticated operator intervention",
        now,
      );
    });
  }
  acquire(owner: string, now: number, leaseMs: number): Lease | null {
    return transaction(this.db, () => {
      const row = this.db
        .prepare(
          "SELECT run_id,fence FROM runtime_jobs WHERE available_at<=? AND (lease_until IS NULL OR lease_until<=?) ORDER BY available_at,run_id LIMIT 1",
        )
        .get(now, now);
      if (!row) return null;
      const fence = Number(row.fence) + 1;
      this.db
        .prepare(
          "UPDATE runtime_jobs SET lease_owner=?,lease_until=?,fence=? WHERE run_id=?",
        )
        .run(owner, now + leaseMs, fence, String(row.run_id));
      return { runId: String(row.run_id), owner, fence, until: now + leaseMs };
    });
  }
  owned(lease: Lease, now = Date.now()) {
    return !!this.db
      .prepare(
        "SELECT 1 FROM runtime_jobs WHERE run_id=? AND lease_owner=? AND fence=? AND lease_until>?",
      )
      .get(lease.runId, lease.owner, lease.fence, now);
  }
  renew(lease: Lease, now: number, leaseMs: number) {
    const changed = this.db
      .prepare(
        "UPDATE runtime_jobs SET lease_until=? WHERE run_id=? AND lease_owner=? AND fence=? AND lease_until>?",
      )
      .run(now + leaseMs, lease.runId, lease.owner, lease.fence, now).changes;
    return Number(changed) === 1;
  }
  fenced<T>(lease: Lease, fn: () => T): T {
    return transaction(this.db, () => {
      if (!this.owned(lease)) throw new Error("Lease lost");
      return fn();
    });
  }
  finish(
    lease: Lease,
    state: RunState,
    reason: string,
    wakeAt: number | null,
    revision: number,
  ) {
    this.fenced(lease, () => {
      // A concurrent operator control wins over this tick's stale state.
      const run = this.run(lease.runId)!;
      if (run.revision !== revision) wakeAt = Date.now();
      else
        this.db
          .prepare(
            "UPDATE runtime_runs SET state=?,reason=?,updated_at=?,next_wake_at=? WHERE id=?",
          )
          .run(state, reason.slice(0, 2000), Date.now(), wakeAt, lease.runId);
      if (wakeAt === null)
        this.db
          .prepare("DELETE FROM runtime_jobs WHERE run_id=?")
          .run(lease.runId);
      else
        this.db
          .prepare(
            "UPDATE runtime_jobs SET available_at=?,lease_owner=NULL,lease_until=NULL WHERE run_id=?",
          )
          .run(wakeAt, lease.runId);
    });
  }
  plan(lease: Lease, action: ToolAction) {
    return this.fenced(lease, () => {
      const existing = this.db
        .prepare("SELECT * FROM runtime_actions WHERE run_id=? AND work_key=?")
        .get(action.runId, action.workKey) as unknown as ActionRow | undefined;
      if (existing) return existing;
      const now = Date.now();
      this.db
        .prepare(
          "INSERT INTO runtime_actions(id,run_id,work_key,state,action_json,created_at,updated_at) VALUES (?,?,?,'planned',?,?,?)",
        )
        .run(
          action.actionId,
          action.runId,
          action.workKey,
          JSON.stringify(action),
          now,
          now,
        );
      this.db
        .prepare("INSERT INTO runtime_outbox VALUES (?,'pending',?,?,?)")
        .run(action.actionId, action.actionId, now, now);
      this.event(action.runId, "action.planned", action.actionId);
      return this.actions(action.runId).find(
        (row) => row.id === action.actionId,
      )!;
    });
  }
}
export function publicRun(run: RunRow): PublicRun {
  return {
    runId: run.id,
    profile: run.profile,
    state: run.state,
    createdAt: new Date(run.created_at).toISOString(),
    updatedAt: new Date(run.updated_at).toISOString(),
    nextWakeAt:
      run.next_wake_at === null
        ? null
        : new Date(run.next_wake_at).toISOString(),
  };
}
