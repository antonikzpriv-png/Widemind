import test from "node:test";
import assert from "node:assert/strict";
import { openDatabase } from "../src/db/database.ts";
import { Store } from "../src/runtime/store.ts";
import { AgentRuntime } from "../src/runtime/engine.ts";
import { fixture } from "./helpers.ts";
const event = (externalEventId = "provider-event-1") => ({
  source: "fixture-provider",
  accountRef: "fixture-account",
  externalEventId,
  kind: "message.received",
  payload: { text: "Private event: untrusted task data" },
});
test("event delivery is idempotent across restart and rejects changed identities", () => {
  const f = fixture();
  const second = openDatabase(f.config.database);
  try {
    const id = f.store.create("demo");
    const delivered = f.store.deliverEvent(id, event());
    const store = new Store(second);
    assert.equal(store.pendingEvents(id)[0]?.eventId, delivered.eventId);
    assert.deepEqual(store.deliverEvent(id, event()), {
      eventId: delivered.eventId,
      duplicate: true,
    });
    assert.throws(
      () => store.deliverEvent(id, { ...event(), payload: { changed: true } }),
      /conflicts/,
    );
    const other = store.create("demo");
    assert.throws(() => store.deliverEvent(other, event()), /conflicts/);
    assert.equal(store.pendingEvents(other).length, 0);
    assert.equal(store.pendingEvents(id).length, 1);
  } finally {
    second.close();
    f.close();
  }
});
test("events queue while paused or stopped without changing operator intent or funds", async () => {
  const f = fixture();
  try {
    const id = f.store.create("demo");
    f.store.deliverEvent(id, event());
    assert.equal(f.store.run(id)?.state, "paused");
    assert.equal(await f.runtime.tick(), false);
    f.store.control(id, "stop");
    await f.runtime.tick();
    f.store.deliverEvent(id, event("second"));
    assert.equal(f.store.run(id)?.state, "stopped");
    assert.equal(await f.runtime.tick(), false);
    assert.equal(f.store.pendingEvents(id).length, 2);
    assert.equal(await f.services.economy.summary(id), null);
  } finally {
    f.close();
  }
});
test("a delivered event wakes an idle run and acknowledged data is consumed durably", async () => {
  const f = fixture();
  try {
    const id = f.store.create("demo");
    f.store.control(id, "resume");
    f.services.business.selectWork = async (snapshot) => ({
      kind: "wait",
      reason: "Await event",
      acknowledgeEventIds: snapshot.events.map((item) => item.eventId),
    });
    await f.runtime.tick();
    assert.equal(await f.runtime.tick(), false);
    f.store.deliverEvent(id, event());
    assert.equal(f.store.run(id)?.state, "active");
    await f.runtime.tick();
    assert.equal(f.store.pendingEvents(id).length, 0);
    assert.equal(f.store.run(id)?.state, "waiting");
    assert.equal(await f.runtime.tick(), false);
    assert.equal(f.store.deliverEvent(id, event()).duplicate, true);
    assert.equal(await f.runtime.tick(), false);
  } finally {
    f.close();
  }
});
test("event arriving during selection cannot be lost by a stale wait decision", async () => {
  const f = fixture();
  try {
    const id = f.store.create("demo");
    f.store.control(id, "resume");
    f.store.deliverEvent(id, event());
    f.services.business.selectWork = async (snapshot) => {
      f.store.deliverEvent(id, event("arrived-during-select"));
      return {
        kind: "wait",
        reason: "Old snapshot",
        acknowledgeEventIds: snapshot.events.map((item) => item.eventId),
      };
    };
    await f.runtime.tick();
    assert.equal(f.store.pendingEvents(id).length, 2);
    f.services.business.selectWork = async (snapshot) => ({
      kind: "wait",
      reason: "Both processed",
      acknowledgeEventIds: snapshot.events.map((item) => item.eventId),
    });
    await f.runtime.tick();
    assert.equal(f.store.pendingEvents(id).length, 0);
  } finally {
    f.close();
  }
});
test("planning and acknowledging inbox records commit together or both roll back", async () => {
  const f = fixture();
  try {
    const id = f.store.create("demo");
    f.store.control(id, "resume");
    f.store.deliverEvent(id, event());
    const select = f.services.business.selectWork;
    f.services.business.selectWork = async (snapshot) => ({
      ...(await select(snapshot)),
      acknowledgeEventIds: snapshot.events.map((item) => item.eventId),
    });
    f.db.exec(
      "CREATE TRIGGER reject_plan BEFORE INSERT ON runtime_actions BEGIN SELECT RAISE(ABORT,'fixture failure'); END;",
    );
    await f.runtime.tick();
    assert.equal(f.store.run(id)?.state, "failed");
    assert.equal(f.store.pendingEvents(id).length, 1);
    assert.equal(f.store.actions(id).length, 0);
    f.db.exec("DROP TRIGGER reject_plan");
    f.store.control(id, "resume");
    await f.runtime.tick();
    assert.equal(f.store.pendingEvents(id).length, 0);
    assert.equal(f.store.actions(id)[0]?.state, "completed");
  } finally {
    f.close();
  }
});
test("business cannot acknowledge an event outside its inspected snapshot", async () => {
  const f = fixture();
  try {
    const id = f.store.create("demo");
    f.store.control(id, "resume");
    f.store.deliverEvent(id, event());
    f.services.business.selectWork = async () => ({
      kind: "wait",
      reason: "Invalid ack",
      acknowledgeEventIds: ["not-inspected"],
    });
    await f.runtime.tick();
    assert.equal(f.store.run(id)?.state, "failed");
    assert.equal(f.store.pendingEvents(id).length, 1);
  } finally {
    f.close();
  }
});
test("worker liveness expires after a hard stop and graceful stop is immediately unavailable", () => {
  const f = fixture();
  try {
    const now = Date.now();
    assert.equal(f.store.runtimeStatus("demo", now).available, false);
    f.store.workerSeen("worker-one", "demo", 15000, now);
    assert.equal(f.store.runtimeStatus("demo", now + 100).liveWorkers, 1);
    assert.equal(
      f.store.runtimeStatus("foundation", now + 100).available,
      false,
    );
    assert.equal(f.store.runtimeStatus("demo", now + 15000).available, false);
    f.store.workerSeen("worker-one", "demo", 15000, now + 16000);
    f.store.workerStopped("worker-one", now + 16001);
    assert.equal(f.store.runtimeStatus("demo", now + 16002).available, false);
  } finally {
    f.close();
  }
});
test("two concurrent ticks on one runtime cannot replace active cancellation ownership", async () => {
  const f = fixture();
  let release!: () => void;
  try {
    const id = f.store.create("demo");
    f.store.control(id, "resume");
    const select = f.services.business.selectWork;
    f.services.business.selectWork = async (snapshot) => {
      await new Promise<void>((resolve) => {
        release = resolve;
      });
      return select(snapshot);
    };
    const first = f.runtime.tick();
    while (!release) await new Promise((resolve) => setTimeout(resolve, 1));
    assert.equal(await f.runtime.tick(), false);
    release();
    await first;
    assert.equal(f.store.actions(id).length, 1);
  } finally {
    f.close();
  }
});
test("overdue wakeups are scheduled with a bounded delay instead of failing or spinning", async () => {
  const f = fixture();
  try {
    const id = f.store.create("demo");
    f.store.control(id, "resume");
    f.services.business.selectWork = async () => ({
      kind: "wait",
      reason: "Time passed during planning",
      wakeAt: new Date(Date.now() - 1000).toISOString(),
    });
    await f.runtime.tick();
    assert.equal(f.store.run(id)?.state, "waiting");
    assert.ok(f.store.run(id)!.next_wake_at! > Date.now());
  } finally {
    f.close();
  }
});
test("a receipt already stored before settlement timeout is not executed again", async () => {
  const f = fixture();
  let release!: () => void;
  try {
    Object.assign(f.config, { dependencyMs: 20 });
    const id = f.store.create("demo");
    f.store.control(id, "resume");
    let executions = 0;
    const execute = f.services.tools.execute;
    f.services.tools.execute = async (...args) => {
      executions++;
      return execute(...args);
    };
    f.services.economy.settle = async () => {
      await new Promise<void>((resolve) => {
        release = resolve;
      });
      return { kind: "settled" };
    };
    await f.runtime.tick();
    assert.equal(f.store.run(id)?.state, "waiting");
    assert.ok(f.store.actions(id)[0]?.receipt_json);
    release();
    f.services.economy.settle = async () => ({ kind: "settled" });
    f.store.control(id, "resume");
    await new AgentRuntime(f.store, f.services, f.config).tick();
    assert.equal(executions, 1);
    assert.equal(f.store.actions(id)[0]?.state, "completed");
  } finally {
    f.close();
  }
});

test("a backlog larger than one snapshot is drained without stranding unseen events", async () => {
  const f = fixture();
  try {
    const id = f.store.create("demo");
    f.store.control(id, "resume");
    for (let i = 0; i < 105; i++) f.store.deliverEvent(id, event(`batch-${i}`));
    let inspections = 0;
    f.services.business.selectWork = async (snapshot) => {
      inspections++;
      assert.ok(snapshot.events.length <= 100);
      return {
        kind: "wait",
        reason: "Batch processed",
        acknowledgeEventIds: snapshot.events.map((item) => item.eventId),
      };
    };
    await f.runtime.tick();
    assert.equal(f.store.pendingEvents(id).length, 5);
    assert.equal(await f.runtime.tick(), true);
    assert.equal(f.store.pendingEvents(id).length, 0);
    assert.equal(await f.runtime.tick(), false);
    assert.equal(inspections, 2);
  } finally {
    f.close();
  }
});
