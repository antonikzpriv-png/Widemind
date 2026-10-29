ALTER TABLE effects ADD COLUMN logical_id TEXT;
ALTER TABLE effects ADD COLUMN attempt INTEGER NOT NULL DEFAULT 0;
UPDATE effects SET logical_id=id;
CREATE UNIQUE INDEX effects_attempt ON effects(logical_id,attempt);
