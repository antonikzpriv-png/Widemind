import type { Migration } from "../../contracts/index.ts";
export const coreMigrations: readonly Migration[] = [
  {
    version: 1,
    sql: `
CREATE TABLE runtime_runs (
 id TEXT PRIMARY KEY, profile TEXT NOT NULL CHECK(profile IN ('foundation','demo')),
 state TEXT NOT NULL CHECK(state IN ('active','waiting','paused','exhausted','stopped','failed')),
 desired TEXT NOT NULL CHECK(desired IN ('run','pause','stop')),
 reason TEXT NOT NULL, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
 next_wake_at INTEGER, last_error TEXT, revision INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE runtime_jobs (
 run_id TEXT PRIMARY KEY REFERENCES runtime_runs(id), available_at INTEGER NOT NULL,
 lease_owner TEXT, lease_until INTEGER, fence INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE runtime_actions (
 id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runtime_runs(id), work_key TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('planned','reserved','running','completed','failed','unresolved')),
 action_json TEXT NOT NULL, reservation_json TEXT, receipt_json TEXT, reason TEXT,
 attempts INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
 UNIQUE(run_id,work_key)
);
CREATE TABLE runtime_outbox (
 action_id TEXT PRIMARY KEY REFERENCES runtime_actions(id),
 state TEXT NOT NULL CHECK(state IN ('pending','inflight','unresolved','delivered')),
 idempotency_key TEXT NOT NULL UNIQUE, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
);
CREATE TABLE runtime_events (
 id INTEGER PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runtime_runs(id),
 kind TEXT NOT NULL, at INTEGER NOT NULL, detail TEXT NOT NULL
);
CREATE TABLE auth_sessions (
 token_hash TEXT PRIMARY KEY, csrf TEXT NOT NULL, expires_at INTEGER NOT NULL, created_at INTEGER NOT NULL
);
CREATE TABLE auth_attempts (
 key TEXT PRIMARY KEY, count INTEGER NOT NULL, window_at INTEGER NOT NULL
);
CREATE INDEX runtime_due ON runtime_jobs(available_at,lease_until);
CREATE INDEX runtime_actions_run ON runtime_actions(run_id,created_at);
CREATE INDEX runtime_events_run ON runtime_events(run_id,id);
`,
  },
  {
    version: 2,
    sql: `
ALTER TABLE runtime_jobs ADD COLUMN lease_token TEXT;
CREATE TABLE runtime_workers (
 id TEXT PRIMARY KEY, profile TEXT NOT NULL, started_at INTEGER NOT NULL,
 heartbeat_at INTEGER NOT NULL, expires_at INTEGER NOT NULL, stopped_at INTEGER
);
CREATE TABLE runtime_inbox (
 id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runtime_runs(id),
 source TEXT NOT NULL, account_ref TEXT NOT NULL, external_event_id TEXT NOT NULL,
 kind TEXT NOT NULL, payload_json TEXT NOT NULL, digest TEXT NOT NULL,
 received_at INTEGER NOT NULL, consumed_at INTEGER,
 UNIQUE(source,account_ref,external_event_id)
);
CREATE INDEX runtime_inbox_pending ON runtime_inbox(run_id,consumed_at,received_at);
`,
  },
  {
    version: 3,
    sql: `
CREATE TABLE pilot_attestations (
 id INTEGER PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runtime_runs(id),
 subject TEXT NOT NULL CHECK(subject IN ('independent_customer')),
 stance TEXT NOT NULL CHECK(stance IN ('affirmed','withdrawn')),
 evidence_ref TEXT NOT NULL, note TEXT NOT NULL, recorded_at INTEGER NOT NULL
);
CREATE TABLE pilot_costs (
 id INTEGER PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runtime_runs(id),
 category TEXT NOT NULL CHECK(category IN ('hosting','domain','subscription','tooling','other')),
 amount_micros TEXT NOT NULL, period_start TEXT NOT NULL, period_end TEXT NOT NULL,
 evidence_ref TEXT NOT NULL, note TEXT NOT NULL,
 corrects_id INTEGER REFERENCES pilot_costs(id), recorded_at INTEGER NOT NULL,
 CHECK((corrects_id IS NULL) = (substr(amount_micros,1,1) <> '-'))
);
CREATE TRIGGER pilot_attestations_append_only_update BEFORE UPDATE ON pilot_attestations BEGIN SELECT RAISE(ABORT,'pilot attestations are append-only'); END;
CREATE TRIGGER pilot_attestations_append_only_delete BEFORE DELETE ON pilot_attestations BEGIN SELECT RAISE(ABORT,'pilot attestations are append-only'); END;
CREATE TRIGGER pilot_costs_append_only_update BEFORE UPDATE ON pilot_costs BEGIN SELECT RAISE(ABORT,'pilot costs are append-only'); END;
CREATE TRIGGER pilot_costs_append_only_delete BEFORE DELETE ON pilot_costs BEGIN SELECT RAISE(ABORT,'pilot costs are append-only'); END;
CREATE INDEX pilot_attestations_run ON pilot_attestations(run_id,subject,id);
CREATE INDEX pilot_costs_run ON pilot_costs(run_id,id);
CREATE TABLE pilot_alerts (
 run_id TEXT NOT NULL REFERENCES runtime_runs(id), condition TEXT NOT NULL,
 active INTEGER NOT NULL, since INTEGER NOT NULL,
 notified_active INTEGER, notified_at INTEGER,
 PRIMARY KEY(run_id,condition)
);
`,
  },
  {
    version: 4,
    sql: `
CREATE TABLE pilot_snapshots (
 id INTEGER PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runtime_runs(id),
 taken_at INTEGER NOT NULL, report_json TEXT NOT NULL,
 prev_hash TEXT, hash TEXT NOT NULL UNIQUE,
 published INTEGER NOT NULL CHECK(published IN (0,1)), retracted_at INTEGER
);
CREATE INDEX pilot_snapshots_run ON pilot_snapshots(run_id,id);
CREATE TRIGGER pilot_snapshots_append_only_delete BEFORE DELETE ON pilot_snapshots BEGIN SELECT RAISE(ABORT,'pilot snapshots are append-only'); END;
CREATE TRIGGER pilot_snapshots_retract_only BEFORE UPDATE ON pilot_snapshots
 WHEN NOT (OLD.published=1 AND NEW.published=0 AND OLD.retracted_at IS NULL AND NEW.retracted_at IS NOT NULL
  AND NEW.id=OLD.id AND NEW.run_id=OLD.run_id AND NEW.taken_at=OLD.taken_at AND NEW.report_json=OLD.report_json
  AND NEW.prev_hash IS OLD.prev_hash AND NEW.hash=OLD.hash)
 BEGIN SELECT RAISE(ABORT,'pilot snapshots are append-only; only retracting a publication is allowed'); END;
`,
  },
  {
    version: 5,
    sql: `
CREATE TABLE agent_profiles (
 id INTEGER PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runtime_runs(id),
 document TEXT NOT NULL, created_at INTEGER NOT NULL
);
CREATE INDEX agent_profiles_run ON agent_profiles(run_id,id);
CREATE TRIGGER agent_profiles_append_only BEFORE UPDATE ON agent_profiles BEGIN SELECT RAISE(ABORT,'agent profile versions are append-only'); END;
CREATE TABLE agent_plans (
 id INTEGER PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runtime_runs(id),
 proposal TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('approved','dismissed')),
 result TEXT NOT NULL, decided_at INTEGER NOT NULL
);
CREATE INDEX agent_plans_run ON agent_plans(run_id,id);
`,
  },
];
