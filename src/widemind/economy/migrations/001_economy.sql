CREATE TABLE IF NOT EXISTS runs (
 run_id TEXT PRIMARY KEY, mode TEXT NOT NULL CHECK(mode IN ('test','live')), created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ledger (
 entry_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id),
 kind TEXT NOT NULL, category TEXT NOT NULL, amount_micro_usd INTEGER NOT NULL CHECK(typeof(amount_micro_usd)='integer'),
 reference TEXT NOT NULL, metadata TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ledger_run ON ledger(run_id);
CREATE UNIQUE INDEX IF NOT EXISTS one_opening ON ledger(run_id) WHERE kind='opening_grant';
CREATE TABLE IF NOT EXISTS reservations (
 reservation_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id), action_id TEXT NOT NULL,
 amount_micro_usd INTEGER NOT NULL CHECK(typeof(amount_micro_usd)='integer' AND amount_micro_usd>0),
 provider TEXT NOT NULL, account TEXT NOT NULL, limits_json TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('reserved','running','unknown','settled','cancelled')),
 actual_micro_usd INTEGER, provisional_micro_usd INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 UNIQUE(run_id,action_id)
);
CREATE TABLE IF NOT EXISTS holds (
 run_id TEXT NOT NULL REFERENCES runs(run_id), hold_id TEXT NOT NULL, kind TEXT NOT NULL,
 amount_micro_usd INTEGER NOT NULL CHECK(typeof(amount_micro_usd)='integer' AND amount_micro_usd>=0),
 reason TEXT NOT NULL, PRIMARY KEY(run_id,hold_id)
);
CREATE TABLE IF NOT EXISTS operations (
 run_id TEXT NOT NULL REFERENCES runs(run_id), scope TEXT NOT NULL, idempotency_key TEXT NOT NULL,
 fingerprint TEXT NOT NULL, result TEXT NOT NULL, PRIMARY KEY(run_id,scope,idempotency_key)
);
CREATE TABLE IF NOT EXISTS charges (
 provider TEXT NOT NULL, account TEXT NOT NULL, mode TEXT NOT NULL, external_charge_id TEXT NOT NULL,
 reservation_id TEXT NOT NULL REFERENCES reservations(reservation_id), amount_micro_usd INTEGER NOT NULL,
 usage TEXT NOT NULL, outcome TEXT NOT NULL, PRIMARY KEY(provider,account,mode,external_charge_id), UNIQUE(reservation_id)
);
CREATE TABLE IF NOT EXISTS provisional_charges (
 provider TEXT NOT NULL, account TEXT NOT NULL, mode TEXT NOT NULL, external_charge_id TEXT NOT NULL,
 reservation_id TEXT NOT NULL UNIQUE REFERENCES reservations(reservation_id), amount_micro_usd INTEGER NOT NULL,
 PRIMARY KEY(provider,account,mode,external_charge_id)
);
CREATE TABLE IF NOT EXISTS events (
 sequence INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE,
 version INTEGER NOT NULL CHECK(version=1), type TEXT NOT NULL, run_id TEXT NOT NULL REFERENCES runs(run_id),
 occurred_at TEXT NOT NULL, data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS payments (
 provider TEXT NOT NULL, account TEXT NOT NULL, mode TEXT NOT NULL, payment_id TEXT NOT NULL,
 run_id TEXT NOT NULL REFERENCES runs(run_id), job_id TEXT, status TEXT NOT NULL DEFAULT 'pending',
 revision INTEGER NOT NULL DEFAULT 0, released_micro_usd INTEGER NOT NULL DEFAULT 0, pending_liability_micro_usd INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(provider,account,mode,payment_id)
);
CREATE TABLE IF NOT EXISTS payment_transactions (
 provider TEXT NOT NULL, account TEXT NOT NULL, mode TEXT NOT NULL, transaction_id TEXT NOT NULL,
 payment_id TEXT NOT NULL, run_id TEXT NOT NULL REFERENCES runs(run_id), kind TEXT NOT NULL,
 amount_micro_usd INTEGER NOT NULL, fee_micro_usd INTEGER NOT NULL, net_micro_usd INTEGER NOT NULL,
 native TEXT NOT NULL, PRIMARY KEY(provider,account,mode,transaction_id)
);
CREATE TABLE IF NOT EXISTS payment_events (
 provider TEXT NOT NULL, account TEXT NOT NULL, mode TEXT NOT NULL, event_id TEXT NOT NULL,
 digest TEXT NOT NULL, payment_id TEXT NOT NULL, PRIMARY KEY(provider,account,mode,event_id)
);
CREATE TABLE IF NOT EXISTS checkouts (
 provider TEXT NOT NULL, account TEXT NOT NULL, mode TEXT NOT NULL, request_id TEXT NOT NULL,
 run_id TEXT NOT NULL REFERENCES runs(run_id), job_id TEXT, amount_micro_usd INTEGER NOT NULL,
 details TEXT NOT NULL, fingerprint TEXT NOT NULL, payment_id TEXT, url TEXT, created_at TEXT NOT NULL,
 PRIMARY KEY(provider,account,mode,request_id)
);
CREATE TABLE IF NOT EXISTS reconciliations (
 reconciliation_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id),
 provider TEXT NOT NULL, account TEXT NOT NULL, payment_id TEXT NOT NULL,
 revision INTEGER NOT NULL, created_at TEXT NOT NULL, summary TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS ledger_no_update BEFORE UPDATE ON ledger BEGIN SELECT RAISE(ABORT,'append-only ledger'); END;
CREATE TRIGGER IF NOT EXISTS ledger_no_delete BEFORE DELETE ON ledger BEGIN SELECT RAISE(ABORT,'append-only ledger'); END;
CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events BEGIN SELECT RAISE(ABORT,'append-only events'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events BEGIN SELECT RAISE(ABORT,'append-only events'); END;
CREATE TRIGGER IF NOT EXISTS operations_no_update BEFORE UPDATE ON operations BEGIN SELECT RAISE(ABORT,'append-only operations'); END;
CREATE TRIGGER IF NOT EXISTS operations_no_delete BEFORE DELETE ON operations BEGIN SELECT RAISE(ABORT,'append-only operations'); END;
CREATE TRIGGER IF NOT EXISTS charges_no_update BEFORE UPDATE ON charges BEGIN SELECT RAISE(ABORT,'append-only charges'); END;
CREATE TRIGGER IF NOT EXISTS charges_no_delete BEFORE DELETE ON charges BEGIN SELECT RAISE(ABORT,'append-only charges'); END;
CREATE TRIGGER IF NOT EXISTS payment_transactions_no_update BEFORE UPDATE ON payment_transactions BEGIN SELECT RAISE(ABORT,'append-only transactions'); END;
CREATE TRIGGER IF NOT EXISTS payment_transactions_no_delete BEFORE DELETE ON payment_transactions BEGIN SELECT RAISE(ABORT,'append-only transactions'); END;
CREATE TRIGGER IF NOT EXISTS payment_events_no_update BEFORE UPDATE ON payment_events BEGIN SELECT RAISE(ABORT,'append-only payment events'); END;
CREATE TRIGGER IF NOT EXISTS payment_events_no_delete BEFORE DELETE ON payment_events BEGIN SELECT RAISE(ABORT,'append-only payment events'); END;
CREATE TRIGGER IF NOT EXISTS reconciliations_no_update BEFORE UPDATE ON reconciliations BEGIN SELECT RAISE(ABORT,'append-only reconciliations'); END;
CREATE TRIGGER IF NOT EXISTS reconciliations_no_delete BEFORE DELETE ON reconciliations BEGIN SELECT RAISE(ABORT,'append-only reconciliations'); END;

CREATE TABLE IF NOT EXISTS payment_components (
 provider TEXT NOT NULL, account TEXT NOT NULL, mode TEXT NOT NULL, transaction_id TEXT NOT NULL,
 component TEXT NOT NULL CHECK(component IN ('gross','fee')), entry_id TEXT NOT NULL REFERENCES ledger(entry_id),
 amount_micro_usd INTEGER NOT NULL,
 PRIMARY KEY(provider,account,mode,transaction_id,component)
);
CREATE TRIGGER IF NOT EXISTS payment_components_no_update BEFORE UPDATE ON payment_components BEGIN SELECT RAISE(ABORT,'append-only payment components'); END;
CREATE TRIGGER IF NOT EXISTS payment_components_no_delete BEFORE DELETE ON payment_components BEGIN SELECT RAISE(ABORT,'append-only payment components'); END;

CREATE TRIGGER IF NOT EXISTS ledger_no_replace BEFORE INSERT ON ledger WHEN EXISTS(SELECT 1 FROM ledger WHERE entry_id=NEW.entry_id) OR (NEW.kind='opening_grant' AND EXISTS(SELECT 1 FROM ledger WHERE run_id=NEW.run_id AND kind='opening_grant')) BEGIN SELECT RAISE(ABORT,'append-only identity cannot be replaced'); END;

CREATE TRIGGER IF NOT EXISTS events_no_replace BEFORE INSERT ON events WHEN EXISTS(SELECT 1 FROM events WHERE sequence=NEW.sequence) OR EXISTS(SELECT 1 FROM events WHERE event_id=NEW.event_id) BEGIN SELECT RAISE(ABORT,'append-only identity cannot be replaced'); END;

CREATE TRIGGER IF NOT EXISTS operations_no_replace BEFORE INSERT ON operations WHEN EXISTS(SELECT 1 FROM operations WHERE run_id=NEW.run_id AND scope=NEW.scope AND idempotency_key=NEW.idempotency_key) BEGIN SELECT RAISE(ABORT,'append-only identity cannot be replaced'); END;

CREATE TRIGGER IF NOT EXISTS charges_no_replace BEFORE INSERT ON charges WHEN EXISTS(SELECT 1 FROM charges WHERE provider=NEW.provider AND account=NEW.account AND mode=NEW.mode AND external_charge_id=NEW.external_charge_id) OR EXISTS(SELECT 1 FROM charges WHERE reservation_id=NEW.reservation_id) BEGIN SELECT RAISE(ABORT,'append-only identity cannot be replaced'); END;

CREATE TRIGGER IF NOT EXISTS payment_transactions_no_replace BEFORE INSERT ON payment_transactions WHEN EXISTS(SELECT 1 FROM payment_transactions WHERE provider=NEW.provider AND account=NEW.account AND mode=NEW.mode AND transaction_id=NEW.transaction_id) BEGIN SELECT RAISE(ABORT,'append-only identity cannot be replaced'); END;

CREATE TRIGGER IF NOT EXISTS payment_events_no_replace BEFORE INSERT ON payment_events WHEN EXISTS(SELECT 1 FROM payment_events WHERE provider=NEW.provider AND account=NEW.account AND mode=NEW.mode AND event_id=NEW.event_id) BEGIN SELECT RAISE(ABORT,'append-only identity cannot be replaced'); END;

CREATE TRIGGER IF NOT EXISTS reconciliations_no_replace BEFORE INSERT ON reconciliations WHEN EXISTS(SELECT 1 FROM reconciliations WHERE reconciliation_id=NEW.reconciliation_id) BEGIN SELECT RAISE(ABORT,'append-only identity cannot be replaced'); END;

CREATE TRIGGER IF NOT EXISTS payment_components_no_replace BEFORE INSERT ON payment_components WHEN EXISTS(SELECT 1 FROM payment_components WHERE provider=NEW.provider AND account=NEW.account AND mode=NEW.mode AND transaction_id=NEW.transaction_id AND component=NEW.component) BEGIN SELECT RAISE(ABORT,'append-only identity cannot be replaced'); END;
