import assert from "node:assert/strict";
import { createServer } from "node:http";
import test from "node:test";
import { hashPassword } from "../src/auth/password.ts";
import { definition } from "../src/modules/business/module.ts";
import {
  discoveryCommandPath,
  discoveryPagePath,
  renderDiscoveryPage,
} from "../src/modules/business/discovery-ui.ts";
import { registerModules } from "../src/modules/registry.ts";
import { webServer } from "../src/web/server.ts";
import { fixture } from "./helpers.ts";

function explicitStatus(runId: string) {
  return {
    run_id: runId,
    mode: "test",
    runtime_state: "paused",
    config: {
      enabled: false,
      allowance_micro_usd: 1000000,
      markets: [
        {
          id: "fixture-market",
          label: "Controlled source fixture",
          kind: "experiment",
          proposed_offering: "A bounded fixture prototype",
          source_urls: ["https://fixture.example.test/needs?x=1&y=2"],
          research_allowance_micro_usd: 1000,
          interval_seconds: 3600,
          cooldown_seconds: 3600,
        },
      ],
    },
    history_limit: 2,
    history_offset: 0,
    totals: {
      allocated_micro_usd: 1000,
      allowance_micro_usd: 1000000,
      discoveries: 3,
    },
    discoveries: [
      {
        id: "discovery-fixture",
        opportunity_id: "opportunity-fixture",
        market_id: "fixture-market",
        kind: "experiment",
        observed_need: "private-discovery-marker <script>untrusted()</script>",
        proposed_offering: "Fixture offering with an uncertain market",
        estimated_cost_micro_usd: 250000,
        uncertainty: ["Unverified demand"],
        next_action: "Operator review",
        evidence: {
          url: "https://fixture.example.test/needs?x=1&y=2",
          excerpt: "Exact private fixture need <img src=x onerror=untrusted()>",
          retrieved_at: 1793275200,
        },
      },
      {
        opportunity_id: "unsafe-link-fixture",
        kind: "lead",
        evidence: {
          url: "javascript:untrusted()",
          excerpt: "Unsafe URL fixture",
        },
      },
    ],
    experiments: [
      {
        id: "experiment-fixture",
        hypothesis: "A prototype can meet this limited scope",
        allowance_micro_usd: 1000,
        deadline: 1793275200,
        state: "stopped",
        artifact: {
          filename: "fixture.svg",
          media_type: "image/svg+xml",
          content_sha256: "fixture-hash",
        },
      },
    ],
    attempts: [{ id: "attempt-fixture", state: "unknown" }],
    effects: [
      {
        id: "unknown-effect-fixture",
        job_id: "experiment-fixture",
        action: "graphics.create",
        state: "unknown",
      },
    ],
    outcomes: [
      { opportunity_id: "opportunity-fixture", contribution_micro_usd: null },
    ],
    upcoming: [
      { kind: "discovery.research", state: "queued", due_at: 1793275200 },
    ],
    priorities: [{ service: "svg-graphic", delta: -2 }],
  };
}

