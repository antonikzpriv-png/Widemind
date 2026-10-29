import test from "node:test";
import assert from "node:assert/strict";
import { fixture, receipt } from "./helpers.ts";

for (const permitted of [false, true]) {
  test(`definitive no-effect recovery requires the configured accounting rearm: allowed=${permitted}`, async () => {
    const f = fixture();
    const id = f.store.create("demo");
    f.store.control(id, "resume");
    let executed = 0,
      confirmed = 0;
    f.services.tools.execute = async (action) =>
      ++executed === 1
        ? { kind: "unknown", reason: "Response lost after dispatch" }
        : { kind: "receipt", receipt: receipt(action) };
    f.services.tools.reconcile = async () => ({
      kind: "not_performed",
      evidenceRef: "fixture-definitive-no-effect",
    });
    f.services.economy.confirmNotPerformed = async (reservation, evidence) => {
      confirmed++;
      assert.equal(reservation.runId, id);
      assert.equal(evidence, "fixture-definitive-no-effect");
      assert.equal(f.store.actions(id)[0]?.state, "unresolved");
      return permitted
        ? { kind: "renewed" }
        : {
            kind: "unavailable",
            reason: "Backend cannot authorize another dispatch",
          };
    };
    try {
      await f.runtime.tick();
      const rid = JSON.parse(
        f.store.actions(id)[0]!.reservation_json!,
      ).reservationId;
      f.store.control(id, "wake");
      await f.runtime.tick();
      assert.equal(confirmed, 1);
      assert.equal(executed, permitted ? 2 : 1);
      assert.equal(
        f.store.actions(id)[0]?.state,
        permitted ? "completed" : "unresolved",
      );
      assert.equal(
        JSON.parse(f.store.actions(id)[0]!.reservation_json!).reservationId,
        rid,
      );
      assert.equal(
        f.db.prepare("SELECT state FROM runtime_outbox").get()?.state,
        permitted ? "delivered" : "unresolved",
      );
    } finally {
      f.close();
    }
  });
}

test("an accounting rearm timeout never permits another execute attempt", async () => {
  const f = fixture();
  Object.assign(f.config, { dependencyMs: 20 });
  const id = f.store.create("demo");
  f.store.control(id, "resume");
  let executed = 0;
  f.services.tools.execute = async () => {
    executed++;
    return { kind: "unknown", reason: "Unknown provider result" };
  };
  f.services.tools.reconcile = async () => ({
    kind: "not_performed",
    evidenceRef: "fixture-no-effect",
  });
  f.services.economy.confirmNotPerformed = async () => new Promise(() => {});
  try {
    await f.runtime.tick();
    f.store.control(id, "wake");
    await f.runtime.tick();
    assert.equal(executed, 1);
    assert.equal(f.store.actions(id)[0]?.attempts, 1);
    assert.equal(f.store.actions(id)[0]?.state, "unresolved");
    assert.equal(f.store.run(id)?.state, "waiting");
  } finally {
    f.close();
  }
});

test("the third definitive no-effect proof closes accounting and the outbox without a fourth attempt", async () => {
  const f = fixture();
  const id = f.store.create("demo");
  f.store.control(id, "resume");
  let executed = 0,
    confirmed = 0;
  f.services.tools.execute = async () => {
    executed++;
    return { kind: "unknown", reason: "Provider response unavailable" };
  };
  f.services.tools.reconcile = async () => ({
    kind: "not_performed",
    evidenceRef: `fixture-no-effect-${executed}`,
  });
  f.services.economy.confirmNotPerformed = async () => {
    confirmed++;
    return executed >= 3 ? { kind: "closed" } : { kind: "renewed" };
  };
  try {
    await f.runtime.tick();
    for (let i = 0; i < 3; i++) {
      f.store.control(id, "wake");
      await f.runtime.tick();
    }
    assert.equal(executed, 3);
    assert.equal(confirmed, 3);
    assert.equal(f.store.actions(id)[0]?.state, "failed");
    assert.equal(f.store.actions(id)[0]?.receipt_json, null);
    assert.match(f.store.actions(id)[0]?.reason ?? "", /Confirmed no effect/);
    assert.equal(
      f.db.prepare("SELECT state FROM runtime_outbox").get()?.state,
      "delivered",
    );
    f.store.control(id, "wake");
    await f.runtime.tick();
    assert.equal(executed, 3);
  } finally {
    f.close();
  }
});
