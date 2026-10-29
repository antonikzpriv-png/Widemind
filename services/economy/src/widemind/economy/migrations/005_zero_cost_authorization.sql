CREATE TABLE reservations_v5 (
 reservation_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id), action_id TEXT NOT NULL,
 amount_micro_usd INTEGER NOT NULL CHECK(typeof(amount_micro_usd)='integer' AND amount_micro_usd>=0),
 provider TEXT NOT NULL, account TEXT NOT NULL, limits_json TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('reserved','running','unknown','settled','cancelled')),
 actual_micro_usd INTEGER, provisional_micro_usd INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 UNIQUE(run_id,action_id)
);

INSERT INTO reservations_v5 SELECT * FROM reservations;
DROP TABLE reservations;
ALTER TABLE reservations_v5 RENAME TO reservations;
CREATE TRIGGER reservations_identity_guard BEFORE UPDATE OF reservation_id,run_id,action_id,amount_micro_usd,provider,account,limits_json,created_at ON reservations WHEN NEW.reservation_id IS NOT OLD.reservation_id OR NEW.run_id IS NOT OLD.run_id OR NEW.action_id IS NOT OLD.action_id OR NEW.amount_micro_usd IS NOT OLD.amount_micro_usd OR NEW.provider IS NOT OLD.provider OR NEW.account IS NOT OLD.account OR NEW.limits_json IS NOT OLD.limits_json OR NEW.created_at IS NOT OLD.created_at BEGIN SELECT RAISE(ABORT,'immutable accounting authorization'); END;
CREATE TRIGGER reservations_delete_guard BEFORE DELETE ON reservations BEGIN SELECT RAISE(ABORT,'accounting identities cannot be deleted'); END;
CREATE TRIGGER reservations_replace_guard BEFORE INSERT ON reservations WHEN EXISTS(SELECT 1 FROM reservations WHERE reservation_id=NEW.reservation_id) OR EXISTS(SELECT 1 FROM reservations WHERE run_id=NEW.run_id AND action_id=NEW.action_id) BEGIN SELECT RAISE(ABORT,'immutable accounting authorization'); END;
CREATE TRIGGER reservations_numeric_insert BEFORE INSERT ON reservations WHEN typeof(NEW.provisional_micro_usd)!='integer' OR NEW.provisional_micro_usd<0 OR (NEW.actual_micro_usd IS NOT NULL AND (typeof(NEW.actual_micro_usd)!='integer' OR NEW.actual_micro_usd<0)) BEGIN SELECT RAISE(ABORT,'invalid accounting projection'); END;
CREATE TRIGGER reservations_numeric_update BEFORE UPDATE ON reservations WHEN typeof(NEW.provisional_micro_usd)!='integer' OR NEW.provisional_micro_usd<0 OR (NEW.actual_micro_usd IS NOT NULL AND (typeof(NEW.actual_micro_usd)!='integer' OR NEW.actual_micro_usd<0)) BEGIN SELECT RAISE(ABORT,'invalid accounting projection'); END;
