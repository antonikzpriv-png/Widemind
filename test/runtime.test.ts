import test from "node:test";
import assert from "node:assert/strict";
import { openDatabase } from "../src/db/database.ts";
import { Store } from "../src/runtime/store.ts";
import { AgentRuntime } from "../src/runtime/engine.ts";
import { fixture, receipt, unavailableServices } from "./helpers.ts";

test("demo executes through reservation and outbox, then waits without repeat selection", async () => {
  const f = fixture();
  try {
    const id = f.store.create("demo");
    f.store.control(id, "resume");
    let selectionCalls = 0;
    const select = f.services.business.selectWork;
    f.services.business.selectWork = async (state) => {
      selectionCalls++;
      return select(state);
    };
    assert.equal(await f.runtime.tick(), true);
    const action = f.store.actions(id)[0]!;
    assert.equal(action.state, "completed");
    assert.equal(action.attempts, 1);
    assert.equal(
      f.db.prepare("SELECT state FROM runtime_outbox").get()?.state,
      "delivered",
    );
    assert.equal(await f.runtime.tick(), true);
    assert.equal(f.store.run(id)?.state, "waiting");
    assert.equal(f.store.run(id)?.next_wake_at, null);
    for (let i = 0; i < 5; i++) assert.equal(await f.runtime.tick(), false);
    assert.equal(selectionCalls, 2);
    assert.equal(await f.services.economy.summary(id), null);
  } finally {
    f.close();
  }
});
test("unavailable dependencies cannot execute or invent starting credit", async () => {
  const f = fixture("foundation");
  try {
    const id = f.store.create("foundation");
    f.store.control(id, "resume");
    await f.runtime.tick();
    assert.equal(f.store.run(id)?.state, "waiting");
    assert.match(f.store.run(id)!.reason, /unavailable/i);
    assert.equal(f.store.actions(id).length, 0);
    assert.equal(await f.services.economy.summary(id), null);
    assert.equal(f.services.tools.health().available, false);
  } finally {
    f.close();
  }
});
test("missing economy and exhausted authorization both prevent tool execution", async () => {
  for (const kind of ["unavailable", "insufficient"] as const) {
    const f = fixture();
    try {
      const id = f.store.create("demo");
      f.store.control(id, "resume");
      let executed = 0;
      f.services.economy.authorize = async () => ({
        kind,
        reason: "fixture authorization denied",
      });
      f.services.tools.execute = async () => {
        executed++;
        throw new Error("Must not execute");
      };
      await f.runtime.tick();
      assert.equal(executed, 0);
      assert.equal(
        f.store.run(id)?.state,
        kind === "insufficient" ? "exhausted" : "waiting",
      );
      assert.equal(f.store.actions(id)[0]?.state, "planned");
    } finally {
      f.close();
    }
  }
});
test("pause persists across database reopen and prevents all selection/execution", async () => {
  const f = fixture();
  let reopened;
  try {
    const id = f.store.create("demo");
    f.store.control(id, "pause");
    await f.runtime.tick();
    reopened = openDatabase(f.config.database);
    const second = new Store(reopened);
    assert.equal(second.run(id)?.state, "paused");
    f.services.business.selectWork = async () => {
      throw new Error("Paused selection forbidden");
    };
    const runtime = new AgentRuntime(second, f.services, f.config);
    assert.equal(await runtime.tick(), false);
    second.control(id, "resume");
    assert.equal(second.run(id)?.state, "active");
  } finally {
    reopened?.close();
    f.close();
  }
});
test("separate database connections cannot claim one lease; expiry fences stale owner", () => {
  const f = fixture();
  const second = openDatabase(f.config.database);
  try {
    const id = f.store.create("demo");
    f.store.control(id, "resume");
    const now = Date.now();
    const first = f.store.acquire("one", now, 3000)!;
    assert.ok(first);
    const store = new Store(second);
    assert.equal(store.acquire("two", now, 3000), null);
    f.db.prepare("UPDATE runtime_jobs SET lease_until=?").run(now - 1);
    const next = store.acquire("two", now, 3000)!;
    assert.ok(next.fence > first.fence);
    assert.equal(f.store.renew(first, now, 3000), false);
    assert.throws(() => f.store.fenced(first, () => {}), /Lease lost/);
    assert.equal(next.runId, id);
  } finally {
    second.close();
    f.close();
  }
});
test("interrupted external action is reconciled on restart before any duplicate execution", async () => {
  const f = fixture();
  const second = openDatabase(f.config.database);
  try {
    const id = f.store.create("demo");
    f.store.control(id, "resume");
    let executions = 0,
      reconciliations = 0;
    f.services.tools.execute = async () => {
      executions++;
      return {
        kind: "unknown",
        reason: "Response lost after provider accepted",
      };
    };
    await f.runtime.tick();
    const original = f.store.actions(id)[0]!;
    assert.equal(original.state, "unresolved");
    const order: string[] = [];
    f.services.tools.reconcile = async (action) => {
      order.push("reconcile");
      reconciliations++;
      return { kind: "receipt", receipt: receipt(action) };
    };
    const store = new Store(second);
    store.control(id, "resume");
    await new AgentRuntime(store, f.services, f.config).tick();
    assert.equal(executions, 1);
    assert.equal(reconciliations, 1);
    assert.deepEqual(order, ["reconcile"]);
    assert.equal(store.actions(id)[0]?.id, original.id);
    assert.equal(store.actions(id)[0]?.state, "completed");
  } finally {
    second.close();
    f.close();
  }
});
test("confirmed not-performed permits bounded retry with the same durable id and reauthorization", async () => {
  const f = fixture();
  try {
    const id = f.store.create("demo");
    f.store.control(id, "resume");
    const ids: string[] = [];
    let authorizations = 0;
    const authorize = f.services.economy.authorize;
    f.services.economy.authorize = async (req) => {
      authorizations++;
      return authorize(req);
    };
    f.services.tools.execute = async (action) => {
      ids.push(action.actionId);
      return ids.length === 1
        ? { kind: "unknown", reason: "Lost response" }
        : { kind: "receipt", receipt: receipt(action) };
    };
    await f.runtime.tick();
    f.store.control(id, "resume");
    await f.runtime.tick();
    assert.equal(ids.length, 2);
    assert.equal(ids[0], ids[1]);
    assert.equal(authorizations, 2);
    assert.equal(f.store.actions(id)[0]?.state, "completed");
  } finally {
    f.close();
  }
});
test("settlement interruption retries settlement only, including billable failures", async () => {
  const f = fixture();
  try {
    const id = f.store.create("demo");
    f.store.control(id, "resume");
    let executes = 0,
      settles = 0;
    f.services.tools.execute = async (action) => {
      executes++;
      const r = receipt(action);
      r.status = "failed";
      r.usage.billableFailure = true;
      return { kind: "receipt", receipt: r };
    };
    f.services.economy.settle = async () =>
      ++settles === 1
        ? { kind: "unavailable", reason: "Ledger offline" }
        : { kind: "settled" };
    await f.runtime.tick();
    assert.ok(f.store.actions(id)[0]?.receipt_json);
    f.store.control(id, "resume");
    await f.runtime.tick();
    assert.equal(executes, 1);
    assert.equal(settles, 2);
    assert.equal(f.store.actions(id)[0]?.state, "failed");
  } finally {
    f.close();
  }
});
test("pause during selection wins; no tool starts after the operator intervenes", async () => {
  const f = fixture();
  try {
    const id = f.store.create("demo");
    f.store.control(id, "resume");
    const select = f.services.business.selectWork;
    f.services.business.selectWork = async (state) => {
      const choice = await select(state);
      f.store.control(id, "pause");
      return choice;
    };
    await f.runtime.tick();
    assert.equal(f.store.actions(id).length, 0);
    assert.equal(f.store.run(id)?.state, "paused");
  } finally {
    f.close();
  }
});
test("stop cancels supported active execution and keeps uncertain effects reconcilable", async () => {
  const f = fixture();
  try {
    const id = f.store.create("demo");
    f.store.control(id, "resume");
    let aborted = false;
    f.services.tools.execute = async (_action, _reservation, signal) =>
      new Promise((resolve) => {
        f.store.control(id, "stop");
        signal.addEventListener(
          "abort",
          () => {
            aborted = true;
            resolve({
              kind: "unknown",
              reason: "Cancelled; provider outcome unknown",
            });
          },
          { once: true },
        );
      });
    await f.runtime.tick();
    assert.equal(aborted, true);
    assert.equal(f.store.run(id)?.state, "stopped");
    assert.equal(f.store.actions(id)[0]?.state, "unresolved");
    f.services.tools.reconcile = async (action) => ({
      kind: "receipt",
      receipt: receipt(action),
    });
    // Reconciliation remains available even while stopped.
    await f.runtime.tick();
    assert.equal(f.store.actions(id)[0]?.state, "completed");
    assert.equal(f.store.run(id)?.state, "stopped");
  } finally {
    f.close();
  }
});
test("planned work and scheduled wakeups survive a worker process restart", async () => {
  const f = fixture();
  try {
    const id = f.store.create("demo");
    f.store.control(id, "resume");
    f.services.business.selectWork = async () => ({
      kind: "wait",
      reason: "Await reply",
      wakeAt: new Date(Date.now() + 60000).toISOString(),
    });
    await f.runtime.tick();
    assert.equal(f.store.run(id)?.state, "waiting");
    assert.ok(f.store.run(id)?.next_wake_at);
    const second = openDatabase(f.config.database);
    try {
      assert.equal(
        await new AgentRuntime(new Store(second), f.services, f.config).tick(),
        false,
      );
    } finally {
      second.close();
    }
  } finally {
    f.close();
  }
});
test("unexpected module errors fail durably without leaking exception text", async () => {
  const f = fixture();
  try {
    const id = f.store.create("demo");
    f.store.control(id, "resume");
    f.services.business.selectWork = async () => {
      throw new Error("secret-fixture-marker");
    };
    await f.runtime.tick();
    assert.equal(f.store.run(id)?.state, "failed");
    assert.doesNotMatch(f.store.run(id)!.reason, /secret-fixture-marker/);
  } finally {
    f.close();
  }
});

