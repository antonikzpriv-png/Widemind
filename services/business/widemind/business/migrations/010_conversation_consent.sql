ALTER TABLE customer_threads ADD COLUMN contact_allowed INTEGER NOT NULL DEFAULT 1 CHECK(contact_allowed IN (0,1));
UPDATE customer_threads SET contact_allowed=0 WHERE status='opted_out';
