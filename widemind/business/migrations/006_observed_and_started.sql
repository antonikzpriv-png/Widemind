ALTER TABLE orders ADD COLUMN payment_observed_at REAL;
ALTER TABLE effects ADD COLUMN started_at REAL;
UPDATE effects SET started_at=created_at WHERE state!='planned';
