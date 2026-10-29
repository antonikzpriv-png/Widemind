import test from "node:test";
import assert from "node:assert/strict";
import { fixture } from "./helpers.ts";
import { webServer } from "../src/web/server.ts";
import { hashPassword } from "../src/auth/password.ts";
async function setup() {
  const f = fixture();
  Object.assign(f.config, { dependencyMs: 30 });
  const server = webServer({
    ...f,
    secrets: {
      operatorPasswordHash: await hashPassword("fixture-event-password"),
    },
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  assert.ok(address && typeof address !== "string");
  const origin = `http://127.0.0.1:${address.port}`;
  f.config.publicOrigin = origin;
  const post = (
    path: string,
    value: unknown,
    headers: Record<string, string> = {},
  ) =>
    fetch(origin + path, {
      method: "POST",
      headers: {
        Origin: origin,
        "Content-Type": "application/json",
        ...headers,
      },
      body: JSON.stringify(value),
    });
  const login = await post("/api/operator/login", {
    password: "fixture-event-password",
  });
  const cookie = login.headers.get("set-cookie")!.split(";")[0]!;
  const { csrf } = (await login.json()) as { csrf: string };
  return {
    ...f,
    server,
    origin,
    post,
    headers: { Cookie: cookie, "x-csrf-token": csrf },
    async closeHttp() {
      await new Promise<void>((resolve) => server.close(() => resolve()));
      f.close();
    },
  };
}
test("operator event ingress is authenticated, idempotent and private", async () => {
  const f = await setup();
  try {
    const id = f.store.create("demo");
    const path = `/api/operator/runs/${id}/events`;
    const event = {
      eventId: "operator-event-1",
      source: "pretend-processor",
      kind: "operator.note",
      payload: { privateText: "private-inbox-marker" },
    };
    assert.equal((await f.post(path, event)).status, 401);
    assert.equal(
      (await f.post(path, event, { Cookie: f.headers.Cookie })).status,
      403,
    );
    assert.equal((await f.post(path, event, f.headers)).status, 202);
    assert.equal((await f.post(path, event, f.headers)).status, 200);
    assert.equal(
      (await f.post(path, { ...event, payload: { changed: true } }, f.headers))
        .status,
      409,
    );
    assert.equal(f.store.pendingEvents(id)[0]?.source, "operator");
    assert.equal(f.store.run(id)?.state, "paused");
    const runtime = await fetch(f.origin + "/api/operator/runtime", {
      headers: f.headers,
    });
    assert.equal(runtime.status, 200);
    assert.equal(
      ((await runtime.json()) as { pendingEvents: number }).pendingEvents,
      1,
    );
    assert.equal((await fetch(f.origin + "/api/operator/runtime")).status, 401);
    const page = await fetch(f.origin + "/operator", { headers: f.headers });
    assert.match(await page.text(), /Worker: unavailable/);
    const publicText = await (await fetch(f.origin + "/api/public")).text();
    assert.doesNotMatch(
      publicText,
      /private-inbox-marker|pendingEvents|lastHeartbeat/,
    );
  } finally {
    await f.closeHttp();
  }
});
test("public reads degrade explicitly and module timeouts return service-unavailable", async () => {
  const f = await setup();
  try {
    f.store.create("demo");
    f.services.economy.summary = async () => new Promise(() => {});
    f.services.website.publicContent = async () => new Promise(() => {});
    const page = await fetch(f.origin + "/api/public");
    assert.equal(page.status, 200);
    const data = (await page.json()) as {
      financial: unknown;
      availability: { available: boolean };
    };
    assert.equal(data.financial, null);
    assert.equal(data.availability.available, false);
    f.services.routes.push({
      path: "/api/modules/fixture/stall",
      method: "POST",
      access: "operator",
      handle: async () => new Promise(() => {}),
    });
    const response = await f.post("/api/modules/fixture/stall", {}, f.headers);
    assert.equal(response.status, 503);
    assert.equal((await fetch(f.origin + "/healthz")).status, 200);
  } finally {
    await f.closeHttp();
  }
});

test("module forwarding strips the core session and CSRF credentials", async () => {
  const f = await setup();
  try {
    f.services.routes.push({
      path: "/api/modules/fixture/headers",
      method: "POST",
      access: "operator",
      handle: async (input) => ({
        status: 200,
        body: { headers: input.headers, body: input.body },
      }),
    });
    const response = await f.post(
      "/api/modules/fixture/headers",
      { csrf: f.headers["x-csrf-token"], value: "safe" },
      {
        ...f.headers,
        Cookie: f.headers.Cookie + "; customer_session=customer-only",
      },
    );
    assert.equal(response.status, 200);
    const result = (await response.json()) as {
      headers: Record<string, string>;
      body: unknown;
    };
    assert.equal(result.headers.cookie, "customer_session=customer-only");
    assert.equal(result.headers["x-csrf-token"], undefined);
    assert.deepEqual(result.body, { value: "safe" });
  } finally {
    await f.closeHttp();
  }
});

test("public ingress receives exact signed bytes and rejects an altered delivery", async () => {
  const f = await setup();
  const { createHmac, timingSafeEqual } = await import("node:crypto");
  const key = "fixture-only-signature-key";
  const sign = (body: Uint8Array) =>
    createHmac("sha256", key).update(body).digest();
  try {
    const id = f.store.create("demo");
    f.services.routes.push({
      path: "/api/modules/fixture/signed",
      method: "POST",
      access: "public",
      handle: async (input) => {
        const expected = sign(input.rawBody ?? new Uint8Array());
        const provided = Buffer.from(
          input.headers["x-fixture-signature"] ?? "",
          "hex",
        );
        if (
          provided.length !== expected.length ||
          !timingSafeEqual(provided, expected)
        )
          return { status: 401, body: { error: "Invalid signature" } };
        const result = f.store.deliverEvent(id, {
          source: "fixture-signed-provider",
          accountRef: "fixture-account",
          externalEventId: "signed-event-1",
          kind: "message.received",
          payload: input.body,
        });
        return {
          status: result.duplicate ? 200 : 202,
          body: { accepted: true },
        };
      },
    });
    const original = Buffer.from(' { "text" : "signed private task data" } ');
    const headers = {
      "Content-Type": "application/json",
      "x-fixture-signature": sign(original).toString("hex"),
    };
    assert.equal(
      (
        await fetch(f.origin + "/api/modules/fixture/signed", {
          method: "POST",
          headers,
          body: original,
        })
      ).status,
      202,
    );
    assert.equal(
      (
        await fetch(f.origin + "/api/modules/fixture/signed", {
          method: "POST",
          headers,
          body: original,
        })
      ).status,
      200,
    );
    assert.equal(
      (
        await fetch(f.origin + "/api/modules/fixture/signed", {
          method: "POST",
          headers,
          body: JSON.stringify({ text: "signed private task data" }),
        })
      ).status,
      401,
    );
    assert.equal(f.store.pendingEvents(id).length, 1);
    assert.equal(await f.services.economy.summary(id), null);
  } finally {
    await f.closeHttp();
  }
});
