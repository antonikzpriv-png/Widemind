/** Revision changes must fence work decisions as well as idle decisions. */
import test from "node:test";
import assert from "node:assert/strict";
import { fixture } from "./helpers.ts";
import { Store } from "../src/runtime/store.ts";
import { openDatabase } from "../src/db/database.ts";

const event = (externalEventId: string) => ({
  source: "fixture-provider",
  accountRef: "fixture-account",
  externalEventId,
  kind: "message.received",
  payload: { text: "Synthetic event" },
});

for (const change of ["event", "stop-resume", "wake"] as const) {
  test(`a ${change} revision during work selection preserves events and prevents stale execution`, async () => {
    const f = fixture();
    const second = openDatabase(f.config.database);
    const other = new Store(second);
    try {
      const id = f.store.create("demo");
      f.store.control(id, "resume");
      f.store.deliverEvent(id, event("before-selection"));
      const select = f.services.business.selectWork;
      const authorize = f.services.economy.authorize;
      const execute = f.services.tools.execute;
      let selections = 0,
        authorizations = 0,
        executions = 0;
      f.services.economy.authorize = async (request) => {
        authorizations++;
        return authorize(request);
      };
      f.services.tools.execute = async (...args) => {
        executions++;
        return execute(...args);
      };
      f.services.business.selectWork = async (snapshot) => {
        selections++;
        const selected = await select(snapshot);
        assert.equal(selected.kind, "work");
        if (selections === 1) {
          if (change === "event")
            other.deliverEvent(id, event("during-selection"));
          else if (change === "stop-resume") {
            other.control(id, "stop");
            other.control(id, "resume");
          } else other.control(id, "wake");
        }
        return selected;
      };

      await f.runtime.tick();
      assert.equal(authorizations, 0);
      assert.equal(executions, 0);
      assert.equal(f.store.actions(id).length, 0);
      assert.equal(
        f.db.prepare("SELECT COUNT(*) AS n FROM runtime_outbox").get()?.n,
        0,
      );
      assert.equal(
        f.store.pendingEvents(id).length,
        change === "event" ? 2 : 1,
      );
      assert.equal(f.store.run(id)?.desired, "run");
      assert.ok(f.store.run(id)!.next_wake_at! <= Date.now());

      assert.equal(await f.runtime.tick(), true);
      assert.equal(selections, 2);
      assert.equal(authorizations, 1);
      assert.equal(executions, 1);
      assert.equal(f.store.actions(id)[0]?.state, "completed");
      assert.equal(f.store.pendingEvents(id).length, 0);
    } finally {
      second.close();
      f.close();
    }
  });
}

test("the store rejects a stale revision before either planning or acknowledging", async () => {
  const f = fixture();
  try {
    const id = f.store.create("demo");
    f.store.control(id, "resume");
    const delivered = f.store.deliverEvent(id, event("snapshot-event"));
    const lease = f.store.acquire("fixture-worker", Date.now(), 3000)!;
    const revision = f.store.run(id)!.revision;
    const selected = await f.services.business.selectWork({
      runId: id,
      now: new Date().toISOString(),
      financial: null,
      actions: [],
      capabilities: f.services.tools.capabilities(),
      events: f.store.pendingEvents(id),
    });
    assert.equal(selected.kind, "work");
    if (selected.kind !== "work") throw new Error("Expected fixture work");
    f.store.control(id, "wake");
    assert.equal(
      f.store.plan(
        lease,
        { ...selected.candidate, version: 1, actionId: "stale", runId: id },
        [delivered.eventId],
        revision,
      ),
      null,
    );
    assert.equal(f.store.actions(id).length, 0);
    assert.equal(f.store.pendingEvents(id).length, 1);
  } finally {
    f.close();
  }
});
