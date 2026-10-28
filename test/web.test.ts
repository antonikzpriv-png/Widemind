import test from "node:test";
import assert from "node:assert/strict";
import type { Server } from "node:http";
import { webServer } from "../src/web/server.ts";
import { hashPassword } from "../src/auth/password.ts";
import { publicProjection, privateProjection } from "../src/web/projections.ts";
import { fixture } from "./helpers.ts";
async function listen(server: Server) {
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  if (!address || typeof address === "string")
    throw new Error("Missing test port");
  return `http://127.0.0.1:${address.port}`;
}
async function close(server: Server) {
  await new Promise<void>((resolve, reject) =>
    server.close((error) => (error ? reject(error) : resolve())),
  );
}
const password = "fixture-only-long-password";
test("server authentication, CSRF, durable sessions, logout and public/private projections", async () => {
  const f = fixture();
  const passwordHash = await hashPassword(password);
  let server = webServer({
    ...f,
    secrets: { operatorPasswordHash: passwordHash },
  });
  try {
    let url = await listen(server);
    f.config.publicOrigin = url;
    const req = (
      path: string,
      body: unknown,
      extra: Record<string, string> = {},
    ) =>
      fetch(url + path, {
        method: "POST",
        headers: { Origin: url, "Content-Type": "application/json", ...extra },
        body: JSON.stringify(body),
        redirect: "manual",
      });
    assert.equal((await fetch(url + "/api/operator/status")).status, 401);
    assert.equal((await req("/api/operator/runs", {})).status, 401);
    assert.equal(
      (
        await req(
          "/api/operator/login",
          { password },
          { Origin: "https://attacker.invalid" },
        )
      ).status,
      403,
    );
    assert.equal(
      (await req("/api/operator/login", { password: "incorrect" })).status,
      401,
    );
    const login = await req("/api/operator/login", { password });
    assert.equal(login.status, 200);
    const cookie = login.headers.get("set-cookie")!.split(";")[0]!;
    assert.match(login.headers.get("set-cookie")!, /HttpOnly; SameSite=Strict/);
    const { csrf } = (await login.json()) as { csrf: string };
    assert.equal(
      (await req("/api/operator/runs", {}, { Cookie: cookie })).status,
      403,
    );
    const headers = { Cookie: cookie, "x-csrf-token": csrf };
    const created = await req("/api/operator/runs", {}, headers);
    assert.equal(created.status, 201);
    const { runId } = (await created.json()) as { runId: string };
    assert.equal(
      (await req(`/api/operator/runs/${runId}/resume`, {}, headers)).status,
      200,
    );
    await f.runtime.tick();
    // Add an explicitly private operational detail; public output must not copy it.
    f.store.event(
      runId,
      "private.marker",
      "private-customer-marker secret-fixture-marker",
    );
    const publicResponse = await fetch(url + "/api/public");
    const publicText = await publicResponse.text();
    assert.doesNotMatch(
      publicText,
      /private-customer-marker|secret-fixture-marker|authorization|demo-zero-cost-only|receipt|reservation/,
    );
    const operator = await fetch(url + "/api/operator/status", {
      headers: { Cookie: cookie },
    });
    assert.equal(operator.status, 200);
    const privateText = await operator.text();
    assert.match(privateText, /private-customer-marker/);
    assert.doesNotMatch(privateText, /demo-zero-cost-only/);
    assert.equal(
      (await fetch(url + "/api/public")).headers.get("cache-control"),
      "no-store",
    );
    const page = await fetch(url + "/operator", {
      headers: { Cookie: cookie },
    });
    assert.match(await page.text(), /Run control/);
    await close(server);
    server = webServer({
      ...f,
      secrets: { operatorPasswordHash: passwordHash },
    });
    url = await listen(server);
    f.config.publicOrigin = url;
    assert.equal(
      (
        await fetch(url + "/api/operator/status", {
          headers: { Cookie: cookie },
        })
      ).status,
      200,
      "Session survives web restart",
    );
    assert.equal((await req("/api/operator/logout", {}, headers)).status, 200);
    assert.equal(
      (
        await fetch(url + "/api/operator/status", {
          headers: { Cookie: cookie },
        })
      ).status,
      401,
    );
    assert.equal(
      (await fetch(url + "/operator")).headers
        .get("content-security-policy")
        ?.includes("frame-ancestors 'none'"),
      true,
    );
  } finally {
    await close(server);
    f.close();
  }
});
test("missing authentication fails closed and login rate limit is durable", async () => {
  const f = fixture();
  let server = webServer({ ...f, secrets: { operatorPasswordHash: null } });
  try {
    let url = await listen(server);
    f.config.publicOrigin = url;
    const login = () =>
      fetch(url + "/api/operator/login", {
        method: "POST",
        headers: { Origin: url, "Content-Type": "application/json" },
        body: JSON.stringify({ password: "incorrect" }),
      });
    assert.equal((await login()).status, 503);
    assert.equal((await fetch(url + "/api/operator/status")).status, 401);
    assert.match(
      await (await fetch(url + "/operator")).text(),
      /Authentication unavailable/,
    );
    await close(server);
    server = webServer({
      ...f,
      secrets: { operatorPasswordHash: await hashPassword(password) },
    });
    url = await listen(server);
    f.config.publicOrigin = url;
    for (let i = 0; i < 10; i++) assert.equal((await login()).status, 401);
    assert.equal((await login()).status, 429);
  } finally {
    await close(server);
    f.close();
  }
});
test("public and private data are distinct schemas, not filtered raw logs", async () => {
  const f = fixture();
  try {
    const id = f.store.create("demo");
    f.store.control(id, "resume");
    await f.runtime.tick();
    const data = await publicProjection(f.store, f.services);
    assert.equal(data.financial, null);
    assert.deepEqual(data.activity, []);
    assert.deepEqual(data.offerings, []);
    assert.equal("reason" in data.runs[0]!, false);
    assert.equal("actions" in data.runs[0]!, false);
    assert.equal(
      privateProjection(f.store, f.services).runs[0]?.actions.length,
      1,
    );
  } finally {
    f.close();
  }
});

