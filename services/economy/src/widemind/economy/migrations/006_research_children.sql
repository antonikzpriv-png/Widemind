-- service-v1 only. Allocation identity and original quoted amounts never change.
CREATE TABLE research_reservation_children (
 parent_reservation_id TEXT NOT NULL REFERENCES reservations(reservation_id),
 child_reservation_id TEXT NOT NULL UNIQUE REFERENCES reservations(reservation_id),
 created_at TEXT NOT NULL,
 PRIMARY KEY(parent_reservation_id,child_reservation_id),
 CHECK(parent_reservation_id != child_reservation_id)
);
CREATE TRIGGER research_children_no_update BEFORE UPDATE ON research_reservation_children BEGIN SELECT RAISE(ABORT,'immutable research allocation'); END;
CREATE TRIGGER research_children_no_delete BEFORE DELETE ON research_reservation_children BEGIN SELECT RAISE(ABORT,'immutable research allocation'); END;
CREATE TRIGGER research_children_no_replace BEFORE INSERT ON research_reservation_children WHEN EXISTS(SELECT 1 FROM research_reservation_children WHERE child_reservation_id=NEW.child_reservation_id) BEGIN SELECT RAISE(ABORT,'immutable research allocation'); END;
CREATE TRIGGER research_children_binding BEFORE INSERT ON research_reservation_children WHEN
 NOT EXISTS(SELECT 1 FROM reservations p JOIN reservations c ON c.reservation_id=NEW.child_reservation_id WHERE p.reservation_id=NEW.parent_reservation_id AND p.provider='research.collect' AND p.state IN ('running','unknown') AND p.provisional_micro_usd=0 AND (p.actual_micro_usd IS NULL OR p.actual_micro_usd=0) AND c.state='reserved' AND c.run_id=p.run_id AND c.account=p.account AND c.provider IN ('ai.generate','research.fetch','browser.read','research.search','files'))
 OR EXISTS(SELECT 1 FROM research_reservation_children WHERE child_reservation_id=NEW.parent_reservation_id OR parent_reservation_id=NEW.child_reservation_id)
 OR (SELECT COALESCE(SUM(c.amount_micro_usd),0) FROM research_reservation_children l JOIN reservations c ON c.reservation_id=l.child_reservation_id WHERE l.parent_reservation_id=NEW.parent_reservation_id)+(SELECT amount_micro_usd FROM reservations WHERE reservation_id=NEW.child_reservation_id)>(SELECT amount_micro_usd FROM reservations WHERE reservation_id=NEW.parent_reservation_id)
 BEGIN SELECT RAISE(ABORT,'invalid research child authorization'); END;
