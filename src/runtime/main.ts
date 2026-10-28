import { setTimeout as delay } from "node:timers/promises";
import { bootstrap } from "../bootstrap.ts";
import { Store } from "./store.ts";
import { AgentRuntime } from "./engine.ts";
const app = await bootstrap();
const runtime = new AgentRuntime(new Store(app.db), app.services, app.config);
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
  app.db.close();
}
