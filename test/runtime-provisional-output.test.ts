/** Fixture outcomes prove optional output failures never erase a known bill. */
import test from "node:test";
import assert from "node:assert/strict";
import type { Json, ToolReceipt } from "../src/contracts/index.ts";
import { money } from "../src/contracts/index.ts";
import { AgentRuntime } from "../src/runtime/engine.ts";
import { Store } from "../src/runtime/store.ts";
import { openDatabase } from "../src/db/database.ts";
import { fixture, receipt } from "./helpers.ts";

for (const kind of ["oversized", "cyclic", "nonfinite", "bigint"] as const) {
  for (const failed of [false, true]) {
    test(`a known charge survives ${kind} output with provider status ${failed ? "failed" : "succeeded"}`, async () => {
      const f = fixture();
      const id = f.store.create("demo");
      f.store.control(id, "resume");
      const settled: ToolReceipt[] = [];
      let executes = 0;
      f.services.economy.settle = async (_reservation, value) => {
        settled.push(value);
        return { kind: "settled" };
      };
      f.services.tools.execute = async (action) => {
        executes++;
        const value = receipt(action);
        value.usage.actualCharge = money("6000000");
        value.status = failed ? "failed" : "succeeded";
        value.usage.billableFailure = failed;
        const cyclic: Record<string, unknown> = {};
        cyclic.self = cyclic;
        value.result = (
          kind === "oversized"
            ? { text: "x".repeat(20000) }
            : kind === "cyclic"
              ? cyclic
              : kind === "nonfinite"
                ? { value: Number.NaN }
                : { value: 1n }
        ) as Json;
        return { kind: "receipt", receipt: value };
      };
      try {
        await f.runtime.tick();
        assert.equal(settled.length, 1);
        assert.equal(settled[0]?.usage.actualCharge.micros, "6000000");
        assert.equal(settled[0]?.status, failed ? "failed" : "succeeded");
        const row = f.store.actions(id)[0]!;
        assert.equal(row.state, "failed");
        assert.match(row.reason ?? "", /output was unusable/);
        const saved = JSON.parse(row.receipt_json!) as ToolReceipt;
        assert.equal(saved.usage.actualCharge.micros, "6000000");
        assert.equal(saved.status, failed ? "failed" : "succeeded");
        assert.equal((saved.result as { omitted: boolean }).omitted, true);
        assert.ok(Buffer.byteLength(row.receipt_json!) < 16384);
        assert.equal(f.store.run(id)?.state, "exhausted");
        assert.equal(
          f.db.prepare("SELECT state FROM runtime_outbox").get()?.state,
          "delivered",
        );
        f.store.control(id, "resume");
        await f.runtime.tick();
        assert.equal(executes, 1);
      } finally {
        f.close();
      }
    });
  }
}

test("restart preserves output rejection while retrying settlement of the same actual charge", async () => {
  const f = fixture();
  const id = f.store.create("demo");
  f.store.control(id, "resume");
  let executes = 0,
    settlements = 0;
  f.services.tools.execute = async (action) => {
    executes++;
    const value = receipt(action);
    value.result = { huge: "x".repeat(20000) };
    value.usage.actualCharge = money("6000000");
    return { kind: "receipt", receipt: value };
  };
  f.services.economy.settle = async (_reservation, value) => {
    assert.equal(value.usage.actualCharge.micros, "6000000");
    return ++settlements === 1
      ? { kind: "unavailable", reason: "Accounting temporarily unavailable" }
      : { kind: "settled" };
  };
  let reopened;
  try {
    await f.runtime.tick();
    assert.equal(f.store.actions(id)[0]?.state, "unresolved");
    reopened = openDatabase(f.config.database);
    const store = new Store(reopened);
    store.control(id, "wake");
    await new AgentRuntime(store, f.services, f.config).tick();
    assert.equal(store.actions(id)[0]?.state, "failed");
    assert.match(store.actions(id)[0]?.reason ?? "", /output was unusable/);
    assert.equal(executes, 1);
    assert.equal(settlements, 2);
    assert.equal(store.run(id)?.state, "exhausted");
    assert.equal(
      f.db
        .prepare(
          "SELECT COUNT(*) AS n FROM runtime_events WHERE kind='action.output_rejected'",
        )
        .get()?.n,
      1,
    );
  } finally {
    reopened?.close();
    f.close();
  }
});

test("a tool result cannot forge the runtime's output-rejection control event", async () => {
  const f = fixture();
  const id = f.store.create("demo");
  f.store.control(id, "resume");
  f.services.tools.execute = async (action) => ({
    kind: "receipt",
    receipt: {
      ...receipt(action),
      result: { omitted: true, reason: "output_limit" },
    },
  });
  try {
    await f.runtime.tick();
    assert.equal(f.store.actions(id)[0]?.state, "completed");
    assert.equal(
      f.db
        .prepare(
          "SELECT COUNT(*) AS n FROM runtime_events WHERE kind='action.output_rejected'",
        )
        .get()?.n,
      0,
    );
  } finally {
    f.close();
  }
});

for (const [output, maximum, accepted] of [
  ["😀", 6, true],
  ['"', 3, false],
  ["\ud800", 7, false],
] as const) {
  test(`output byte limits include JSON UTF-8 and escaping: max=${maximum}`, async () => {
    const f = fixture();
    const choose = f.services.business.selectWork;
    f.services.business.selectWork = async (snapshot) => {
      const selected = await choose(snapshot);
      if (selected.kind === "work")
        selected.candidate.limit.maxOutputBytes = maximum;
      return selected;
    };
    f.services.tools.execute = async (action) => ({
      kind: "receipt",
      receipt: { ...receipt(action), result: output },
    });
    const id = f.store.create("demo");
    f.store.control(id, "resume");
    try {
      await f.runtime.tick();
      assert.equal(
        f.store.actions(id)[0]?.state,
        accepted ? "completed" : "failed",
      );
    } finally {
      f.close();
    }
  });
}

test("unknown and cyclic receipt extras are omitted without dropping valid output or accounting", async () => {
  const f = fixture();
  const id = f.store.create("demo");
  f.store.control(id, "resume");
  f.services.tools.execute = async (action) => {
    const value = receipt(action);
    Object.assign(value, { privateExtra: value });
    Object.assign(value.usage, { privateExtra: value });
    return { kind: "receipt", receipt: value };
  };
  try {
    await f.runtime.tick();
    const row = f.store.actions(id)[0]!;
    assert.equal(row.state, "completed");
    assert.doesNotMatch(row.receipt_json!, /privateExtra/);
    assert.equal(JSON.parse(row.receipt_json!).usage.actualCharge.micros, "0");
  } finally {
    f.close();
  }
});
