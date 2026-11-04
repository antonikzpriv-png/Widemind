// Pilot alerts: one pass by default (for cron), or `--watch <seconds>`.
// Exit status: 0 nothing active, 2 a condition is active, 3 delivery failed.
import { bootstrap } from "../bootstrap.ts";
import { alertPass, alertSettings, alertText } from "./alerts.ts";
import { option } from "./cli-args.ts";
import { pilotRunId } from "./pilot.ts";
const args = process.argv.slice(2);
const runId = pilotRunId(args, process.env);
if (!runId) throw new Error("Set WIDEMIND_COMMERCE_RUN_ID or pass --run <id>");
const watch = option(args, "--watch");
if (watch !== undefined && (!/^[0-9]{1,6}$/.test(watch) || Number(watch) < 30))
  throw new Error("--watch takes an interval of at least 30 seconds");
const settings = alertSettings(process.env);
const app = await bootstrap();
let stopping = false;
for (const signal of ["SIGINT", "SIGTERM"] as const)
  process.once(signal, () => {
    stopping = true;
  });
try {
  do {
    const result = await alertPass({
      db: app.db,
      services: app.services,
      config: app.config,
      runId,
      settings,
    });
    if (watch === undefined) console.log(JSON.stringify(result, null, 2));
    else if (result.notices.length)
      console.log(result.observedAt + "\n" + alertText(result.notices));
    if (!result.delivered)
      console.error(
        "Alert delivery failed; it will be retried on the next pass",
      );
    process.exitCode = !result.delivered ? 3 : result.active.length ? 2 : 0;
    if (watch === undefined) break;
    const until = Date.now() + Number(watch) * 1000;
    while (!stopping && Date.now() < until)
      await new Promise((resolve) => setTimeout(resolve, 500));
  } while (!stopping);
} finally {
  app.db.close();
}