test("unknown outcomes never exceed three confirmed-not-performed execution attempts", async () => {
  const f = fixture();
  try {
    const id = f.store.create("demo");
    f.store.control(id, "resume");
    let executions = 0;
    f.services.tools.execute = async () => {
      executions++;
      return { kind: "unknown", reason: "Unknown outcome" };
    };
    for (let i = 0; i < 6; i++) {
      f.store.control(id, "resume");
      await f.runtime.tick();
    }
    assert.equal(executions, 3);
    assert.equal(f.store.actions(id)[0]?.state, "unresolved");
    assert.equal(f.store.actions(id)[0]?.attempts, 3);
  } finally {
    f.close();
  }
});
test("over-limit usage is settled as liability before exhaustion, never dropped", async () => {
  const f = fixture();
  try {
    const id = f.store.create("demo");
    f.store.control(id, "resume");
    let settled: string | null = null;
    f.services.tools.execute = async (action) => {
      const r = receipt(action);
      r.usage.actualCharge.micros = "10";
      return { kind: "receipt", receipt: r };
    };
    f.services.economy.settle = async (_reservation, r) => {
      settled = r.usage.actualCharge.micros;
      return { kind: "settled" };
    };
    await f.runtime.tick();
    assert.equal(settled, "10");
    assert.equal(f.store.run(id)?.state, "exhausted");
    assert.equal(f.store.actions(id)[0]?.state, "completed");
    f.db.prepare("UPDATE runtime_jobs SET available_at=0").run();
    await f.runtime.tick();
    assert.equal(f.store.run(id)?.state, "exhausted");
  } finally {
    f.close();
  }
});
test("a malformed receipt cannot mark an external action completed", async () => {
  const f = fixture();
  try {
    const id = f.store.create("demo");
    f.store.control(id, "resume");
    f.services.tools.execute = async (action) => {
      const r = receipt(action);
      r.actionId = "wrong-action";
      return { kind: "receipt", receipt: r };
    };
    await f.runtime.tick();
    assert.equal(f.store.run(id)?.state, "failed");
    assert.equal(f.store.actions(id)[0]?.state, "unresolved");
    assert.equal(
      f.db.prepare("SELECT state FROM runtime_outbox").get()?.state,
      "unresolved",
    );
  } finally {
    f.close();
  }
});

test("authorization matches semantic limits independently of JSON property order", async () => {
  const f = fixture();
  try {
    const id = f.store.create("demo");
    f.store.control(id, "resume");
    const authorize = f.services.economy.authorize;
    f.services.economy.authorize = async (request) => {
      const result = await authorize(request);
      if (result.kind === "authorized") {
        const limit = result.reservation.limit;
        result.reservation.limit = {
          maxOutputBytes: limit.maxOutputBytes,
          maxDurationMs: limit.maxDurationMs,
          maximum: limit.maximum,
        };
      }
      return result;
    };
    await f.runtime.tick();
    assert.equal(f.store.actions(id)[0]?.state, "completed");
  } finally {
    f.close();
  }
});
