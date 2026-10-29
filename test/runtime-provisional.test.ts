/** Synthetic tool receipts exercise recovery; no paid provider is invoked. */
import test from "node:test";
import assert from "node:assert/strict";
import type { ToolAction, ToolReceipt } from "../src/contracts/index.ts";
import { money } from "../src/contracts/index.ts";
import { AgentRuntime } from "../src/runtime/engine.ts";
import { Store } from "../src/runtime/store.ts";
import { openDatabase } from "../src/db/database.ts";
import { fixture, receipt } from "./helpers.ts";

function provisionalFixture(cap = "1000000", acknowledgePartial = false) {
  const f = fixture();
  const selected = f.services.business.selectWork;
  f.services.business.selectWork = async (snapshot) => {
    const selection = await selected(snapshot);
    if (selection.kind === "work")
      selection.candidate.limit.maximum = money(cap);
    return selection;
  };
  f.services.economy.authorize = async ({ runId, actionId, limit }) => ({
    kind: "authorized",
    reservation: {
      runId,
      actionId,
      limit,
      reservationId: `fixture:${actionId}`,
      authorization: "fixture-private-permit",
    },
  });
  const settled: ToolReceipt[] = [];
  f.services.economy.settle = async (_reservation, value) => {
    settled.push(structuredClone(value));
    return value.usage.provisional && !acknowledgePartial
      ? { kind: "unavailable", reason: "Provider cost is not final" }
      : { kind: "settled" };
  };
  const runId = f.store.create("demo");
  f.store.control(runId, "resume");
  return { ...f, runId, settled };
}

function usageReceipt(
  action: ToolAction,
  amount: string,
  provisional: boolean,
  failed = false,
) {
  const value = receipt(action);
  value.usage.actualCharge = money(amount);
  value.usage.provisional = provisional;
  value.usage.externalEventId = `fixture-charge:${action.actionId}`;
  value.status = failed ? "failed" : "succeeded";
  value.usage.billableFailure = failed;
  return value;
}

for (const acknowledgePartial of [false, true]) {
  for (const failed of [false, true]) {
    test(`provisional ${failed ? "failed" : "successful"} usage stays recoverable even with ledger acknowledgement=${acknowledgePartial}`, async () => {
      const f = provisionalFixture("1000000", acknowledgePartial);
      let executions = 0;
      f.services.tools.execute = async (action) => {
        executions++;
        return {
          kind: "receipt",
          receipt: usageReceipt(action, "200000", true, failed),
        };
      };
      try {
        await f.runtime.tick();
        const original = f.store.actions(f.runId)[0]!;
        assert.equal(original.state, "unresolved");
        assert.equal(
          JSON.parse(original.receipt_json!).usage.provisional,
          true,
        );
        assert.equal(
          f.db.prepare("SELECT state FROM runtime_outbox").get()?.state,
          "unresolved",
        );
        assert.deepEqual(
          f.settled.map((r) => r.usage.actualCharge.micros),
          ["200000"],
        );
        let observations = 0;
        f.services.tools.reconcile = async (action) => {
          observations++;
          return {
            kind: "receipt",
            receipt: usageReceipt(action, "400000", false, failed),
          };
        };
        f.store.control(f.runId, "wake");
        await f.runtime.tick();
        const final = f.store.actions(f.runId)[0]!;
        assert.equal(final.id, original.id);
        assert.equal(final.state, failed ? "failed" : "completed");
        assert.equal(JSON.parse(final.receipt_json!).usage.provisional, false);
        assert.equal(
          f.db.prepare("SELECT state FROM runtime_outbox").get()?.state,
          "delivered",
        );
        assert.equal(executions, 1);
        assert.equal(observations, 1);
        assert.deepEqual(
          f.settled.map((r) => r.usage.actualCharge.micros),
          ["200000", "400000"],
        );
      } finally {
        f.close();
      }
    });
  }
}

test("multiple provisional observations update stored usage before the final total", async () => {
  const f = provisionalFixture();
  let executions = 0,
    observations = 0;
  f.services.tools.execute = async (action) => {
    executions++;
    return { kind: "receipt", receipt: usageReceipt(action, "100000", true) };
  };
  f.services.tools.reconcile = async (action) => {
    observations++;
    return {
      kind: "receipt",
      receipt: usageReceipt(
        action,
        observations === 1 ? "200000" : "300000",
        observations === 1,
      ),
    };
  };
  try {
    await f.runtime.tick();
    for (let i = 0; i < 2; i++) {
      f.store.control(f.runId, "wake");
      await f.runtime.tick();
      assert.equal(
        f.store.actions(f.runId)[0]?.state,
        i === 0 ? "unresolved" : "completed",
      );
    }
    assert.equal(executions, 1);
    assert.equal(observations, 2);
    assert.deepEqual(
      f.settled.map((r) => r.usage.actualCharge.micros),
      ["100000", "200000", "300000"],
    );
  } finally {
    f.close();
  }
});

