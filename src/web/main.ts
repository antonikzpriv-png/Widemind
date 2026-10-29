import { bootstrap } from "../bootstrap.ts";
import { webServer } from "./server.ts";
const app = await bootstrap();
const server = webServer(app);
server.listen(app.config.port, app.config.host, () =>
  console.log(
    `WideMind web listening on ${app.config.publicOrigin}; worker is a separate process.`,
  ),
);
for (const signal of ["SIGINT", "SIGTERM"] as const)
  process.on(signal, () =>
    server.close(() => {
      app.db.close();
    }),
  );
