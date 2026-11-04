// Operator command: record (or withdraw) that the paying customer is independent.
import { bootstrap } from "../bootstrap.ts";
import { option, required } from "./cli-args.ts";
import { pilotRunId } from "./pilot.ts";
import { recordAttestation } from "./pilot-records.ts";
const args = process.argv.slice(2);
const withdraw = args.includes("--withdraw");
if (withdraw === args.includes("--independent-customer"))
  throw new Error(
    "Pass exactly one of --independent-customer or --withdraw, with --evidence <private reference>",
  );
const runId = pilotRunId(args, process.env);
if (!runId) throw new Error("Set WIDEMIND_COMMERCE_RUN_ID or pass --run <id>");
const app = await bootstrap();
try {
  const entry = recordAttestation(app.db, {
    runId,
    stance: withdraw ? "withdrawn" : "affirmed",
    evidenceRef: required(args, "--evidence"),
    note: option(args, "--note") ?? "",
  });
  console.log(JSON.stringify(entry, null, 2));
} finally {
  app.db.close();
}