test("restart reconciles a cached provisional receipt without repeating its effect", async () => {
  const f = provisionalFixture();
  let executions = 0;
  f.services.tools.execute = async (action) => {
    executions++;
    return { kind: "receipt", receipt: usageReceipt(action, "100000", true) };
  };
  let reopened;
  try {
    await f.runtime.tick();
    const original = f.store.actions(f.runId)[0]!;
    reopened = openDatabase(f.config.database);
    const store = new Store(reopened);
    f.services.tools.reconcile = async (action) => ({
      kind: "receipt",
      receipt: usageReceipt(action, "150000", false),
    });
    store.control(f.runId, "wake");
    await new AgentRuntime(store, f.services, f.config).tick();
    assert.equal(store.actions(f.runId)[0]?.id, original.id);
    assert.equal(store.actions(f.runId)[0]?.state, "completed");
    assert.equal(executions, 1);
    assert.deepEqual(
      f.settled.map((r) => r.usage.actualCharge.micros),
      ["100000", "150000"],
    );
  } finally {
    reopened?.close();
    f.close();
  }
});

for (const kind of ["unknown", "unavailable", "not_performed"] as const) {
  test(`a cached provisional receipt cannot be erased or replayed by ${kind}`, async () => {
    const f = provisionalFixture();
    let executions = 0;
    f.services.tools.execute = async (action) => {
      executions++;
      return { kind: "receipt", receipt: usageReceipt(action, "100000", true) };
    };
    f.services.tools.reconcile = async () =>
      kind === "not_performed"
        ? {
            kind: "not_performed",
            evidenceRef: "fixture-no-effect-without-final-cost",
          }
        : { kind, reason: "Final usage is still unavailable" };
    try {
      await f.runtime.tick();
      const saved = f.store.actions(f.runId)[0]!.receipt_json;
      f.store.control(f.runId, "wake");
      await f.runtime.tick();
      const row = f.store.actions(f.runId)[0]!;
      assert.equal(row.state, "unresolved");
      assert.equal(row.receipt_json, saved);
      assert.equal(row.attempts, 1);
      assert.equal(executions, 1);
      assert.equal(f.settled.length, 1);
    } finally {
      f.close();
    }
  });
}

test("a provisional overage remains exhausted until authoritative final reconciliation", async () => {
  const f = provisionalFixture();
  f.services.tools.execute = async (action) => ({
    kind: "receipt",
    receipt: usageReceipt(action, "2000000", true),
  });
  try {
    await f.runtime.tick();
    assert.equal(f.store.run(f.runId)?.state, "exhausted");
    assert.equal(f.store.actions(f.runId)[0]?.state, "unresolved");
    f.services.tools.reconcile = async () => ({
      kind: "unknown",
      reason: "Usage pending",
    });
    f.store.control(f.runId, "wake");
    await f.runtime.tick();
    assert.equal(f.store.run(f.runId)?.state, "exhausted");
    f.services.tools.reconcile = async (action) => ({
      kind: "receipt",
      receipt: usageReceipt(action, "500000", false),
    });
    f.store.control(f.runId, "wake");
    await f.runtime.tick();
    assert.equal(f.store.run(f.runId)?.state, "active");
    assert.equal(f.store.actions(f.runId)[0]?.state, "completed");
  } finally {
    f.close();
  }
});

test("an unexpected charge on a zero-cost authorization is recorded and exhausts the runtime", async () => {
  const f = provisionalFixture("0");
  f.services.tools.execute = async (action) => ({
    kind: "receipt",
    receipt: usageReceipt(action, "1", false),
  });
  try {
    await f.runtime.tick();
    assert.equal(f.settled[0]?.usage.actualCharge.micros, "1");
    assert.equal(f.store.actions(f.runId)[0]?.state, "completed");
    assert.equal(f.store.run(f.runId)?.state, "exhausted");
  } finally {
    f.close();
  }
});
