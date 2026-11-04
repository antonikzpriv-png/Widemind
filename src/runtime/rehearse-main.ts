// Pilot rehearsal against the running services, in sandbox mode only.
// Usage: npm run pilot:rehearse -- --email you@example.com [--origin URL]
import { bootstrap } from "../bootstrap.ts";
import { option, required } from "./cli-args.ts";
import { pilotRunId } from "./pilot.ts";
import { rehearsalAllowed, rehearse } from "./rehearsal.ts";
const args = process.argv.slice(2);
const refusal = rehearsalAllowed(process.env);
if (refusal) throw new Error(refusal);
const runId = pilotRunId(args, process.env);
if (!runId) throw new Error("Set WIDEMIND_COMMERCE_RUN_ID or pass --run <id>");
const email = required(args, "--email");
const app = await bootstrap();
try {
  const result = await rehearse({
    origin: option(args, "--origin") ?? app.config.publicOrigin,
    email,
    db: app.db,
    services: app.services,
    config: app.config,
    env: process.env,
    runId,
    log: (line) => console.error(line),
    onCheckout: async (url) => {
      console.error(
        `\nOpen this checkout link and pay with your PayPal sandbox buyer account:\n\n  ${url}\n\nWaiting for payment and delivery (up to 15 minutes)...`,
      );
    },
  });
  console.log(JSON.stringify(result, null, 2));
  if (!result.ok) process.exitCode = 2;
} finally {
  app.db.close();
}
