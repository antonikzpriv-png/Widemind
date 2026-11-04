import { bootstrap } from "../bootstrap.ts";
import { pilotReadiness, pilotRunId } from "./pilot.ts";
const app = await bootstrap();
try {
  const readiness = await pilotReadiness({
    env: process.env,
    config: app.config,
    operatorPasswordConfigured: Boolean(app.secrets.operatorPasswordHash),
    services: app.services,
    db: app.db,
    runId: pilotRunId(process.argv.slice(2), process.env),
  });
  console.log(JSON.stringify(readiness, null, 2));
  if (!readiness.ready) process.exitCode = 2;
} finally {
  app.db.close();
}
