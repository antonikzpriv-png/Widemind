CREATE TABLE settings (run_id TEXT PRIMARY KEY, document TEXT NOT NULL);
CREATE TABLE customers (
 id TEXT PRIMARY KEY, run_id TEXT NOT NULL, email TEXT NOT NULL,
 opted_out INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL,
 UNIQUE(run_id,email)
);
CREATE TABLE opportunities (
 id TEXT PRIMARY KEY, run_id TEXT NOT NULL, customer_id TEXT REFERENCES customers(id),
 origin TEXT NOT NULL, source_reference TEXT NOT NULL, service TEXT NOT NULL,
 scope TEXT NOT NULL, priority INTEGER NOT NULL, status TEXT NOT NULL,
 assessment TEXT NOT NULL DEFAULT '{}', created_at REAL NOT NULL
);
CREATE TABLE quotes (
 id TEXT PRIMARY KEY, opportunity_id TEXT NOT NULL UNIQUE REFERENCES opportunities(id),
 price_micro_usd INTEGER NOT NULL CHECK(price_micro_usd > 0),
 cost_micro_usd INTEGER NOT NULL CHECK(cost_micro_usd >= 0),
 turnaround_seconds INTEGER NOT NULL, expires_at REAL NOT NULL,
 scope TEXT NOT NULL, status TEXT NOT NULL
);
CREATE TABLE orders (
 id TEXT PRIMARY KEY, run_id TEXT NOT NULL, quote_id TEXT NOT NULL UNIQUE REFERENCES quotes(id),
 customer_id TEXT NOT NULL REFERENCES customers(id), service TEXT NOT NULL,
 scope TEXT NOT NULL, status TEXT NOT NULL, price_micro_usd INTEGER NOT NULL,
 cost_micro_usd INTEGER NOT NULL, reservation_id TEXT,
 delivery_due_at REAL, payment TEXT NOT NULL DEFAULT '{}',
 artifact TEXT, delivery_reference TEXT, created_at REAL NOT NULL, completed_at REAL
);
CREATE TABLE events (
 id TEXT PRIMARY KEY, run_id TEXT NOT NULL, source TEXT NOT NULL, external_id TEXT NOT NULL,
 kind TEXT NOT NULL, payload TEXT NOT NULL, due_at REAL NOT NULL,
 state TEXT NOT NULL DEFAULT 'queued', lease_until REAL,
 attempts INTEGER NOT NULL DEFAULT 0, error TEXT,
 UNIQUE(run_id,source,external_id)
);
CREATE INDEX events_due ON events(state,due_at);
CREATE TABLE effects (
 id TEXT PRIMARY KEY, run_id TEXT NOT NULL, job_id TEXT NOT NULL,
 provider TEXT NOT NULL, action TEXT NOT NULL, request TEXT NOT NULL,
 state TEXT NOT NULL, result TEXT, reason TEXT, created_at REAL NOT NULL
);
CREATE TABLE memory (
 id TEXT PRIMARY KEY, run_id TEXT NOT NULL, customer_id TEXT,
 entity_id TEXT NOT NULL, kind TEXT NOT NULL, document TEXT NOT NULL,
 created_at REAL NOT NULL, UNIQUE(run_id,entity_id,kind)
);
CREATE TABLE public_activity (
 id TEXT PRIMARY KEY, run_id TEXT NOT NULL, kind TEXT NOT NULL,
 service TEXT NOT NULL, created_at REAL NOT NULL
);
CREATE TABLE experiments (
 run_id TEXT NOT NULL, opportunity_id TEXT NOT NULL UNIQUE,
 allowance_micro_usd INTEGER NOT NULL CHECK(allowance_micro_usd >= 0)
);
CREATE TABLE conversations (
 id TEXT PRIMARY KEY, run_id TEXT NOT NULL, customer_id TEXT,
 thread_id TEXT NOT NULL, message_id TEXT NOT NULL, direction TEXT NOT NULL,
 purpose TEXT NOT NULL, reference TEXT NOT NULL, created_at REAL NOT NULL,
 UNIQUE(run_id,message_id,direction)
);
