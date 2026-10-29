CREATE TABLE host_requests (request_id TEXT PRIMARY KEY,run_id TEXT NOT NULL,opportunity_id TEXT NOT NULL,email TEXT NOT NULL,token_hash TEXT NOT NULL,created_at REAL NOT NULL,revoked_at REAL);
CREATE TABLE host_notifications (id TEXT PRIMARY KEY,run_id TEXT NOT NULL,customer_id TEXT NOT NULL,entity_id TEXT NOT NULL,purpose TEXT NOT NULL,subject TEXT NOT NULL,body TEXT NOT NULL,artifact TEXT,created_at REAL NOT NULL);
CREATE TABLE host_invocations (action_id TEXT PRIMARY KEY,run_id TEXT NOT NULL,event_id TEXT NOT NULL,state TEXT NOT NULL,result TEXT,created_at REAL NOT NULL);
CREATE TABLE host_payment_snapshots (job_id TEXT PRIMARY KEY,run_id TEXT NOT NULL,revision INTEGER NOT NULL,digest TEXT NOT NULL,document TEXT NOT NULL);
