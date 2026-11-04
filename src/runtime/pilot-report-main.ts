import { bootstrap } from "../bootstrap.ts";
import { pilotReport, pilotRunId } from "./pilot.ts";
const args = process.argv.slice(2);
const runId = pilotRunId(args, process.env);
if (!runId) throw new Error("Set WIDEMIND_COMMERCE_RUN_ID or pass --run <id>");
const app = await bootstrap();
try {
  const report = await pilotReport({
    db: app.db,
    services: app.services,
    runId,
    view: args.includes("--public") ? "public" : "private",
  });
  console.log(JSON.stringify(report, null, 2));
} finally {
  app.db.close();
}
