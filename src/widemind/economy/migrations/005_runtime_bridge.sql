-- Preserve financial history and all reservation identities while admitting
-- native zero-cost authorizations. FK enforcement is disabled only by the
-- migration connection before its transaction; a full check precedes commit.
CREATE TABLE reservations_v5 (
 reservation_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id), action_id TEXT NOT NULL,
 amount_micro_usd INTEGER NOT NULL CHECK(typeof(amount_micro_usd)='integer' AND amount_micro_usd>=0),
 provider TEXT NOT NULL, account TEXT NOT NULL, limits_json TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('reserved','running','unknown','settled','cancelled')),
 actual_micro_usd INTEGER, provisional_micro_usd INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 UNIQUE(run_id,action_id)
);
INSERT INTO reservations_v5(rowid,reservation_id,run_id,action_id,amount_micro_usd,provider,account,limits_json,state,actual_micro_usd,provisional_micro_usd,created_at,updated_at)
 SELECT rowid,reservation_id,run_id,action_id,amount_micro_usd,provider,account,limits_json,state,actual_micro_usd,provisional_micro_usd,created_at,updated_at FROM reservations;
DROP TABLE reservations;
ALTER TABLE reservations_v5 RENAME TO reservations;
CREATE TRIGGER reservations_identity_guard BEFORE UPDATE OF reservation_id,run_id,action_id,amount_micro_usd,provider,account,limits_json,created_at ON reservations WHEN NEW.reservation_id IS NOT OLD.reservation_id OR NEW.run_id IS NOT OLD.run_id OR NEW.action_id IS NOT OLD.action_id OR NEW.amount_micro_usd IS NOT OLD.amount_micro_usd OR NEW.provider IS NOT OLD.provider OR NEW.account IS NOT OLD.account OR NEW.limits_json IS NOT OLD.limits_json OR NEW.created_at IS NOT OLD.created_at BEGIN SELECT RAISE(ABORT,'immutable accounting authorization'); END;
CREATE TRIGGER reservations_delete_guard BEFORE DELETE ON reservations BEGIN SELECT RAISE(ABORT,'accounting identities cannot be deleted'); END;
CREATE TRIGGER reservations_replace_guard BEFORE INSERT ON reservations WHEN EXISTS(SELECT 1 FROM reservations WHERE reservation_id=NEW.reservation_id) OR EXISTS(SELECT 1 FROM reservations WHERE run_id=NEW.run_id AND action_id=NEW.action_id) BEGIN SELECT RAISE(ABORT,'immutable accounting authorization'); END;
CREATE TRIGGER reservations_numeric_insert BEFORE INSERT ON reservations WHEN typeof(NEW.provisional_micro_usd)!='integer' OR NEW.provisional_micro_usd<0 OR (NEW.actual_micro_usd IS NOT NULL AND (typeof(NEW.actual_micro_usd)!='integer' OR NEW.actual_micro_usd<0)) BEGIN SELECT RAISE(ABORT,'invalid accounting projection'); END;
CREATE TRIGGER reservations_numeric_update BEFORE UPDATE ON reservations WHEN typeof(NEW.provisional_micro_usd)!='integer' OR NEW.provisional_micro_usd<0 OR (NEW.actual_micro_usd IS NOT NULL AND (typeof(NEW.actual_micro_usd)!='integer' OR NEW.actual_micro_usd<0)) BEGIN SELECT RAISE(ABORT,'invalid accounting projection'); END;
CREATE TABLE runtime_authorizations (
 reservation_id TEXT PRIMARY KEY REFERENCES reservations(reservation_id),
 run_id TEXT NOT NULL REFERENCES runs(run_id), action_id TEXT NOT NULL, tool TEXT NOT NULL,
 limit_json TEXT NOT NULL, provider TEXT NOT NULL, account TEXT NOT NULL,
 mode TEXT NOT NULL CHECK(mode IN ('test','live')), fingerprint TEXT NOT NULL,
 created_at TEXT NOT NULL, UNIQUE(run_id,action_id)
);
CREATE TRIGGER runtime_authorizations_no_update BEFORE UPDATE ON runtime_authorizations BEGIN SELECT RAISE(ABORT,'immutable runtime authorization'); END;
CREATE TRIGGER runtime_authorizations_no_delete BEFORE DELETE ON runtime_authorizations BEGIN SELECT RAISE(ABORT,'immutable runtime authorization'); END;
CREATE TRIGGER runtime_authorizations_no_replace BEFORE INSERT ON runtime_authorizations WHEN EXISTS(SELECT 1 FROM runtime_authorizations WHERE reservation_id=NEW.reservation_id) OR EXISTS(SELECT 1 FROM runtime_authorizations WHERE run_id=NEW.run_id AND action_id=NEW.action_id) BEGIN SELECT RAISE(ABORT,'immutable runtime authorization'); END;
CREATE TRIGGER runtime_authorizations_binding_guard BEFORE INSERT ON runtime_authorizations WHEN NOT EXISTS(SELECT 1 FROM reservations r JOIN runs u ON u.run_id=r.run_id WHERE r.reservation_id=NEW.reservation_id AND r.run_id=NEW.run_id AND r.action_id=NEW.action_id AND r.provider=NEW.provider AND r.account=NEW.account AND u.mode=NEW.mode) BEGIN SELECT RAISE(ABORT,'runtime authorization does not match accounting reservation'); END;
CREATE TABLE runtime_receipts (
 provider TEXT NOT NULL, account TEXT NOT NULL, mode TEXT NOT NULL CHECK(mode IN ('test','live')),
 event_id TEXT NOT NULL, reservation_id TEXT NOT NULL REFERENCES reservations(reservation_id),
 fingerprint TEXT NOT NULL, result_json TEXT NOT NULL, created_at TEXT NOT NULL,
 PRIMARY KEY(provider,account,mode,event_id)
);
CREATE TRIGGER runtime_receipts_no_update BEFORE UPDATE ON runtime_receipts BEGIN SELECT RAISE(ABORT,'immutable runtime receipt'); END;
CREATE TRIGGER runtime_receipts_no_delete BEFORE DELETE ON runtime_receipts BEGIN SELECT RAISE(ABORT,'immutable runtime receipt'); END;
CREATE TRIGGER runtime_receipts_no_replace BEFORE INSERT ON runtime_receipts WHEN EXISTS(SELECT 1 FROM runtime_receipts WHERE provider=NEW.provider AND account=NEW.account AND mode=NEW.mode AND event_id=NEW.event_id) BEGIN SELECT RAISE(ABORT,'immutable runtime receipt'); END;
CREATE TRIGGER runtime_receipts_binding_guard BEFORE INSERT ON runtime_receipts WHEN NOT EXISTS(SELECT 1 FROM reservations r JOIN runs u ON u.run_id=r.run_id WHERE r.reservation_id=NEW.reservation_id AND r.provider=NEW.provider AND r.account=NEW.account AND u.mode=NEW.mode) BEGIN SELECT RAISE(ABORT,'runtime receipt does not match accounting reservation'); END;
