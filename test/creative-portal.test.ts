import test from "node:test";
import assert from "node:assert/strict";
import { createServer, type Server } from "node:http";
import { fixture } from "./helpers.ts";
import { webServer } from "../src/web/server.ts";
import { registerModules } from "../src/modules/registry.ts";
import { definition } from "../src/modules/business/module.ts";

async function listen(server: Server) {
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  assert.ok(address && typeof address !== "string");
  return `http://127.0.0.1:${address.port}`;
}
const close = (server: Server) =>
  new Promise<void>((resolve) => server.close(() => resolve()));

test("private previews require ownership, remain opaque and cannot relax the parent site's CSP", async () => {
  const f = fixture("foundation");
  const runId = f.store.create("foundation");
  const token = "a".repeat(43);
  let calls = 0;
  let received: Record<string, unknown> = {};
  let mediaType = "text/html";
  let content =
    "<!doctype html><p>Private calculator</p><script>window.privatePreview=true</script>";
  const upstream = createServer(async (request, response) => {
    let raw = "";
    for await (const chunk of request) raw += chunk;
    received = JSON.parse(raw);
    calls++;
    assert.equal(request.headers.authorization, `Bearer ${"b".repeat(48)}`);
    assert.equal(request.headers.cookie, undefined);
    response.setHeader("Content-Type", "application/json");
    if (received.token !== token) {
      response.writeHead(403);
      response.end(JSON.stringify({ error: "private access required" }));
      return;
    }
    response.end(
      JSON.stringify({
        filename: "index.html",
        mediaType,
        encoding: "utf-8",
        content,
      }),
    );
  });
  let web: Server | undefined;
  try {
    const address = await listen(upstream);
    const services = registerModules(
      {
        db: f.db,
        config: f.config,
        env: {
          WIDEMIND_BUSINESS_URL: address,
          WIDEMIND_BUSINESS_TOKEN: "b".repeat(48),
          WIDEMIND_COMMERCE_RUN_ID: runId,
        },
      },
      [definition],
    );
    web = webServer({
      ...f,
      services,
      secrets: { operatorPasswordHash: null },
    });
    const origin = await listen(web);
    f.config.publicOrigin = origin;
    const request = (body: unknown, requestOrigin = origin) =>
      fetch(origin + "/api/modules/business/preview", {
        method: "POST",
        headers: {
          Origin: requestOrigin,
          "Content-Type": "application/json",
          Cookie: "widemind_session=operator-canary",
        },
        body: JSON.stringify(body),
      });
    const valid = {
      token,
      orderId: "private-order",
      version: 1,
      filename: "index.html",
    };
    const blocked = await request(valid, "https://other.invalid");
    assert.equal(blocked.status, 403);
    assert.equal(calls, 0);
    assert.equal(
      (await request({ ...valid, token: "c".repeat(43) })).status,
      403,
    );
    const preview = await request(valid);
    assert.equal(preview.status, 200);
    assert.match(preview.headers.get("content-type")!, /text\/html/);
    assert.equal(preview.headers.get("x-frame-options"), "SAMEORIGIN");
    assert.equal(preview.headers.get("referrer-policy"), "no-referrer");
    assert.equal(preview.headers.get("cache-control"), "no-store");
    const csp = preview.headers.get("content-security-policy")!;
    assert.match(csp, /sandbox allow-scripts;/);
    assert.doesNotMatch(
      csp,
      /allow-same-origin|allow-forms|allow-popups|allow-top-navigation/,
    );
    assert.match(csp, /connect-src 'none'/);
    assert.match(csp, /form-action 'none'/);
    assert.equal(await preview.text(), content);
    assert.deepEqual(received, {
      runId,
      token,
      orderId: "private-order",
      version: 1,
      filename: "index.html",
    });
    const parent = await fetch(origin + "/commerce");
    assert.match(
      parent.headers.get("content-security-policy")!,
      /frame-src 'self'/,
    );
    assert.doesNotMatch(
      parent.headers.get("content-security-policy")!,
      /unsafe-inline|allow-same-origin/,
    );
    assert.equal(
      (await fetch(origin + "/api/modules/business/preview")).status,
      404,
    );
    mediaType = "application/javascript";
    assert.equal((await request(valid)).status, 400);
    mediaType = "image/png";
    content = "x".repeat(800_000);
    assert.equal((await request(valid)).status, 400);
    mediaType = "text/html";
    content = "x".repeat(1_000_001);
    assert.equal((await request(valid)).status, 400);
    assert.equal((await request({ ...valid, version: -1 })).status, 400);
    assert.equal(
      (await request({ ...valid, filename: "../../private" })).status,
      400,
    );
  } finally {
    if (web) await close(web);
    await close(upstream);
    f.close();
  }
});

