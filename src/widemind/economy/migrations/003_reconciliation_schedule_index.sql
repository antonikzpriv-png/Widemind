CREATE INDEX reconciliation_payment_work ON reconciliation_jobs(
 provider, account, mode, kind, json_extract(payload,'$.payment_id'), state, next_attempt_at
);
