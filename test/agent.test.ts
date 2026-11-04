import test from "node:test";
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import type { AddressInfo } from "node:net";
import type { Json } from "../src/contracts/index.ts";
import {
  applyProfile,
  approveFirstPlan,
  currentProfile,
  latestPlanDecision,
  policyFromProfile,
  profileFromInput,
  proposeFirstPlan,
  saveAgent,
  sourceUrls,
  SANDBOX_OPEN,
} from "../src/runtime/agent.ts";
import { setupGuide } from "../src/runtime/setup.ts";
import { activityTimeline } from "../src/runtime/timeline.ts";
import { webServer } from "../src/web/server.ts";
import { hashPassword } from "../src/auth/password.ts";
import { fixture } from "./helpers.ts";

const form = {
  name: "Pixel & Proof",
  mission: "Small, useful graphics for local businesses.",
  offer_svg_graphic: "true",
};
const input = {
  ...form,
  "offer_svg-graphic": "true",
  "price_svg-graphic": "4.00",
};

test("profiles validate owner input with exact prices and $5 limits", () => {
  const profile = profileFromInput(input);
  assert.deepEqual(profile.offerings, { "svg-graphic": "4000000" });
  assert.equal(profile.experimentBudgetMicros, "1000000");
  assert.equal(profile.allowOutbound, false);
  assert.throws(() => profileFromInput({ ...input, name: " " }), /Name/);
  assert.throws(
    () => profileFromInput({ name: "A", mission: "B" }),
    /at least one offering/,
  );
  assert.throws(
    () => profileFromInput({ ...input, "price_svg-graphic": "0.50" }),
    /at least \$1.00/,
  );
  assert.throws(
    () => profileFromInput({ ...input, "price_svg-graphic": "1.005" }),
    /whole cents/,
  );
  assert.throws(() => profileFromInput({ ...input, maxJob: "6" }), /\$5.00/);
  assert.deepEqual(sourceUrls("https://a.example/x, https://b.example/y"), [
    "https://a.example/x",
    "https://b.example/y",
  ]);
  assert.throws(() => sourceUrls(""), /one to five/);
  assert.throws(() => sourceUrls("http://a.example"), /HTTPS/);
});

test("saving creates the configured run once, versions the profile and maps policy", async () => {
  const f = fixture();
  try {
    const runId = randomUUID();
    const first = saveAgent({
      db: f.db,
      config: f.config,
      runId,
      profile: profileFromInput(input),
    });
    assert.equal(first.created, true);
    assert.equal(f.store.run(runId)?.state, "paused");
    const second = saveAgent({
      db: f.db,
      config: f.config,
      runId,
      profile: profileFromInput({ ...input, name: "Renamed" }),
    });
    assert.equal(second.created, false);
    assert.equal(currentProfile(f.db, runId)?.name, "Renamed");
    assert.ok(second.profile.version > first.profile.version);
    assert.throws(
      () => f.db.exec("UPDATE agent_profiles SET document='{}'"),
      /append-only/,
    );

    const existing: Record<string, Json> = {
      goals: ["old"],
      conversation_enabled: true,
      max_open_orders: 7,
      conversation_price_floors: {
        "svg-graphic": 3000000,
        "research-brief": 1000000,
      },
    };
    const policy = policyFromProfile(second.profile, existing);
    assert.deepEqual(policy.conversation_price_floors, {
      "svg-graphic": 3000000,
    });
    assert.deepEqual(policy.goals, [
      "Small, useful graphics for local businesses.",
    ]);
    assert.deepEqual(policy.capabilities, ["graphics"]);
    assert.deepEqual(policy.prices, { "svg-graphic": 4000000 });
    assert.equal(policy.max_open_orders, 7);
    assert.equal(policy.allow_outbound, false);

    assert.equal(
      (await applyProfile(f.services, runId, second.profile)).applied,
      false,
    );
    let written: Record<string, Json> | null = null;
    f.services.business.readPolicy = async () => existing;
    f.services.business.writePolicy = async (_run, value) => {
      written = value;
    };
    assert.equal(
      (await applyProfile(f.services, runId, second.profile)).applied,
      true,
    );
    assert.equal(
      (written as unknown as Record<string, Json>).max_open_orders,
      7,
    );
  } finally {
    f.close();
  }
});

