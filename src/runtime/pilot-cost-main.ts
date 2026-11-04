// Operator command: record a cost paid outside the ledger, or correct one.
import { bootstrap } from "../bootstrap.ts";
import { option, required } from "./cli-args.ts";
import { pilotRunId } from "./pilot.ts";
import {
  outsideLedgerCosts,
  recordCost,
  usdToMicros,
  type CostCategory,
} from "./pilot-records.ts";
const args = process.argv.slice(2);
const runId = pilotRunId(args, process.env);
if (!runId) throw new Error("Set WIDEMIND_COMMERCE_RUN_ID or pass --run <id>");
const app = await bootstrap();
try {
  if (args.includes("--list")) {
    console.log(
      JSON.stringify(outsideLedgerCosts(app.db, runId, true), null, 2),
    );
  } else {
    const amountMicros = usdToMicros(required(args, "--amount"));
    const evidenceRef = required(args, "--evidence");
    const note = option(args, "--note") ?? "";
    const corrects = option(args, "--corrects");
    const entry =
      corrects !== undefined
        ? recordCost(app.db, {
            runId,
            amountMicros,
            evidenceRef,
            note,
            correctsId: /^[1-9][0-9]{0,15}$/.test(corrects)
              ? Number(corrects)
              : NaN,
          })
        : recordCost(app.db, {
            runId,
            amountMicros,
            evidenceRef,
            note,
            category: required(args, "--category") as CostCategory,
            periodStart: required(args, "--from"),
            periodEnd: required(args, "--to"),
          });
    console.log(JSON.stringify(entry, null, 2));
  }
} finally {
  app.db.close();
}
