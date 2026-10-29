import { bootstrap } from "../bootstrap.ts";
import { Store } from "./store.ts";
const app = await bootstrap();
try {
  const status = new Store(app.db).runtimeStatus(app.config.profile);
  console.log(JSON.stringify(status, null, 2));
  if (!status.available) process.exitCode = 2;
} finally {
  app.db.close();
}
