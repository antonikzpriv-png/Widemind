import test from "node:test";
import assert from "node:assert/strict";
import { Script } from "node:vm";
import { fixture, receipt } from "./helpers.ts";
import { commerceScript } from "../src/web/commerce.ts";

test("public commerce script parses without inline execution or external dependencies", () => {
  assert.doesNotThrow(() => new Script(commerceScript));
  assert.match(commerceScript, /sandbox\.paypal\.com/);
  assert.doesNotMatch(commerceScript, /checkout\.stripe/);
});
test("provisional usage is reconciled before final settlement without re-execution", async () => {
  const f = fixture();
  try {
    const id = f.store.create("demo");
    f.store.control(id, "resume");
    let executions = 0,
      settlements = 0,
      reconciliations = 0;
    f.services.tools.execute = async (action) => {
      executions++;
      const r = receipt(action);
      r.usage.provisional = true;
      return { kind: "receipt", receipt: r };
    };
    f.services.economy.settle = async (_reservation, r) => {
      settlements++;
      return r.usage.provisional
        ? { kind: "unavailable", reason: "Actual cost awaiting final record" }
        : { kind: "settled" };
    };
    await f.runtime.tick();
    assert.equal(f.store.actions(id)[0]?.state, "unresolved");
    f.services.tools.reconcile = async (action) => {
      reconciliations++;
      return { kind: "receipt", receipt: receipt(action) };
    };
    f.store.control(id, "resume");
    await f.runtime.tick();
    assert.equal(executions, 1);
    assert.equal(reconciliations, 1);
    assert.equal(settlements, 2);
    assert.equal(f.store.actions(id)[0]?.state, "completed");
  } finally {
    f.close();
  }
});
test("recovered queued work refreshes actual capabilities before dispatch", async () => {
  const f = fixture();
  try {
    const id = f.store.create("demo");
    f.store.control(id, "resume");
    f.services.economy.authorize = async () => ({
      kind: "unavailable",
      reason: "Ledger temporarily offline",
    });
    await f.runtime.tick();
    const original = f.store.actions(id)[0]!;
    assert.equal(original.state, "planned");
    let ready = false;
    f.services.tools.capabilities = () =>
      ready
        ? [{ id: "demo.write-note", availability: { available: true } }]
        : [];
    f.services.tools.refresh = async () => {
      ready = true;
    };
    const { definition } = await import("../src/modules/demo/module.ts");
    f.services.economy = definition.create({
      db: f.db,
      config: f.config,
    }).economy!;
    f.store.control(id, "resume");
    await f.runtime.tick();
    assert.equal(f.store.actions(id)[0]?.id, original.id);
    assert.equal(f.store.actions(id)[0]?.state, "completed");
  } finally {
    f.close();
  }
});
