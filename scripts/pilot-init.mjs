// Explicit sandbox opening. This command never fabricates a live opening grant.
import { bootstrap } from "../src/bootstrap.ts";
import { Store } from "../src/runtime/store.ts";
if (!process.argv.includes("--sandbox"))
  throw new Error(
    "Pass --sandbox to explicitly create test credit. Live funding uses verified operator infrastructure.",
  );
const app = await bootstrap();
try {
  const id = process.env.WIDEMIND_COMMERCE_RUN_ID;
  if (!id || !/^[a-f0-9-]{36}$/.test(id))
    throw new Error(
      "Run services:configure first; WIDEMIND_COMMERCE_RUN_ID is required.",
    );
  const store = new Store(app.db);
  if (!store.run(id)) store.create(app.config.profile, Date.now(), id);
  const url = process.env.WIDEMIND_ECONOMY_URL,
    token = process.env.WIDEMIND_ECONOMY_OPERATOR_TOKEN;
  if (!url || !token)
    throw new Error("Economy service URL and operator credential required");
  const response = await fetch(url + "/v1/runs", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: "Bearer " + token,
    },
    body: JSON.stringify({ run_id: id, mode: "test" }),
    signal: AbortSignal.timeout(10000),
  });
  if (!response.ok)
    throw new Error("Economy refused sandbox opening: " + response.status);
  console.log(
    JSON.stringify({
      runId: id,
      mode: "sandbox",
      runtimeState: store.run(id).state,
      openingCreditMicros: "5000000",
      liveMoney: false,
    }),
  );
} finally {
  app.db.close();
}
