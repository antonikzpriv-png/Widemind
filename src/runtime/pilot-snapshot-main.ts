// Pilot snapshots: save the public report (optionally publish it), on demand
// or `--every <seconds>`; also `--list`, `--verify` and `--retract <id>`.
import { bootstrap } from "../bootstrap.ts";
import { option } from "./cli-args.ts";
import { pilotRunId } from "./pilot.ts";
import {
  listSnapshots,
  retractSnapshot,
  takeSnapshot,
  verifyChain,
} from "./pilot-snapshots.ts";
const args = process.argv.slice(2);
const runId = pilotRunId(args, process.env);
if (!runId) throw new Error("Set WIDEMIND_COMMERCE_RUN_ID or pass --run <id>");
const every = option(args, "--every");
if (every !== undefined && (!/^[0-9]{1,7}$/.test(every) || Number(every) < 300))
  throw new Error("--every takes an interval of at least 300 seconds");
const retract = option(args, "--retract");
const app = await bootstrap();
let stopping = false;
for (const signal of ["SIGINT", "SIGTERM"] as const)
  process.once(signal, () => {
    stopping = true;
  });
try {
  if (args.includes("--list")) {
    console.log(JSON.stringify(listSnapshots(app.db, runId), null, 2));
  } else if (args.includes("--verify")) {
    const result = verifyChain(app.db, runId);
    console.log(JSON.stringify(result, null, 2));
    if (!result.ok) process.exitCode = 2;
  } else if (retract !== undefined) {
    if (!/^[1-9][0-9]{0,15}$/.test(retract))
      throw new Error("--retract takes a snapshot id");
    console.log(
      JSON.stringify(retractSnapshot(app.db, runId, Number(retract)), null, 2),
    );
  } else {
    do {
      const snapshot = await takeSnapshot({
        db: app.db,
        services: app.services,
        runId,
        publish: args.includes("--publish"),
      });
      console.log(JSON.stringify(snapshot));
      if (every === undefined) break;
      const until = Date.now() + Number(every) * 1000;
      while (!stopping && Date.now() < until)
        await new Promise((resolve) => setTimeout(resolve, 500));
    } while (!stopping);
  }
} finally {
  app.db.close();
}
