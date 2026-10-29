import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import {
  connection,
  createTools,
  toolsRequest,
} from "../src/modules/tools/module.ts";
import type { ToolAction, Reservation } from "../src/contracts/index.ts";
const action: ToolAction = {
  version: 1,
  actionId: "fixture-action",
  runId: "fixture-run",
  workKey: "local-file",
  tool: "files",
  input: { operation: "create", path: "note.txt", text: "hello" },
  limit: {
    maximum: { currency: "USD", micros: "0" },
    maxDurationMs: 1000,
    maxOutputBytes: 10000,
  },
};
const reservation: Reservation = {
  actionId: action.actionId,
  runId: action.runId,
  reservationId: "fixture-reservation",
  authorization: "opaque-fixture",
  limit: action.limit,
};
test("tools connection fails closed for incomplete credentials and non-loopback HTTP", () => {
  assert.equal(connection({}), null);
  assert.throws(() =>
    connection({
      WIDEMIND_TOOLS_URL: "http://example.com",
      WIDEMIND_TOOLS_TOKEN: "x".repeat(40),
    }),
  );
  assert.throws(() =>
    connection({
      WIDEMIND_TOOLS_URL: "http://127.0.0.1:8081",
      WIDEMIND_TOOLS_TOKEN: "short",
    }),
  );
});
test("Node tools gateway exposes verified availability and passes reservation-bound actions", async () => {
  const calls: Array<{ path: string; body: unknown }> = [];
  const token = "fixture-only-" + "t".repeat(32);
  const server = createServer(async (req, res) => {
    assert.equal(req.headers.authorization, "Bearer " + token);
    let raw = "";
    for await (const chunk of req) raw += chunk;
    calls.push({ path: req.url!, body: raw ? JSON.parse(raw) : null });
    res.setHeader("Content-Type", "application/json");
    if (req.url === "/capabilities") {
      res.end(
        JSON.stringify({
          capabilities: [{ id: "files", availability: { available: true } }],
        }),
      );
      return;
    }
    if (req.url === "/execute") {
      res.end(
        JSON.stringify({
          kind: "receipt",
          receipt: {
            version: 1,
            actionId: action.actionId,
            status: "succeeded",
            result: { file: "note.txt" },
            usage: {
              actualCharge: { currency: "USD", micros: "0" },
              provider: "files",
              accountRef: "dedicated-tools",
              externalEventId: action.actionId,
              billableFailure: false,
              provisional: false,
              evidenceRef: "fixture-local",
            },
          },
        }),
      );
      return;
    }
    res.end(
      JSON.stringify({ kind: "unknown", reason: "Provider lookup pending" }),
    );
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  assert.ok(address && typeof address !== "string");
  const config = { url: `http://127.0.0.1:${address.port}`, token };
  const tools = createTools(config);
  try {
    assert.equal(tools.health().available, false);
    assert.deepEqual(tools.capabilities(), []);
    await tools.refresh();
    assert.equal(tools.health().available, true);
    assert.equal(tools.capabilities()[0]?.id, "files");
    const result = await tools.execute(
      action,
      reservation,
      new AbortController().signal,
    );
    assert.equal(result.kind, "receipt");
    assert.deepEqual(calls.find((c) => c.path === "/execute")?.body, {
      action,
      reservation,
    });
    assert.equal((await tools.reconcile(action, reservation)).kind, "unknown");
  } finally {
    await new Promise<void>((resolve) => server.close(() => resolve()));
  }
});
test("unreachable tool service never becomes a successful tool result", async () => {
  const server = createServer();
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  assert.ok(address && typeof address !== "string");
  await new Promise<void>((resolve) => server.close(() => resolve()));
  const tools = createTools({
    url: `http://127.0.0.1:${address.port}`,
    token: "t".repeat(40),
  });
  await tools.refresh();
  assert.equal(tools.health().available, false);
  assert.equal(
    (await tools.execute(action, reservation, new AbortController().signal))
      .kind,
    "unknown",
  );
  assert.equal(
    (await tools.reconcile(action, reservation)).kind,
    "unavailable",
  );
});
