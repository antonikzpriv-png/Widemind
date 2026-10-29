import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { bootstrap } from "../src/bootstrap.ts";

test("programmatic bootstrap uses its explicit private environment for module configuration", async () => {
  const directory = mkdtempSync(join(tmpdir(), "widemind-bootstrap-env-"));
  const names = [
    "WIDEMIND_ECONOMY_URL",
    "WIDEMIND_GATEWAY_TOKEN",
    "WIDEMIND_OPERATOR_TOKEN",
  ] as const;
  const saved = names.map((name) => process.env[name]);
  process.env.WIDEMIND_ECONOMY_URL = "http://127.0.0.1:8080";
  process.env.WIDEMIND_GATEWAY_TOKEN = "g".repeat(48);
  process.env.WIDEMIND_OPERATOR_TOKEN = "o".repeat(48);
  let app;
  try {
    app = await bootstrap({
      WIDEMIND_DATABASE: join(directory, "runtime.sqlite3"),
      WIDEMIND_PROFILE: "demo",
    });
    assert.ok(
      !app.services.routes.some(
        (route) => route.path === "/api/modules/economy/open",
      ),
    );
    assert.equal(await app.services.economy.summary("demo"), null);
  } finally {
    app?.db.close();
    for (const [index, name] of names.entries()) {
      if (saved[index] === undefined) delete process.env[name];
      else process.env[name] = saved[index];
    }
    rmSync(directory, { recursive: true, force: true });
  }
});
