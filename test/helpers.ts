import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { openDatabase } from "../src/db/database.ts";
import {
  registerModules,
  unavailableServices,
} from "../src/modules/registry.ts";
import { definition } from "../src/modules/demo/module.ts";
import { Store } from "../src/runtime/store.ts";
import { AgentRuntime } from "../src/runtime/engine.ts";
import type {
  RuntimeConfig,
  ToolAction,
  ToolReceipt,
} from "../src/contracts/index.ts";
import { money } from "../src/contracts/index.ts";
export function fixture(profile: "foundation" | "demo" = "demo") {
  const dir = mkdtempSync(join(tmpdir(), "widemind-test-"));
  const config: RuntimeConfig = {
    database: join(dir, "db.sqlite"),
    host: "127.0.0.1",
    port: 3000,
    publicOrigin: "http://127.0.0.1:3000",
    profile,
    secureCookies: false,
    leaseMs: 3000,
    idleMs: 1000,
  };
  const db = openDatabase(config.database);
  const services = registerModules(
    { db, config },
    profile === "demo" ? [definition] : [],
  );
  const store = new Store(db),
    runtime = new AgentRuntime(store, services, config);
  return {
    dir,
    config,
    db,
    services,
    store,
    runtime,
    close() {
      db.close();
      rmSync(dir, { recursive: true, force: true });
    },
  };
}
export function receipt(action: ToolAction): ToolReceipt {
  return {
    version: 1,
    actionId: action.actionId,
    status: "succeeded",
    result: { text: "private-customer-marker" },
    usage: {
      actualCharge: money("0"),
      provider: "fixture",
      accountRef: "fixture-account",
      externalEventId: action.actionId,
      billableFailure: false,
      provisional: false,
      evidenceRef: "test-fixture",
    },
  };
}
export { unavailableServices };
