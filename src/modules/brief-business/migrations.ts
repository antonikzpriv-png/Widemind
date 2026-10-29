import type { Migration } from "../../contracts/index.ts";
export const migrations: readonly Migration[] = [
  {
    version: 1,
    sql: `
CREATE TABLE business_settings(run_id TEXT PRIMARY KEY REFERENCES runtime_runs(id),document TEXT NOT NULL);
CREATE TABLE business_customers(id TEXT PRIMARY KEY,run_id TEXT NOT NULL REFERENCES runtime_runs(id),email TEXT NOT NULL,opted_out INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL,UNIQUE(run_id,email));
CREATE TABLE business_opportunities(id TEXT PRIMARY KEY,run_id TEXT NOT NULL REFERENCES runtime_runs(id),customer_id TEXT REFERENCES business_customers(id),service TEXT NOT NULL,scope TEXT NOT NULL,origin TEXT NOT NULL,source_reference TEXT NOT NULL,priority INTEGER NOT NULL,status TEXT NOT NULL,assessment TEXT NOT NULL DEFAULT '{}',created_at TEXT NOT NULL);
CREATE TABLE business_quotes(id TEXT PRIMARY KEY,opportunity_id TEXT NOT NULL UNIQUE REFERENCES business_opportunities(id),scope TEXT NOT NULL,price_micros TEXT NOT NULL,cost_micros TEXT NOT NULL,turnaround_seconds INTEGER NOT NULL,expires_at TEXT NOT NULL,status TEXT NOT NULL);
CREATE TABLE business_orders(id TEXT PRIMARY KEY,run_id TEXT NOT NULL REFERENCES runtime_runs(id),quote_id TEXT NOT NULL UNIQUE REFERENCES business_quotes(id),customer_id TEXT NOT NULL REFERENCES business_customers(id),service TEXT NOT NULL,scope TEXT NOT NULL,status TEXT NOT NULL,price_micros TEXT NOT NULL,cost_micros TEXT NOT NULL,delivery_due_at TEXT,payment TEXT,payment_observed_at TEXT,artifact TEXT,delivery_reference TEXT,created_at TEXT NOT NULL,completed_at TEXT);
CREATE TABLE business_memory(id TEXT PRIMARY KEY,run_id TEXT NOT NULL,entity_id TEXT NOT NULL,kind TEXT NOT NULL,document TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE business_commands(event_id TEXT PRIMARY KEY,run_id TEXT NOT NULL,request TEXT NOT NULL,outcome TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE business_receipts(action_id TEXT PRIMARY KEY,run_id TEXT NOT NULL,work_key TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE business_inbox_requests(id TEXT PRIMARY KEY,run_id TEXT NOT NULL,cursor TEXT,state TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE business_cursors(run_id TEXT PRIMARY KEY,order_id TEXT NOT NULL,history_id TEXT NOT NULL,turn INTEGER NOT NULL);
CREATE TABLE business_mail(id TEXT PRIMARY KEY,run_id TEXT NOT NULL,message_id TEXT NOT NULL,customer_email TEXT NOT NULL,created_at TEXT NOT NULL,UNIQUE(run_id,message_id));
CREATE TABLE business_delivery_evidence(action_id TEXT PRIMARY KEY,run_id TEXT NOT NULL,job_id TEXT NOT NULL,evidence_ref TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE business_public_activity(id TEXT PRIMARY KEY,run_id TEXT NOT NULL,service TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE INDEX business_orders_run ON business_orders(run_id,status,created_at);
CREATE INDEX business_opportunities_run ON business_opportunities(run_id,status,created_at);
CREATE TRIGGER business_commands_no_update BEFORE UPDATE ON business_commands BEGIN SELECT RAISE(ABORT,'business command audit is append-only'); END;
CREATE TRIGGER business_commands_no_delete BEFORE DELETE ON business_commands BEGIN SELECT RAISE(ABORT,'business command audit is append-only'); END;
`,
  },
];
