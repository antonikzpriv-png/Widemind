CREATE TABLE business_runs (
 run_id TEXT PRIMARY KEY,
 mode TEXT NOT NULL CHECK(mode IN ('live','demo','test','unclassified')),
 evidence_reference TEXT NOT NULL
);
INSERT INTO business_runs SELECT run_id,'unclassified','created before run-mode pinning' FROM settings;
