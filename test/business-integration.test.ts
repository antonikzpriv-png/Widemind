import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { fixture } from "./helpers.ts";
import { registerModules } from "../src/modules/registry.ts";
import { definition } from "../src/modules/business/module.ts";
import { createBusinessClient } from "../src/modules/business/client.ts";
const keys = [
  "WIDEMIND_BUSINESS_URL",
  "WIDEMIND_BUSINESS_TOKEN",
  "WIDEMIND_COMMERCE_RUN_ID",
] as const;
test("business selection is free and namespaced ingress is scoped and rate bounded", async () => {
  const f = fixture("foundation");
  const id = f.store.create("foundation");
  const saved = Object.fromEntries(keys.map((key) => [key, process.env[key]]));
  let due = false;
  let calls = 0;
  const server = createServer(async (request, response) => {
    assert.equal(request.headers.authorization, `Bearer ${"b".repeat(48)}`);
    let text = "";
    for await (const chunk of request) text += chunk;
    const input = JSON.parse(text);
    assert.equal(input.runId, id);
    calls++;
    response.setHeader("Content-Type", "application/json");
    response.end(
      JSON.stringify(
        request.url === "/v1/work-state"
          ? {
              eventId: due ? "event-1" : null,
              attempt: 0,
              dueAt: due ? Date.now() / 1000 - 1 : null,
              runtimeState: "active",
            }
          : {
              requestId: input.requestId,
              opportunityId: "opportunity-1",
              customerToken: input.customerToken,
              runId: id,
            },
      ),
    );
  });
  try {
    await new Promise<void>((resolve) =>
      server.listen(0, "127.0.0.1", resolve),
    );
    const address = server.address();
    assert.ok(address && typeof address !== "string");
    process.env.WIDEMIND_BUSINESS_URL = `http://127.0.0.1:${address.port}`;
    process.env.WIDEMIND_BUSINESS_TOKEN = "b".repeat(48);
    process.env.WIDEMIND_COMMERCE_RUN_ID = id;
    const provider = registerModules({ db: f.db, config: f.config }, [
      definition,
    ]);
    assert.ok(provider.business);
    const snapshot = {
      runId: id,
      now: new Date().toISOString(),
      financial: null,
      actions: [],
      capabilities: [],
      events: [],
    };
    assert.equal((await provider.business.selectWork(snapshot)).kind, "wait");
    due = true;
    const selected = await provider.business.selectWork(snapshot);
    assert.equal(selected.kind, "work");
    if (selected.kind === "work") {
      assert.equal(selected.candidate.tool, "business.step");
      assert.equal(selected.candidate.limit.maximum.micros, "0");
    }
    const request = provider.routes!.find((route) =>
      route.path.endsWith("/request"),
    )!;
    assert.equal(request.access, "public");
    const input = {
      body: {
        requestId: "customer-1",
        email: "provided@example.test",
        service: "svg-graphic",
        scope: { title: "Provided title" },
        customerToken: "x".repeat(43),
      },
      query: {},
      headers: {},
      operator: false,
    };
    const accepted = await request.handle(
      Object.assign({}, input, { remoteAddress: "local-fixture" }),
    );
    assert.equal(accepted.status, 202);
    assert.equal(
      (accepted.body as { customerToken: string }).customerToken,
      "x".repeat(43),
    );
    const wrong = await request.handle({
      ...input,
      body: { ...input.body, runId: "other-run" },
    });
    assert.equal(wrong.status, 400);
    for (let i = 0; i < 4; i++)
      assert.equal(
        (
          await request.handle(
            Object.assign({}, input, { remoteAddress: "local-fixture" }),
          )
        ).status,
        202,
      );
    const before = calls;
    assert.equal(
      (
        await request.handle(
          Object.assign({}, input, { remoteAddress: "local-fixture" }),
        )
      ).status,
      429,
    );
    assert.equal(calls, before);
  } finally {
    for (const key of keys) {
      if (saved[key] === undefined) delete process.env[key];
      else process.env[key] = saved[key];
    }
    await new Promise<void>((resolve) => server.close(() => resolve()));
    f.close();
  }
});
test("business configuration rejects remote plain HTTP and missing credentials", () => {
  assert.equal(createBusinessClient({}), null);
  assert.throws(() =>
    createBusinessClient({
      WIDEMIND_BUSINESS_URL: "http://example.test",
      WIDEMIND_BUSINESS_TOKEN: "x".repeat(48),
    }),
  );
  assert.throws(() =>
    createBusinessClient({ WIDEMIND_BUSINESS_URL: "http://127.0.0.1:8082" }),
  );
});
