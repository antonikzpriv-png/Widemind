import test from "node:test";
import assert from "node:assert/strict";
import type { AddressInfo } from "node:net";
import { webServer } from "../src/web/server.ts";
import { hashPassword } from "../src/auth/password.ts";
import {
  latestAttestation,
  outsideLedgerCosts,
} from "../src/runtime/pilot-records.ts";
import { listSnapshots } from "../src/runtime/pilot-snapshots.ts";
import { fixture } from "./helpers.ts";

const password = "fixture-only-long-password";

test("operator pilot page is private and its forms are CSRF-protected", async () => {
  const f = fixture();
  const runId = f.store.create("demo");
  f.config.publicRunId = runId;
  f.services.economy.health = () => ({ available: true });
  f.services.economy.summary = async () => null;
  const server = webServer({
    ...f,
    secrets: { operatorPasswordHash: await hashPassword(password) },
    env: {},
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const origin = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  f.config.publicOrigin = origin;
  try {
    assert.equal((await fetch(origin + "/operator/pilot")).status, 401);
    assert.equal((await fetch(origin + "/api/operator/pilot")).status, 401);
    const login = await fetch(origin + "/api/operator/login", {
      method: "POST",
      headers: { Origin: origin, "Content-Type": "application/json" },
      body: JSON.stringify({ password }),
    });
    const cookie = login.headers.get("set-cookie")!.split(";")[0]!;
    const { csrf } = (await login.json()) as { csrf: string };
    const form = (
      action: string,
      fields: Record<string, string>,
      extra: Record<string, string> = {},
    ) =>
      fetch(origin + "/operator/pilot/" + action, {
        method: "POST",
        redirect: "manual",
        headers: {
          Cookie: cookie,
          Origin: origin,
          "Content-Type": "application/x-www-form-urlencoded",
          ...extra,
        },
        body: new URLSearchParams(fields),
      });

    const page = await (
      await fetch(origin + "/operator/pilot", { headers: { Cookie: cookie } })
    ).text();
    assert.match(page, /Go-live checklist/);
    assert.match(page, new RegExp(runId));
    assert.match(page, /name="csrf" value="/);
    assert.match(page, /Take snapshot/);

    // CSRF and same-origin protection.
    assert.equal((await form("attest", { evidence: "x" })).status, 403);
    assert.equal(
      (
        await form(
          "attest",
          { evidence: "x", csrf },
          { Origin: "https://evil.example" },
        )
      ).status,
      403,
    );
    assert.equal(latestAttestation(f.db, runId), null);

    let response = await form("attest", {
      csrf,
      stance: "affirmed",
      evidence: "paypal txn 1",
      note: "unrelated buyer",
    });
    assert.equal(response.status, 303);
    assert.equal(response.headers.get("location"), "/operator/pilot");
    assert.equal(latestAttestation(f.db, runId)?.stance, "affirmed");

    response = await form("cost", {
      csrf,
      category: "hosting",
      amount: "6.50",
      from: "2026-11-01",
      to: "2026-11-30",
      evidence: "invoice 881",
    });
    assert.equal(response.status, 303);
    response = await form("cost", {
      csrf,
      corrects: "1",
      amount: "1.50",
      evidence: "credit note",
    });
    assert.equal(response.status, 303);
    assert.equal(outsideLedgerCosts(f.db, runId, false).totalMicros, "5000000");
    response = await form("cost", {
      csrf,
      category: "hosting",
      amount: "abc",
      from: "2026-11-01",
      to: "2026-11-30",
      evidence: "x",
    });
    assert.equal(response.status, 400);
    assert.match(await response.text(), /Amount must be USD/);
    assert.equal(outsideLedgerCosts(f.db, runId, false).entries, 2);

    response = await form("snapshot", { csrf, publish: "true" });
    assert.equal(response.status, 303);
    assert.doesNotMatch(
      await (await fetch(origin + "/pilot")).text(),
      /No results published yet/,
    );
    const [snapshot] = listSnapshots(f.db, runId);
    response = await form("retract", {
      csrf,
      snapshotId: String(snapshot!.id),
    });
    assert.equal(response.status, 303);
    assert.match(
      await (await fetch(origin + "/pilot")).text(),
      /No results published yet/,
    );

    const api = await fetch(origin + "/api/operator/pilot", {
      headers: { Cookie: cookie },
    });
    const data = (await api.json()) as {
      report: { attestation: { status: string } };
      readiness: { ready: boolean };
    };
    assert.equal(data.report.attestation.status, "affirmed");
    assert.equal(data.readiness.ready, false);
    const json = await fetch(origin + "/api/operator/pilot/attest", {
      method: "POST",
      headers: {
        Cookie: cookie,
        Origin: origin,
        "Content-Type": "application/json",
        "x-csrf-token": csrf,
      },
      body: JSON.stringify({ stance: "withdrawn", evidence: "doubt" }),
    });
    assert.equal(json.status, 200);
    assert.equal(latestAttestation(f.db, runId)?.stance, "withdrawn");

    const after = await (
      await fetch(origin + "/operator/pilot", { headers: { Cookie: cookie } })
    ).text();
    assert.match(after, /invoice 881/);
    assert.match(after, /Retracted/);
    assert.doesNotMatch(after, /<script/);
  } finally {
    await new Promise<void>((resolve) => server.close(() => resolve()));
    f.close();
  }
});
