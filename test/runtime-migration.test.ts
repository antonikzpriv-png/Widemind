import test from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { join } from "node:path";
import { openDatabase } from "../src/db/database.ts";
import { coreMigrations } from "../src/db/migrations/core.ts";
import { migrate } from "../src/db/migrate.ts";
import { Store } from "../src/runtime/store.ts";
import { AgentRuntime } from "../src/runtime/engine.ts";
import { fixture } from "./helpers.ts";
test("version-one saved runs and receipts upgrade without executing their tool again", async () => {
  const f = fixture();
  const legacy = openDatabase(join(f.dir, "version-one.sqlite"));
  try {
    const id = f.store.create("demo");
    f.store.control(id, "resume");
    await f.runtime.tick();
    const original = f.store.actions(id)[0]!;
    legacy.exec(coreMigrations[0]!.sql);
    legacy.exec(
      "CREATE TABLE schema_migrations(module TEXT,version INTEGER,checksum TEXT,applied_at INTEGER,PRIMARY KEY(module,version))",
    );
    legacy
      .prepare("INSERT INTO schema_migrations VALUES (?,?,?,?)")
      .run(
        "core",
        1,
        createHash("sha256").update(coreMigrations[0]!.sql).digest("hex"),
        Date.now(),
      );
    const store = new Store(legacy);
    const restored = store.create("demo");
    store.control(restored, "resume");
    const action = JSON.parse(original.action_json);
    action.runId = restored;
    const reservation = JSON.parse(original.reservation_json!);
    reservation.runId = restored;
    legacy
      .prepare(
        "INSERT INTO runtime_actions(id,run_id,work_key,state,action_json,reservation_json,receipt_json,attempts,created_at,updated_at) VALUES (?,?,?,'running',?,?,?,1,?,?)",
      )
      .run(
        original.id,
        restored,
        original.work_key,
        JSON.stringify(action),
        JSON.stringify(reservation),
        original.receipt_json,
        Date.now(),
        Date.now(),
      );
    legacy
      .prepare("INSERT INTO runtime_outbox VALUES (?,'inflight',?,?,?)")
      .run(original.id, original.id, Date.now(), Date.now());
    migrate(legacy);
    migrate(legacy);
    let calls = 0;
    f.services.tools.execute = async () => {
      calls++;
      throw new Error("Duplicate external execution");
    };
    await new AgentRuntime(store, f.services, f.config).tick();
    assert.equal(calls, 0);
    assert.equal(store.actions(restored)[0]?.state, "completed");
    assert.equal(store.actions(restored)[0]?.id, original.id);
    assert.equal(
      legacy
        .prepare(
          "SELECT COUNT(*) AS n FROM schema_migrations WHERE module='core'",
        )
        .get()?.n,
      2,
    );
  } finally {
    legacy.close();
    f.close();
  }
});
