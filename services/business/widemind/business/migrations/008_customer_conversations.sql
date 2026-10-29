CREATE TABLE customer_threads (
 id TEXT PRIMARY KEY, run_id TEXT NOT NULL, customer_id TEXT NOT NULL REFERENCES customers(id),
 source TEXT NOT NULL, thread_id TEXT NOT NULL, opportunity_id TEXT NOT NULL REFERENCES opportunities(id),
 status TEXT NOT NULL DEFAULT 'collecting', context TEXT NOT NULL DEFAULT '{}', revision INTEGER NOT NULL DEFAULT 0,
 current_quote_id TEXT, intervention TEXT, operator_hold INTEGER NOT NULL DEFAULT 0, inbound_count INTEGER NOT NULL DEFAULT 0,
 followups_sent INTEGER NOT NULL DEFAULT 0, last_message_at REAL NOT NULL, last_reply_at REAL,
 UNIQUE(run_id,source,thread_id,customer_id), UNIQUE(run_id,opportunity_id)
);
CREATE TABLE conversation_messages (
 id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES customer_threads(id), run_id TEXT NOT NULL,
 source TEXT NOT NULL, external_id TEXT NOT NULL, direction TEXT NOT NULL,
 body TEXT NOT NULL, status TEXT NOT NULL, interpretation TEXT, metadata TEXT NOT NULL DEFAULT '{}', created_at REAL NOT NULL,
 UNIQUE(run_id,source,external_id,direction)
);
CREATE TABLE conversation_proposals (
 quote_id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES customer_threads(id), version INTEGER NOT NULL,
 digest TEXT NOT NULL, document TEXT NOT NULL, plan TEXT NOT NULL, status TEXT NOT NULL,
 UNIQUE(conversation_id,version)
);
CREATE TABLE conversation_allocations (
 message_id TEXT PRIMARY KEY REFERENCES conversation_messages(id), run_id TEXT NOT NULL,
 maximum_micro_usd INTEGER NOT NULL CHECK(maximum_micro_usd>0), created_at REAL NOT NULL
);
CREATE INDEX conversation_messages_thread ON conversation_messages(conversation_id,created_at);