test("the first-hour plan reflects real availability and runs only approved items", async () => {
  const f = fixture();
  try {
    const runId = randomUUID();
    const profile = profileFromInput({
      ...input,
      "offer_research-brief": "true",
    });
    saveAgent({ db: f.db, config: f.config, runId, profile });
    f.services.website.publicContent = async () => ({
      offerings: [{ id: "svg-graphic", title: "SVG", description: "" }],
      activity: [],
      availability: { available: true },
    });
    // No economy route and no research tool: nothing can run yet.
    let plan = await proposeFirstPlan({
      services: f.services,
      profile,
      runState: "paused",
      funded: false,
    });
    const status = (id: string) => plan.items.find((item) => item.id === id);
    assert.deepEqual(
      plan.items.map((item) => item.id),
      ["fund", "start", "discovery"],
    );
    assert.match(status("fund")!.blocker!, /economy service/);
    assert.match(status("start")!.blocker!, /\$5.00 credit/);
    assert.match(status("discovery")!.blocker!, /research tool/);

    // Sandbox funding goes through the economy's own operator route.
    const opened: Json[] = [];
    f.services.routes.push({
      path: SANDBOX_OPEN,
      method: "POST",
      access: "operator",
      handle: async (request) => {
        opened.push(request.body);
        return { status: 200, body: {} };
      },
    });
    f.services.tools.capabilities = () => [
      { id: "research.collect", availability: { available: true } },
    ];
    plan = await proposeFirstPlan({
      services: f.services,
      profile,
      runState: "paused",
      funded: false,
    });
    assert.ok(plan.items.every((item) => item.available));

    const discovery: Record<string, Json>[] = [];
    f.services.business.health = () => ({ available: true });
    f.services.business.writeDiscovery = async (_run, value) => {
      discovery.push(value);
    };
    const results = await approveFirstPlan({
      db: f.db,
      services: f.services,
      runId,
      profile,
      plan,
      selected: ["fund", "discovery", "start"],
      sources: ["https://requests.example/graphics"],
    });
    assert.ok(
      results.every((item) => item.ok),
      JSON.stringify(results),
    );
    assert.deepEqual(opened, [
      { runId, mode: "sandbox", confirmOpeningCredit: true },
    ]);
    const market = (discovery[0]!.markets as Record<string, Json>[])[0]!;
    // The graphic offering is the one live now, so discovery targets it.
    assert.equal(market.service, "svg-graphic");
    assert.deepEqual(market.source_urls, ["https://requests.example/graphics"]);
    assert.equal(discovery[0]!.allowance_micro_usd, 500000);
    assert.notEqual(f.store.run(runId)?.state, "paused");
    assert.equal(latestPlanDecision(f.db, runId)?.results.length, 3);

    // Once funded and running, both steps show as done and cannot be re-run.
    plan = await proposeFirstPlan({
      services: f.services,
      profile,
      runState: "waiting",
      funded: true,
    });
    assert.equal(status("fund")?.done, true);
    assert.equal(status("fund")?.available, false);
    assert.equal(status("start")?.done, true);
  } finally {
    f.close();
  }
});

test("prices are capped so policy numbers stay exact", () => {
  assert.throws(
    () => profileFromInput({ ...input, "price_svg-graphic": "10000.01" }),
    /\$10,000.00/,
  );
  assert.equal(
    profileFromInput({ ...input, "price_svg-graphic": "10000" }).offerings[
      "svg-graphic"
    ],
    "10000000000",
  );
});

test("setup orders steps and the timeline speaks plainly", async () => {
  const f = fixture();
  try {
    const runId = randomUUID();
    const readiness = {
      version: 1 as const,
      observedAt: "",
      ready: false,
      checks: ["run_configured", "operator_auth", "economy_available"].map(
        (id) => ({ id, ok: true, detail: "ok" }),
      ),
    };
    let guide = setupGuide(f.db, null, readiness);
    assert.equal(guide.next?.id, "agent_profile");
    saveAgent({
      db: f.db,
      config: f.config,
      runId,
      profile: profileFromInput(input),
    });
    guide = setupGuide(f.db, runId, readiness);
    assert.equal(guide.next?.id, "tools_available");
    assert.equal(
      guide.steps.find((step) => step.id === "rehearsal_passed")?.done,
      false,
    );
    f.store.event(runId, "pilot.rehearsal.passed", "ok");
    assert.equal(
      setupGuide(f.db, runId, readiness).steps.find(
        (step) => step.id === "rehearsal_passed",
      )?.done,
      true,
    );

    f.store.control(runId, "resume");
    await f.runtime.tick();
    f.db
      .prepare(
        "INSERT INTO runtime_actions(id,run_id,work_key,state,action_json,receipt_json,attempts,created_at,updated_at) VALUES ('b1',?,'k','completed',?,?,1,?,?)",
      )
      .run(
        runId,
        JSON.stringify({
          tool: "business.step",
          limit: { maximum: { micros: "0" } },
        }),
        JSON.stringify({
          status: "succeeded",
          result: { eventKind: "offer" },
          usage: { actualCharge: { micros: "1500" } },
        }),
        Date.now() + 1000,
        Date.now() + 1000,
      );
    const timeline = activityTimeline(f.db, runId);
    const titles = timeline.entries.map((entry) => entry.title);
    assert.ok(titles.includes("Prepared and sent a quote"));
    assert.ok(titles.includes("Ran the demonstration task"));
    assert.ok(titles.includes("You created the agent"));
    assert.ok(titles.includes("You started the agent"));
    assert.equal(timeline.spentMicros, "1500");
    assert.ok(timeline.entries[0]!.at >= timeline.entries.at(-1)!.at);
  } finally {
    f.close();
  }
});