test("module routes integrate without root edits and cannot bypass operator controls", async () => {
  const f = fixture();
  const { registerModules } = await import("../src/modules/registry.ts");
  const services = registerModules({ db: f.db, config: f.config }, [
    {
      id: "integration",
      contractVersion: 1,
      create: () => ({
        routes: [
          {
            path: "/api/modules/integration/status",
            method: "GET",
            access: "public",
            handle: async (input) => ({
              status: 200,
              body: { available: true, operator: input.operator },
            }),
          },
          {
            path: "/api/modules/integration/control",
            method: "POST",
            access: "operator",
            handle: async (input) => ({
              status: 200,
              body: { authorized: input.operator },
            }),
          },
        ],
      }),
    },
  ]);
  const server = webServer({
    ...f,
    services,
    secrets: { operatorPasswordHash: await hashPassword(password) },
  });
  try {
    const url = await listen(server);
    f.config.publicOrigin = url;
    assert.deepEqual(
      await (await fetch(url + "/api/modules/integration/status")).json(),
      { available: true, operator: false },
    );
    const post = (
      path: string,
      headers: Record<string, string> = {},
      body: unknown = {},
    ) =>
      fetch(url + path, {
        method: "POST",
        headers: {
          Origin: url,
          "Content-Type": "application/json",
          ...headers,
        },
        body: JSON.stringify(body),
      });
    assert.equal((await post("/api/modules/integration/control")).status, 401);
    const login = await post("/api/operator/login", {}, { password });
    const cookie = login.headers.get("set-cookie")!.split(";")[0]!;
    const { csrf } = (await login.json()) as { csrf: string };
    assert.equal(
      (await post("/api/modules/integration/control", { Cookie: cookie }))
        .status,
      403,
    );
    assert.equal(
      (
        await post("/api/modules/integration/control", {
          Cookie: cookie,
          "x-csrf-token": csrf,
          Origin: "https://attacker.invalid",
        })
      ).status,
      403,
    );
    assert.deepEqual(
      await (
        await post("/api/modules/integration/control", {
          Cookie: cookie,
          "x-csrf-token": csrf,
        })
      ).json(),
      { authorized: true },
    );
    assert.deepEqual(
      await (
        await fetch(url + "/api/modules/integration/status", {
          headers: { Cookie: cookie },
        })
      ).json(),
      { available: true, operator: false },
    );
  } finally {
    await close(server);
    f.close();
  }
});
test("password rotation invalidates stored sessions without exposing raw tokens", async () => {
  const f = fixture();
  const { Sessions } = await import("../src/auth/sessions.ts");
  try {
    const one = new Sessions(f.db, await hashPassword(password));
    const created = one.create();
    const request = {
      headers: { cookie: `widemind_session=${created.token}` },
    } as import("node:http").IncomingMessage;
    assert.ok(one.get(request));
    assert.doesNotMatch(
      JSON.stringify(f.db.prepare("SELECT * FROM auth_sessions").all()),
      new RegExp(created.token),
    );
    const two = new Sessions(
      f.db,
      await hashPassword("different-fixture-password"),
    );
    assert.equal(two.get(request), null);
  } finally {
    f.close();
  }
});
test("financial public projection drops undeclared private provider fields", async () => {
  const f = fixture();
  try {
    f.store.create("demo");
    f.services.economy.summary = async () =>
      Object.assign(
        {
          currency: "USD" as const,
          openingCreditMicros: "0",
          postedMicros: "0",
          reservedMicros: "0",
          holdsMicros: "0",
          spendableMicros: "0",
          customerReceiptsMicros: "0",
          earnedRevenueMicros: "0",
          operatingChargesMicros: "0",
          ownerFundingMicros: "0",
          subsidiesMicros: "0",
          mode: "sandbox" as const,
          asOf: new Date().toISOString(),
        },
        { privateToken: "private-financial-fixture" },
      );
    assert.doesNotMatch(
      JSON.stringify(await publicProjection(f.store, f.services)),
      /private-financial-fixture|privateToken/,
    );
  } finally {
    f.close();
  }
});
