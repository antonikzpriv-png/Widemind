import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import test from "node:test";
import { fixture } from "./helpers.ts";

test("trusted scheduled inbox commands deduplicate and preserve paused/stopped state without providers or funding", () => {
  const f = fixture("foundation");
  try {
    const runId = f.store.create("foundation");
    const env = {
      ...process.env,
      WIDEMIND_DATABASE: f.config.database,
      WIDEMIND_PROFILE: "foundation",
      WIDEMIND_BUSINESS_ENABLED: "true",
      WIDEMIND_ECONOMY_URL: "",
      WIDEMIND_BUSINESS_TOOLS_PROFILE: "",
      WIDEMIND_COMMERCE_ACCOUNT: "",
      WIDEMIND_COMMERCE_MODE: "",
    };
    const invoke = (requestId: string, selectedRun = runId) =>
      spawnSync(
        process.execPath,
        [
          "src/modules/business/sync-main.ts",
          "--run-id",
          selectedRun,
          "--request-id",
          requestId,
        ],
        { env, encoding: "utf8", timeout: 10000 },
      );
    const first = invoke("explicit-offline-scheduler-fixture");
    assert.equal(first.status, 0, first.stderr);
    assert.equal(JSON.parse(first.stdout).duplicate, false);
    assert.equal(f.store.run(runId)?.state, "paused");
    const replay = invoke("explicit-offline-scheduler-fixture");
    assert.equal(replay.status, 0, replay.stderr);
    assert.equal(JSON.parse(replay.stdout).duplicate, true);
    assert.equal(f.store.pendingEvents(runId).length, 1);
    f.store.control(runId, "stop");
    assert.equal(invoke("explicit-second-offline-scheduler-fixture").status, 0);
    assert.equal(f.store.run(runId)?.state, "stopped");
    assert.equal(f.store.actions(runId).length, 0);
    assert.equal(invoke("invalid\nidentity").status, 1);
    assert.equal(f.store.pendingEvents(runId).length, 2);
    const secondRun = f.store.create("foundation");
    assert.equal(
      invoke("explicit-offline-scheduler-fixture", secondRun).status,
      0,
    );
    assert.equal(f.store.pendingEvents(secondRun).length, 1);
    assert.equal(f.store.run(secondRun)?.state, "paused");
  } finally {
    f.close();
  }
});
