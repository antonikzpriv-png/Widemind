CREATE TABLE reconciliation_jobs (
 task_id TEXT PRIMARY KEY, provider TEXT NOT NULL, account TEXT NOT NULL,
 mode TEXT NOT NULL CHECK(mode IN ('test','live')), request_key TEXT NOT NULL,
 fingerprint TEXT NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('webhook','payment')),
 payload TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('pending','running','completed','dead_letter')),
 attempts INTEGER NOT NULL DEFAULT 0, next_attempt_at INTEGER NOT NULL,
 lease_until INTEGER, lease_token TEXT, last_error_code TEXT,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 UNIQUE(provider,account,mode,request_key)
);
CREATE INDEX reconciliation_due ON reconciliation_jobs(provider,account,mode,state,next_attempt_at,lease_until);
CREATE TABLE reconciliation_attempts (
 record_id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES reconciliation_jobs(task_id),
 action TEXT NOT NULL, attempt INTEGER NOT NULL, occurred_at TEXT NOT NULL, details TEXT NOT NULL
);
CREATE INDEX reconciliation_attempts_task ON reconciliation_attempts(task_id);
CREATE TABLE reconciliation_operator_actions (
 task_id TEXT NOT NULL REFERENCES reconciliation_jobs(task_id), idempotency_key TEXT NOT NULL,
 fingerprint TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL,
 PRIMARY KEY(task_id,idempotency_key)
);
CREATE TRIGGER reconciliation_attempts_no_update BEFORE UPDATE ON reconciliation_attempts BEGIN SELECT RAISE(ABORT,'append-only reconciliation attempts'); END;
CREATE TRIGGER reconciliation_attempts_no_delete BEFORE DELETE ON reconciliation_attempts BEGIN SELECT RAISE(ABORT,'append-only reconciliation attempts'); END;
CREATE TRIGGER reconciliation_attempts_no_replace BEFORE INSERT ON reconciliation_attempts WHEN EXISTS(SELECT 1 FROM reconciliation_attempts WHERE record_id=NEW.record_id) BEGIN SELECT RAISE(ABORT,'append-only reconciliation attempts'); END;
CREATE TRIGGER reconciliation_operator_actions_no_update BEFORE UPDATE ON reconciliation_operator_actions BEGIN SELECT RAISE(ABORT,'append-only reconciliation operator actions'); END;
CREATE TRIGGER reconciliation_operator_actions_no_delete BEFORE DELETE ON reconciliation_operator_actions BEGIN SELECT RAISE(ABORT,'append-only reconciliation operator actions'); END;
CREATE TRIGGER reconciliation_operator_actions_no_replace BEFORE INSERT ON reconciliation_operator_actions WHEN EXISTS(SELECT 1 FROM reconciliation_operator_actions WHERE task_id=NEW.task_id AND idempotency_key=NEW.idempotency_key) BEGIN SELECT RAISE(ABORT,'append-only reconciliation operator actions'); END;

CREATE TRIGGER reconciliation_jobs_identity_immutable BEFORE UPDATE OF task_id,provider,account,mode,request_key,fingerprint,kind,payload,created_at ON reconciliation_jobs WHEN NEW.task_id IS NOT OLD.task_id OR NEW.provider IS NOT OLD.provider OR NEW.account IS NOT OLD.account OR NEW.mode IS NOT OLD.mode OR NEW.request_key IS NOT OLD.request_key OR NEW.fingerprint IS NOT OLD.fingerprint OR NEW.kind IS NOT OLD.kind OR NEW.payload IS NOT OLD.payload OR NEW.created_at IS NOT OLD.created_at BEGIN SELECT RAISE(ABORT,'immutable verified reconciliation input'); END;
CREATE TRIGGER reconciliation_jobs_no_delete BEFORE DELETE ON reconciliation_jobs BEGIN SELECT RAISE(ABORT,'reconciliation history cannot be deleted'); END;
CREATE TRIGGER reconciliation_jobs_no_replace BEFORE INSERT ON reconciliation_jobs WHEN EXISTS(SELECT 1 FROM reconciliation_jobs WHERE task_id=NEW.task_id OR (provider=NEW.provider AND account=NEW.account AND mode=NEW.mode AND request_key=NEW.request_key)) BEGIN SELECT RAISE(ABORT,'immutable verified reconciliation input'); END;
