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
];