test("a new owner is sent to setup, creates the agent from the form and sees it publicly", async () => {
  const f = fixture();
  const runId = randomUUID();
  f.config.publicRunId = runId;
  const password = "fixture-only-long-password";
  const server = webServer({
    ...f,
    secrets: { operatorPasswordHash: await hashPassword(password) },
    env: {},
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const origin = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  f.config.publicOrigin = origin;
  try {
    const login = await fetch(origin + "/api/operator/login", {
      method: "POST",
      headers: { Origin: origin, "Content-Type": "application/json" },
      body: JSON.stringify({ password }),
    });
    const cookie = login.headers.get("set-cookie")!.split(";")[0]!;
    const { csrf } = (await login.json()) as { csrf: string };
    const first = await fetch(origin + "/operator", {
      headers: { Cookie: cookie },
      redirect: "manual",
    });
    assert.equal(first.status, 303);
    assert.equal(first.headers.get("location"), "/operator/start");
    const start = await (
      await fetch(origin + "/operator/start", { headers: { Cookie: cookie } })
    ).text();
    assert.match(start, /Let's set up your agent/);
    assert.match(start, /Create my agent/);
    const post = (
      path: string,
      fields: Record<string, string>,
      extra: Record<string, string> = {},
    ) =>
      fetch(origin + path, {
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
    assert.equal((await post("/operator/agent", input)).status, 403);
    assert.equal(f.store.run(runId), undefined);
    const bad = await post("/operator/agent", { ...input, csrf, name: "" });
    assert.equal(bad.status, 400);
    assert.match(await bad.text(), /Name is required/);
    const created = await post("/operator/agent", { ...input, csrf });
    assert.equal(created.status, 303);
    assert.equal(currentProfile(f.db, runId)?.name, "Pixel & Proof");
    assert.equal(
      (
        await fetch(origin + "/operator", {
          headers: { Cookie: cookie },
          redirect: "manual",
        })
      ).status,
      200,
    );

    const page = await (
      await fetch(origin + "/operator/start", { headers: { Cookie: cookie } })
    ).text();
    assert.match(page, /Pixel &amp; Proof/);
    assert.match(
      page,
      /Settings saved, but not active yet: The business service is not connected/,
    );
    assert.match(page, /Add \$5.00 of test credit/);
    assert.equal(
      (await post("/operator/agent/plan", { csrf, "item:start": "true" }))
        .status,
      400,
    );
    f.services.economy.summary = async () => ({
      currency: "USD",
      openingCreditMicros: "5000000",
      postedMicros: "5000000",
      reservedMicros: "0",
      holdsMicros: "0",
      spendableMicros: "5000000",
      customerReceiptsMicros: "0",
      earnedRevenueMicros: "0",
      operatingChargesMicros: "0",
      ownerFundingMicros: "0",
      subsidiesMicros: "0",
      mode: "sandbox",
      asOf: "2026-11-04T12:00:00Z",
    });
    assert.equal(
      (await post("/operator/agent/plan", { csrf, "item:start": "true" }))
        .status,
      303,
    );
    assert.notEqual(f.store.run(runId)?.state, "paused");

    const activity = await (
      await fetch(origin + "/operator/activity", {
        headers: { Cookie: cookie },
      })
    ).text();
    assert.match(activity, /You created the agent/);
    assert.match(activity, /You approved the first-hour plan/);
    const home = await (await fetch(origin + "/")).text();
    assert.match(home, /<h1>Pixel &amp; Proof<\/h1>/);
    assert.match(home, /Small, useful graphics/);
    assert.equal((await fetch(origin + "/operator/start")).status, 401);
  } finally {
    await new Promise<void>((resolve) => server.close(() => resolve()));
    f.close();
  }
});
