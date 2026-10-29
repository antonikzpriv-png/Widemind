CREATE TABLE creative_jobs (
 order_id TEXT PRIMARY KEY REFERENCES orders(id), run_id TEXT NOT NULL,
 scope_hash TEXT NOT NULL, limits_json TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0,
 attempt INTEGER NOT NULL DEFAULT 0, state TEXT NOT NULL DEFAULT 'allocating',
 feedback TEXT NOT NULL DEFAULT '', errors TEXT NOT NULL DEFAULT '[]',
 revision_deadline REAL, updated_at REAL NOT NULL
);
CREATE TABLE creative_stages (
 id TEXT PRIMARY KEY, run_id TEXT NOT NULL, order_id TEXT NOT NULL REFERENCES orders(id),
 revision INTEGER NOT NULL, attempt INTEGER NOT NULL, kind TEXT NOT NULL,
 action_id TEXT NOT NULL UNIQUE, tool TEXT NOT NULL, maximum_micro_usd INTEGER NOT NULL,
 reservation_id TEXT, state TEXT NOT NULL DEFAULT 'pending', request_json TEXT,
 result_json TEXT, created_at REAL NOT NULL, UNIQUE(order_id,revision,attempt,kind)
);
CREATE TABLE artifact_versions (
 order_id TEXT NOT NULL REFERENCES orders(id), version INTEGER NOT NULL, run_id TEXT NOT NULL,
 manifest TEXT NOT NULL, artifact TEXT NOT NULL, created_at REAL NOT NULL, published_at REAL,
 PRIMARY KEY(order_id,version)
);
CREATE TABLE customer_change_requests (
 id TEXT PRIMARY KEY, run_id TEXT NOT NULL, order_id TEXT NOT NULL REFERENCES orders(id),
 request_json TEXT NOT NULL, result_json TEXT NOT NULL, created_at REAL NOT NULL
);