test("discovery rendering escapes private evidence, retains exact costs and exposes bounded history", () => {
  const html = renderDiscoveryPage(
    explicitStatus("fixture-run"),
    "csrf-fixture",
    "fixture-run",
    {
      filename: "fixture.svg",
      media_type: "image/svg+xml",
      content: "<svg><script>untrusted()</script></svg>",
      checks: { scope: true },
    },
  );
  assert.match(html, /Fixture \/ test mode/);
  assert.match(html, /not verified real opportunities/);
  assert.match(html, /&lt;script&gt;untrusted\(\)&lt;\/script&gt;/);
  assert.match(html, /&lt;img src=x onerror=untrusted\(\)&gt;/);
  assert.doesNotMatch(html, /<script|<img|href="javascript:/i);
  assert.match(
    html,
    /href="https:\/\/fixture\.example\.test\/needs\?x=1&amp;y=2"/,
  );
  assert.match(html, /\$0\.001/);
  assert.match(html, /\$0\.25/);
  assert.match(html, /delta.*-2/s);
  assert.match(html, /Next history/);
  assert.match(html, /runId=fixture-run&amp;offset=2/);
  assert.match(html, /name="csrf" value="csrf-fixture"/);
  assert.match(html, /allowance_exhausted/);
  assert.match(html, /Attribute an existing order/);
  assert.match(html, /Reconcile original effect/);
  assert.match(html, /name="evidenceReference"/);
  assert.match(html, /Open private prototype/);
  assert.match(html, /Private prototype: fixture.svg/);
  assert.match(
    html,
    /&lt;svg&gt;&lt;script&gt;untrusted\(\)&lt;\/script&gt;&lt;\/svg&gt;/,
  );
  assert.doesNotMatch(html, /Stop further experiment work/);
  assert.match(html, /name="viewport"/);
  assert.match(html, /href="\/style.css"/);
  const paginated = explicitStatus("fixture-run");
  Object.assign(paginated.discoveries[0]!, {
    experiment_id: "outside-this-history-page",
  });
  paginated.experiments = [];
  const existing = renderDiscoveryPage(
    paginated,
    "csrf-fixture",
    "fixture-run",
  );
  assert.match(existing, /already has its single bounded experiment/);
  assert.doesNotMatch(existing, /Start one bounded experiment/);
});

test("discovery operator pages and commands inherit session, Origin and CSRF controls without provider execution", async () => {
  const f = fixture("foundation"),
    runId = f.store.create("foundation"),
    calls: { path: string; input: Record<string, unknown> }[] = [];
  let dueAt = Date.now() / 1000 + 7200;
  const upstream = createServer(async (request, response) => {
    assert.equal(request.headers.authorization, `Bearer ${"b".repeat(48)}`);
    let text = "";
    for await (const part of request) text += part;
    const input = JSON.parse(text) as Record<string, unknown>;
    assert.equal(input.runId, runId);
    calls.push({ path: request.url!, input });
    response.setHeader("Content-Type", "application/json");
    response.end(
      JSON.stringify(
        request.url === "/v1/discovery-status"
          ? explicitStatus(runId)
          : request.url === "/v1/public"
            ? { availability: { available: true }, offerings: [], activity: [] }
            : request.url === "/v1/work-state"
              ? {
                  eventId: "scheduled-discovery",
                  attempt: 0,
                  dueAt,
                  runtimeState: "active",
                }
              : {
                  saved: true,
                  scheduled: true,
                  experimentId: "experiment-fixture",
                },
      ),
    );
  });
  let server: ReturnType<typeof webServer> | undefined;
  try {
    await new Promise<void>((resolve) =>
      upstream.listen(0, "127.0.0.1", resolve),
    );
    const address = upstream.address();
    assert.ok(address && typeof address !== "string");
    const services = registerModules(
      {
        db: f.db,
        config: f.config,
        env: {
          WIDEMIND_BUSINESS_URL: `http://127.0.0.1:${address.port}`,
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
      events: [],
    };
    const selection = await services.business.selectWork(snapshot);
    assert.equal(selection.kind, "wait");
    if (selection.kind === "wait") {
      assert.ok(Date.parse(selection.wakeAt!) <= Date.now() + 30000);
      assert.ok(Date.parse(selection.wakeAt!) <= dueAt * 1000);
    }
    dueAt = Date.now() / 1000 - 1;
    const ready = await services.business.selectWork(snapshot);
    assert.equal(ready.kind, "work");
    if (ready.kind === "work")
      assert.equal(ready.candidate.limit.maximum.micros, "0");
    const direct = services.routes.find(
      (route) => route.path === discoveryCommandPath,
    )!;
    const beforeDirect = calls.length;
    assert.equal(
      (
        await direct.handle({
          body: {},
          query: {},
          headers: {},
          operator: false,
        })
      ).status,
      403,
    );
    assert.equal(calls.length, beforeDirect);
    server = webServer({
      ...f,
      services,
      secrets: {
        operatorPasswordHash: await hashPassword(
          "discovery-explicit-fixture-password",
        ),
      },
    });
    await new Promise<void>((resolve) =>
      server!.listen(0, "127.0.0.1", resolve),
    );
    const webAddress = server.address();
    assert.ok(webAddress && typeof webAddress !== "string");
    const origin = `http://127.0.0.1:${webAddress.port}`;
    f.config.publicOrigin = origin;
    const page = `${discoveryPagePath}?runId=${runId}`;
    const beforeUnauthorized = calls.length;
    assert.ok(
      [303, 401].includes(
        (await fetch(origin + page, { redirect: "manual" })).status,
      ),
    );
    assert.equal(
      (
        await fetch(
          origin + `/api/modules/business/discovery-status?runId=${runId}`,
        )
      ).status,
      401,
    );
    assert.equal(calls.length, beforeUnauthorized);
    const login = await fetch(origin + "/api/operator/login", {
      method: "POST",
      headers: { Origin: origin, "Content-Type": "application/json" },
      body: JSON.stringify({ password: "discovery-explicit-fixture-password" }),
    });
    assert.equal(login.status, 200);
    const cookie = login.headers.get("set-cookie")!.split(";")[0]!;
    const { csrf } = (await login.json()) as { csrf: string };
    const response = await fetch(origin + page, {
      headers: { Cookie: cookie },
    });
    assert.equal(response.status, 200);
    assert.equal(response.headers.get("cache-control"), "no-store");
    assert.match(await response.text(), /private-discovery-marker/);
    assert.doesNotMatch(
      await (await fetch(origin + "/")).text(),
      /private-discovery-marker/,
    );
    const post = (fields: Record<string, string>, requestOrigin = origin) =>
      fetch(origin + discoveryCommandPath, {
        method: "POST",
        redirect: "manual",
        headers: {
          Cookie: cookie,
          Origin: requestOrigin,
          "Content-Type": "application/x-www-form-urlencoded",
          Accept: "text/html",
        },
        body: new URLSearchParams(fields),
      });
    const fields = {
      runId,
      requestId: "fixture-configure",
      command: "configure",
      configJson: JSON.stringify({
        enabled: false,
        allowance_micro_usd: 0,
        markets: [],
      }),
    };
    const beforeRejected = calls.length;
    assert.equal((await post(fields)).status, 403);
    assert.equal(
      (await post({ ...fields, csrf }, "https://attacker.invalid")).status,
      403,
    );
    assert.equal(calls.length, beforeRejected);
    const accepted = await post({ ...fields, csrf });
    assert.equal(accepted.status, 303);
    assert.equal(
      new URL(accepted.headers.get("location")!, origin).pathname,
      discoveryPagePath,
    );
    assert.equal(calls.at(-1)!.path, "/v1/discovery-configure");
    assert.equal(calls.at(-1)!.input.csrf, undefined);
    assert.deepEqual(calls.at(-1)!.input.config, {
      enabled: false,
      allowance_micro_usd: 0,
      markets: [],
    });
    assert.equal(f.store.run(runId)!.desired, "pause");
    assert.equal(f.store.pendingEvents(runId).length, 1);
    const linked = await fetch(
      origin + "/api/modules/business/experiment-link-order",
      {
        method: "POST",
        headers: {
          Cookie: cookie,
          Origin: origin,
          "X-CSRF-Token": csrf,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          runId,
          experimentId: "experiment-fixture",
          orderId: "existing-real-order",
          evidenceReference: "controlled fixture attribution",
        }),
      },
    );
    assert.equal(linked.status, 200);
    assert.equal(calls.at(-1)!.path, "/v1/experiment-link-order");
    const invalidOffset = await fetch(
      origin +
        `/api/modules/business/discovery-status?runId=${runId}&offset=-1`,
      { headers: { Cookie: cookie } },
    );
    assert.equal(invalidOffset.status, 400);
    assert.ok(calls.every((item) => !/execute|\/step|reserve/.test(item.path)));
  } finally {
    if (server)
      await new Promise<void>((resolve) => server!.close(() => resolve()));
    await new Promise<void>((resolve) => upstream.close(() => resolve()));
    f.close();
  }
});

test("discovery pages remain absent from unconfigured and demo modules", () => {
  const demo = fixture("demo"),
    foundation = fixture("foundation");
  try {
    assert.deepEqual(
      definition.create({
        db: foundation.db,
        config: foundation.config,
        env: {},
      }),
      {},
    );
    assert.deepEqual(
      definition.create({
        db: demo.db,
        config: demo.config,
        env: {
          WIDEMIND_BUSINESS_URL: "http://127.0.0.1:8082",
          WIDEMIND_BUSINESS_TOKEN: "b".repeat(48),
        },
      }),
      {},
    );
  } finally {
    demo.close();
    foundation.close();
  }
});
