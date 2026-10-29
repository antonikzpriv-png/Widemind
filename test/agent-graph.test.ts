import test from "node:test";
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { agentGraphProjection } from "../src/web/agent-graph.ts";
import { publicPage } from "../src/web/pages.ts";
import { renderBusinessPublic } from "../src/modules/brief-business/ui.ts";
import { publicProjection } from "../src/web/projections.ts";
import { webServer } from "../src/web/server.ts";
import { fixture } from "./helpers.ts";

function add(
  f: ReturnType<typeof fixture>,
  runId: string,
  options: {
    state?: string;
    micros?: unknown;
    tool?: string;
    provisional?: boolean;
    malformed?: boolean;
    noReceipt?: boolean;
    wrongAction?: boolean;
    at?: number;
  } = {},
) {
  const id = randomUUID();
  f.db
    .prepare(
      "INSERT INTO runtime_actions(id,run_id,work_key,state,action_json,receipt_json,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
    )
    .run(
      id,
      runId,
      id,
      options.state ?? "completed",
      JSON.stringify({
        tool: options.tool ?? "ai.text",
        jobId: "private-customer-job-canary",
        input: { prompt: "private-reasoning-canary" },
      }),
      options.noReceipt
        ? null
        : options.malformed
          ? "not json private-canary"
          : JSON.stringify({
              version: 1,
              actionId: options.wrongAction ? "wrong-action" : id,
              status: "succeeded",
              result: { text: "private-result-canary" },
              usage: {
                actualCharge: {
                  currency: "USD",
                  micros: options.micros ?? "1234",
                },
                provisional: options.provisional ?? false,
                provider: "private-provider-canary",
                evidenceRef: "private-evidence-canary",
              },
            }),
      options.at ?? Date.now(),
      options.at ?? Date.now(),
    );
  return id;
}

test("graph projects only allowlisted activity and exact receipt costs, never private inputs", () => {
  const f = fixture();
  try {
    const id = f.store.create("demo");
    const actionId = add(f, id);
    f.store.workerSeen("worker", "demo", 1000);
    const graph = agentGraphProjection(f.store);
    assert.equal(graph.workerConnected, true);
    assert.equal(graph.totalActions, 1);
    assert.equal(graph.nodes[0]?.kind, "thinking");
    assert.deepEqual(graph.nodes[0]?.cost, {
      micros: "1234",
      status: "settled",
    });
    assert.match(graph.nodes[0]!.id, /^[a-f0-9]{24}$/);
    assert.doesNotMatch(
      JSON.stringify(graph),
      /private-|prompt|provider|authorization|receipt|jobId|ai.text/,
    );
    assert.equal(JSON.stringify(graph).includes(actionId), false);
    f.db
      .prepare("UPDATE runtime_actions SET state='failed' WHERE id=?")
      .run(actionId);
    const changed = agentGraphProjection(f.store);
    assert.equal(changed.nodes[0]?.id, graph.nodes[0]?.id);
    assert.equal(changed.nodes.length, 1);
    assert.deepEqual(changed.nodes[0]?.cost, graph.nodes[0]?.cost);
  } finally {
    f.close();
  }
});

test("graph distinguishes pending, provisional, unposted, unavailable, and actual zero costs", () => {
  const f = fixture();
  try {
    const id = f.store.create("demo");
    add(f, id, { state: "running", noReceipt: true, at: 1 });
    add(f, id, { state: "unresolved", provisional: true, at: 2 });
    add(f, id, { state: "unresolved", at: 3 });
    add(f, id, { malformed: true, at: 4 });
    add(f, id, { micros: "0", at: 5 });
    add(f, id, { micros: "1.2", at: 6 });
    add(f, id, { wrongAction: true, at: 7 });
    add(f, id, { micros: "9223372036854775808", at: 8 });
    add(f, id, { state: "failed", noReceipt: true, at: 9 });
    assert.deepEqual(
      agentGraphProjection(f.store).nodes.map((n) => n.cost),
      [
        { micros: null, status: "pending" },
        { micros: "1234", status: "provisional" },
        { micros: "1234", status: "recorded" },
        { micros: null, status: "unavailable" },
        { micros: "0", status: "settled" },
        { micros: null, status: "unavailable" },
        { micros: null, status: "unavailable" },
        { micros: null, status: "unavailable" },
        { micros: null, status: "unavailable" },
      ],
    );
  } finally {
    f.close();
  }
});

