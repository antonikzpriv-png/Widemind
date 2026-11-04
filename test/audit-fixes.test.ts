import test from "node:test";
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { createServer } from "node:http";
import type { AddressInfo } from "node:net";
import { pilotReadiness, pilotReport } from "../src/runtime/pilot.ts";
import { profileFromInput, saveAgent } from "../src/runtime/agent.ts";
import { activityTimeline } from "../src/runtime/timeline.ts";
import { webServer } from "../src/web/server.ts";
import { hashPassword } from "../src/auth/password.ts";
import { fixture } from "./helpers.ts";

const listen = async (server: ReturnType<typeof createServer>) => {
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  return `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
};

test("setup marks a configured service unreachable instead of trusting static health", async () => {
  const f = fixture();
  const up = createServer((req, res) => {
    res
      .writeHead(req.headers.authorization === "Bearer tools-token" ? 200 : 401)
      .end();
  });
  const upUrl = await listen(up);
  try {
    const readiness = await pilotReadiness({
      env: {
        WIDEMIND_ECONOMY_URL: "http://127.0.0.1:9", // nothing listens here
        WIDEMIND_TOOLS_URL: upUrl,
        WIDEMIND_TOOLS_TOKEN: "tools-token",
      },
      config: f.config,
      operatorPasswordConfigured: true,
      services: f.services,
      db: f.db,
      runId: null,
    });
    const check = (id: string) =>
      readiness.checks.find((item) => item.id === id)!;
    assert.equal(check("economy_available").ok, false);
    assert.match(check("economy_available").detail, /not reachable/);
    assert.equal(check("tools_available").ok, true);
    assert.match(check("tools_available").detail, /reachable/);
  } finally {
    await new Promise<void>((resolve) => up.close(() => resolve()));
    f.close();
  }
});

test("owner setting changes and plan approvals count as interventions", async () => {
  const f = fixture();
  try {
    const runId = randomUUID();
    const profile = profileFromInput({
      name: "A",
      mission: "B",
      "offer_svg-graphic": "true",
    });
    saveAgent({ db: f.db, config: f.config, runId, profile });
    saveAgent({
      db: f.db,
      config: f.config,
      runId,
      profile: { ...profile, name: "C" },
    });
    f.store.event(runId, "agent.plan.approved", "x");
    f.store.control(runId, "resume");
    const report = await pilotReport({
      db: f.db,
      services: f.services,
      runId,
      view: "public",
    });
    assert.deepEqual(report.interventions.byKind, {
      "agent.updated": 1,
      "agent.plan.approved": 1,
      "operator.resume": 1,
    });
  } finally {
    f.close();
  }
});

test("timeline totals spending across all actions but lists only the newest", () => {
  const f = fixture();
  try {
    const runId = f.store.create("demo");
    const insert = f.db.prepare(
      "INSERT INTO runtime_actions(id,run_id,work_key,state,action_json,receipt_json,attempts,created_at,updated_at) VALUES (?,?,?,?,?,?,1,?,?)",
    );
    for (let i = 0; i < 30; i++)
      insert.run(
        `a${i}`,
        runId,
        `k${i}`,
        i === 0 ? "unresolved" : "completed",
        JSON.stringify({ tool: "graphics.create" }),
        JSON.stringify({
          status: "succeeded",
          usage: { actualCharge: { micros: "1000" } },
        }),
        i,
        i,
      );
    const timeline = activityTimeline(f.db, runId, 10);
    assert.equal(timeline.entries.length, 10);
    assert.equal(timeline.spentMicros, "30000");
    assert.equal(timeline.now.unresolved, 1);
  } finally {
    f.close();
  }
});

test("sign-in form errors render the page, the font is served, and every page shares the layout", async () => {
  const f = fixture();
  const server = webServer({
    ...f,
    secrets: { operatorPasswordHash: await hashPassword("right-password-123") },
    env: {},
  });
  const origin = await listen(server);
  f.config.publicOrigin = origin;
  try {
    const wrong = await fetch(origin + "/operator/login", {
      method: "POST",
      headers: {
        Origin: origin,
        "Content-Type": "application/x-www-form-urlencoded",
      },
      body: "password=nope",
    });
    assert.equal(wrong.status, 401);
    assert.match(wrong.headers.get("content-type") ?? "", /text\/html/);
    assert.match(await wrong.text(), /didn&#39;t match/);
    const api = await fetch(origin + "/api/operator/login", {
      method: "POST",
      headers: { Origin: origin, "Content-Type": "application/json" },
      body: JSON.stringify({ password: "nope" }),
    });
    assert.deepEqual(await api.json(), { error: "Invalid credentials" });

    const font = await fetch(origin + "/fonts/geist-variable.woff2");
    assert.equal(font.status, 200);
    assert.equal(font.headers.get("content-type"), "font/woff2");
    assert.ok((await font.arrayBuffer()).byteLength > 10_000);
    assert.match(
      font.headers.get("content-security-policy") ?? "",
      /font-src 'self'/,
    );
    const css = await (await fetch(origin + "/style.css")).text();
    assert.match(css, /\/fonts\/geist-variable\.woff2/);

    for (const path of ["/", "/commerce", "/pilot", "/operator"]) {
      const html = await (await fetch(origin + path)).text();
      assert.match(html, /class="brand"><span class="mark"/, path);
      assert.match(html, /href="\/pilot">Pilot</, path);
      assert.doesNotMatch(html, / style="/, path);
    }
    assert.match(
      await (await fetch(origin + "/commerce")).text(),
      /<script src="\/commerce\.js" defer>/,
    );
  } finally {
    await new Promise<void>((resolve) => server.close(() => resolve()));
    f.close();
  }
});
