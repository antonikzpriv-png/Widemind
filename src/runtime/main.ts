import { setTimeout as delay } from "node:timers/promises";
import { bootstrap } from "../bootstrap.ts";
import { Store } from "./store.ts";
import { AgentRuntime } from "./engine.ts";
const app = await bootstrap();
const runtime = new AgentRuntime(new Store(app.db), app.services, app.config);
const presenceTtl = Math.max(15000, app.config.leaseMs);
const seen = () =>
  runtime.store.workerSeen(runtime.owner, app.config.profile, presenceTtl);
seen();
const presence = setInterval(seen, 5000);
let shutdown = false;
for (const signal of ["SIGINT", "SIGTERM"] as const)
  process.on(signal, () => {
    shutdown = true;
    runtime.cancelActive();
  });
console.log(
  `WideMind worker started (${app.config.profile}); local scheduling never invokes a model.`,
);
try {
  while (!shutdown) {
    if (!(await runtime.tick())) await delay(500);
  }
} finally {
  clearInterval(presence);
  runtime.store.workerStopped(runtime.owner);
  app.db.close();
}
