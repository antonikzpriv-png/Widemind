import test from "node:test";
import assert from "node:assert/strict";
import type { AddressInfo } from "node:net";
import type { FinancialSummary } from "../src/contracts/index.ts";
import {
  listSnapshots,
  publishedPilot,
  retractSnapshot,
  takeSnapshot,
  verifyChain,
} from "../src/runtime/pilot-snapshots.ts";
import { webServer } from "../src/web/server.ts";
import { fixture } from "./helpers.ts";

const summary = (spendableMicros: string): FinancialSummary => ({
  currency: "USD",
  openingCreditMicros: "5000000",
  postedMicros: spendableMicros,
  reservedMicros: "0",
  holdsMicros: "0",
  spendableMicros,
  customerReceiptsMicros: "0",
  earnedRevenueMicros: "0",
  operatingChargesMicros: "0",
  ownerFundingMicros: "0",
  subsidiesMicros: "0",
  mode: "live",
  asOf: "2026-11-04T12:00:00Z",
});

test("snapshots chain by hash, stay append-only and only expose published ones", async () => {
  const f = fixture();
  try {
    const id = f.store.create("demo");
    let spendable = "4100000";
    f.services.economy.health = () => ({ available: true });
    f.services.economy.summary = async () => summary(spendable);
    const first = await takeSnapshot({
      db: f.db,
      services: f.services,
      runId: id,
      publish: false,
      now: 1000,
    });
    spendable = "4200000";
    const second = await takeSnapshot({
      db: f.db,
      services: f.services,
      runId: id,
      publish: true,
      now: 2000,
    });
    spendable = "4300000";
    const third = await takeSnapshot({
      db: f.db,
      services: f.services,
      runId: id,
      publish: true,
      now: 3000,
    });
    assert.notEqual(first.hash, second.hash);
    assert.deepEqual(verifyChain(f.db, id), {
      ok: true,
      snapshots: 3,
      brokenAt: null,
    });
    assert.equal(listSnapshots(f.db, id).length, 3);

    let published = publishedPilot(f.db, id);
    assert.equal(published.timeline.length, 2);
    assert.equal(published.latest?.outcomes?.spendableMicros, "4300000");
    assert.equal(published.latest?.snapshot.hash, third.hash);
    assert.equal(JSON.stringify(published).includes("4100000"), false);
    assert.equal(JSON.stringify(published).includes(id), false);

    const retracted = retractSnapshot(f.db, id, third.id);
    assert.equal(retracted.published, false);
    assert.ok(retracted.retractedAt);
    assert.throws(
      () => retractSnapshot(f.db, id, third.id),
      /not currently published/,
    );
    assert.throws(
      () => retractSnapshot(f.db, id, first.id),
      /not currently published/,
    );
    published = publishedPilot(f.db, id);
    assert.equal(published.latest?.outcomes?.spendableMicros, "4200000");
    assert.equal(verifyChain(f.db, id).ok, true);

    for (const sql of [
      "DELETE FROM pilot_snapshots",
      "UPDATE pilot_snapshots SET report_json='{}'",
      "UPDATE pilot_snapshots SET published=1,retracted_at=NULL WHERE id=" +
        third.id,
      "UPDATE pilot_snapshots SET published=1 WHERE id=" + first.id,
    ])
      assert.throws(() => f.db.exec(sql), /append-only/, sql);

    // Tampering that bypasses the guard is still detected by the chain.
    f.db.exec("DROP TRIGGER pilot_snapshots_retract_only");
    f.db.exec(
      `UPDATE pilot_snapshots SET report_json=replace(report_json,'4200000','9900000') WHERE id=${second.id}`,
    );
    assert.deepEqual(verifyChain(f.db, id), {
      ok: false,
      snapshots: 3,
      brokenAt: second.id,
    });
    assert.equal(publishedPilot(f.db, id).chain.ok, false);
  } finally {
    f.close();
  }
});

test("the public pilot page shows only published snapshots and no private identities", async () => {
  const f = fixture();
  const id = f.store.create("demo");
  f.config.publicRunId = id;
  const server = webServer({ ...f, secrets: { operatorPasswordHash: null } });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const origin = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  f.config.publicOrigin = origin;
  try {
    let page = await (await fetch(origin + "/pilot")).text();
    assert.match(page, /No results published yet/);
    assert.deepEqual(await (await fetch(origin + "/api/pilot")).json(), {
      latest: null,
      timeline: [],
      chain: { ok: true, publishedSnapshots: 0 },
    });

    let spendable = "6543210";
    f.services.economy.health = () => ({ available: true });
    f.services.economy.summary = async () => summary(spendable);
    await takeSnapshot({
      db: f.db,
      services: f.services,
      runId: id,
      publish: true,
    });
    spendable = "7777777";
    await takeSnapshot({
      db: f.db,
      services: f.services,
      runId: id,
      publish: false,
    });

    const response = await fetch(origin + "/pilot");
    assert.equal(response.status, 200);
    assert.match(
      response.headers.get("content-security-policy") ?? "",
      /default-src 'none'/,
    );
    page = await response.text();
    assert.match(page, /\$6\.54321/);
    assert.match(
      page,
      /Awaiting independence attestation|Milestone not yet met/,
    );
    assert.equal(page.includes("$7.777777"), false);
    assert.equal(page.includes(id), false);
    const api = await (await fetch(origin + "/api/pilot")).text();
    assert.equal(api.includes(id), false);
    assert.equal(api.includes("7777777"), false);
    assert.match(await (await fetch(origin + "/")).text(), /href="\/pilot"/);
  } finally {
    await new Promise<void>((resolve) => server.close(() => resolve()));
    f.close();
  }
});