test("manifest and bounded change routes preserve the customer identity and only wake durable work", async () => {
  const f = fixture("foundation");
  const runId = f.store.create("foundation");
  const delivered: Array<{ path: string; body: Record<string, unknown> }> = [];
  const upstream = createServer(async (request, response) => {
    let raw = "";
    for await (const chunk of request) raw += chunk;
    delivered.push({ path: request.url!, body: JSON.parse(raw) });
    response.setHeader("Content-Type", "application/json");
    response.end(JSON.stringify({ recorded: true }));
  });
  try {
    const address = await listen(upstream);
    const services = registerModules(
      {
        db: f.db,
        config: f.config,
        env: {
          WIDEMIND_BUSINESS_URL: address,
          WIDEMIND_BUSINESS_TOKEN: "b".repeat(48),
          WIDEMIND_COMMERCE_RUN_ID: runId,
        },
      },
      [definition],
    );
    const request = async (
      name: string,
      body: Record<string, string | number | Record<string, unknown>>,
    ) => {
      const route = services.routes.find((item) =>
        item.path.endsWith("/" + name),
      )!;
      return route.handle({
        body: body as never,
        query: {},
        headers: {},
        operator: false,
        remoteAddress: "fixture",
      });
    };
    const body = {
      token: "a".repeat(43),
      orderId: "order-1",
      requestId: "change-1",
      kind: "revision",
      feedback: "Improve spacing",
    };
    assert.equal((await request("change-request", body)).status, 200);
    assert.deepEqual(delivered[0], {
      path: "/v1/change-request",
      body: { ...body, runId },
    });
    assert.equal(f.store.pendingEvents(runId).length, 1);
    assert.equal((await request("change-request", body)).status, 200);
    assert.equal(f.store.pendingEvents(runId).length, 1);
    assert.equal(f.store.run(runId)?.state, "paused");
    assert.equal(
      (
        await request("manifest", {
          token: body.token,
          orderId: body.orderId,
          version: 1,
        })
      ).status,
      200,
    );
    assert.equal(delivered.at(-1)?.path, "/v1/manifest");
    assert.equal(
      (await request("change-request", { ...body, kind: "free-new-features" }))
        .status,
      400,
    );
    assert.equal(
      (await request("change-request", { ...body, feedback: " " })).status,
      400,
    );
    assert.equal(
      (await request("change-request", { ...body, runId: "other" })).status,
      400,
    );
    assert.equal(
      (
        await request("change-request", {
          ...body,
          kind: "scope_change",
          scope: { title: "New scope" },
        })
      ).status,
      200,
    );
    assert.deepEqual(delivered.at(-1)?.body.scope, { title: "New scope" });
  } finally {
    await close(upstream);
    f.close();
  }
});

test("HTML preview registration cannot attach unsandboxed responses to arbitrary method or operator routes", () => {
  for (const [access, method] of [
    ["operator", "POST"],
    ["public", "GET"],
  ] as const) {
    const f = fixture("foundation");
    try {
      assert.throws(
        () =>
          registerModules({ db: f.db, config: f.config }, [
            {
              id: "unsafe",
              contractVersion: 1,
              create: () => ({
                routes: [
                  {
                    path: "/api/modules/unsafe/preview",
                    access,
                    method,
                    responseKind: "sandboxed-html",
                    handle: async () => ({ status: 200, body: { html: "" } }),
                  },
                ],
              }),
            },
          ]),
        /public POST/,
      );
    } finally {
      f.close();
    }
  }
});

test("a future revision deadline keeps free payment observation scheduled and discovers newly funded work", async () => {
  const f = fixture("foundation");
  const runId = f.store.create("foundation");
  f.store.deliverEvent(runId, {
    source: "business",
    accountRef: "synthetic-control-plane",
    externalEventId: "accepted-second-order",
    kind: "business.changed",
    payload: { reference: "synthetic-second-order" },
  });
  let newlyFunded = false;
  const upstream = createServer(async (request, response) => {
    assert.equal(request.url, "/v1/work-state");
    for await (const _chunk of request) {
      /* consume the small authenticated request */
    }
    response.setHeader("Content-Type", "application/json");
    response.end(
      JSON.stringify({
        eventId: newlyFunded
          ? "synthetic-payment-change"
          : "synthetic-revision-expiry",
        attempt: 0,
        dueAt: Date.now() / 1000 + (newlyFunded ? -1 : 172800),
        runtimeState: "waiting",
      }),
    );
  });
  try {
    const address = await listen(upstream);
    const services = registerModules(
      {
        db: f.db,
        config: f.config,
        env: {
          WIDEMIND_BUSINESS_URL: address,
          WIDEMIND_BUSINESS_TOKEN: "b".repeat(48),
          WIDEMIND_COMMERCE_RUN_ID: runId,
        },
      },
      [definition],
    );
    const snapshot = {
      runId,
      now: new Date().toISOString(),
      financial: null,
      actions: [],
      capabilities: [],
      events: f.store.pendingEvents(runId, 100),
    };
    const before = Date.now();
    const waiting = await services.business.selectWork(snapshot);
    assert.equal(waiting.kind, "wait");
    if (waiting.kind !== "wait")
      throw new Error("Expected a scheduled observation");
    assert.ok(Date.parse(waiting.wakeAt!) <= Date.now() + 30000);
    assert.ok(Date.parse(waiting.wakeAt!) >= before);
    assert.deepEqual(
      waiting.acknowledgeEventIds,
      snapshot.events.map((event) => event.eventId),
    );
    newlyFunded = true;
    const ready = await services.business.selectWork({
      ...snapshot,
      events: [],
    });
    assert.equal(ready.kind, "work");
    if (ready.kind !== "work") throw new Error("Expected funded work");
    assert.equal(ready.candidate.tool, "business.step");
    assert.equal(ready.candidate.limit.maximum.micros, "0");
    assert.deepEqual(ready.candidate.input, {
      eventId: "synthetic-payment-change",
    });
  } finally {
    await close(upstream);
    f.close();
  }
});
