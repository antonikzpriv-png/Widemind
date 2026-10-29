import type { DatabaseSync } from "node:sqlite";
/** Only independently authenticated Gmail delivery updates create these rows. */
export function verifiedCommerceDelivery(
  db: DatabaseSync,
  runId: string,
  jobId: string,
  deliveryReference: string,
): boolean {
  return Boolean(
    db
      .prepare(
        `SELECT e.action_id FROM business_delivery_evidence e JOIN business_orders o ON o.id=e.job_id AND o.run_id=e.run_id WHERE e.run_id=? AND e.job_id=? AND e.evidence_ref=? AND o.delivery_reference=e.evidence_ref AND o.status IN ('delivered','completed')`,
      )
      .get(runId, jobId, deliveryReference),
  );
}