test("bounded graph honors configured run and worker expiry without implicitly switching identities", () => {
  const f = fixture();
  try {
    const first = f.store.create("demo", 1000);
    for (let i = 0; i < 60; i++)
      add(f, first, { at: 1000 + i, tool: "untrusted-private-tool-canary" });
    const second = f.store.create("demo", 10000);
    f.store.workerSeen("worker", "demo", 100, 10000);
    const graph = agentGraphProjection(f.store, first, 10101);
    assert.equal(graph.workerConnected, false);
    assert.equal(graph.totalActions, 60);
    assert.equal(graph.nodes.length, 48);
    assert.equal(graph.nodes[0]?.at, new Date(1012).toISOString());
    assert.equal(graph.nodes[0]?.kind, "task");
    assert.equal(agentGraphProjection(f.store).runId, second);
    const absent = agentGraphProjection(f.store, randomUUID());
    assert.equal(absent.runId, null);
    assert.equal(absent.totalActions, 0);
    assert.deepEqual(absent.nodes, []);
  } finally {
    f.close();
  }
});

test("home network works with legacy projections and both configured public renderers", async () => {
  const f = fixture();
  try {
    const run = f.store.create("demo");
    add(f, run);
    const data = await publicProjection(f.store, f.services);
    for (const html of [publicPage(data), await renderBusinessPublic(data)]) {
      assert.match(html, /class="network-home"/);
      assert.match(html, /id="thinking-svg"/);
      assert.match(html, /\$0\.001234/);
      assert.match(html, /src="\/thinking-network.js" defer/);
      assert.match(html, /id="headquarters-details"/);
      assert.doesNotMatch(html, /private-.*canary/);
    }
    const { agentGraph: omitted, ...legacy } = data;
    assert.ok(omitted);
    assert.match(publicPage(legacy), /Awaiting a run/);
  } finally {
    f.close();
  }
});

test("public graph polling is read-only and independent of remote content/economy providers", async () => {
  const f = fixture();
  f.services.website.publicContent = async () => {
    throw new Error("Must not call remote website provider");
  };
  f.services.economy.summary = async () => {
    throw new Error("Must not poll accounting provider");
  };
  const runId = f.store.create("demo");
  add(f, runId);
  const server = webServer({
    ...f,
    secrets: { operatorPasswordHash: null },
  });
  try {
    await new Promise<void>((resolve) =>
      server.listen(0, "127.0.0.1", resolve),
    );
    const address = server.address();
    assert.ok(address && typeof address !== "string");
    const origin = `http://127.0.0.1:${address.port}`;
    const response = await fetch(origin + "/api/public/graph");
    assert.equal(response.status, 200);
    assert.equal(response.headers.get("cache-control"), "no-store");
    assert.match(
      response.headers.get("content-security-policy")!,
      /script-src 'self'/,
    );
    assert.equal((await response.json()).nodes.length, 1);
    assert.equal(f.store.run(runId)?.state, "paused");
    assert.equal(f.store.actions(runId).length, 1);
    const script = await fetch(origin + "/thinking-network.js");
    assert.equal(script.status, 200);
    assert.match(script.headers.get("content-type")!, /text\/javascript/);
    assert.equal((await fetch(origin + "/api/operator/status")).status, 401);
  } finally {
    await new Promise<void>((resolve) => server.close(() => resolve()));
    f.close();
  }
});
