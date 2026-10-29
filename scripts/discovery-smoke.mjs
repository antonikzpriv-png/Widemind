/** Explicit fixture qualification of the real integrated discovery/runtime path. */
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createHash, randomBytes, randomUUID } from "node:crypto";
import { createServer as httpServer } from "node:http";
import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { createServer as netServer } from "node:net";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { setTimeout as delay } from "node:timers/promises";
import { bootstrap } from "../src/bootstrap.ts";
import { hashPassword } from "../src/auth/password.ts";
import { Store } from "../src/runtime/store.ts";
import { webServer } from "../src/web/server.ts";
import { browserSession } from "./browser-session.mjs";

const flags = process.argv.slice(2);
if (
  !flags.includes("--fixtures") ||
  flags.some((flag) => !["--fixtures", "--browser"].includes(flag))
) {
  console.error(
    "Usage: node scripts/discovery-smoke.mjs --fixtures [--browser]",
  );
  console.error(
    "Local source fixtures only; this does not verify a real market, customer, payment or profit.",
  );
  process.exit(2);
}

process.umask(0o077);
const directory = await mkdtemp(join(tmpdir(), "widemind-discovery-"));
const output = resolve("var/discovery-smoke");
const children = [];
let app, server, browser, source;
let logs = "";
let reads = 0;
const fixtureNeed = "Small teams need a graphic for their weekly release notes";
const fixtureText = `${fixtureNeed}. This page is an explicit local qualification fixture, not a real customer or market observation.`;
const fixtureBody = `<html><title>Explicit fixture market</title><main><p>${fixtureText}</p></main></html>`;

async function unusedPort() {
  const socket = netServer();
  await new Promise((resolve) => socket.listen(0, "127.0.0.1", resolve));
  const port = socket.address().port;
  await new Promise((resolve) => socket.close(resolve));
  return port;
}

async function waitFor(check, label, timeout = 60000) {
  const until = Date.now() + timeout;
  while (Date.now() < until) {
    if (await check()) return;
    await delay(300);
  }
  throw new Error("Timed out: " + label);
}

async function stop(child) {
  if (!child || child.exitCode !== null || child.signalCode !== null) return;
  const exited = new Promise((resolve) => child.once("exit", resolve));
  child.kill("SIGTERM");
  const timer = setTimeout(() => child.kill("SIGKILL"), 3000);
  try {
    await exited;
  } finally {
    clearTimeout(timer);
  }
}

function readBusiness(sql, ...arguments_) {
  const db = new DatabaseSync(join(directory, "business.sqlite"), {
    readOnly: true,
  });
  try {
    return db.prepare(sql).all(...arguments_);
  } finally {
    db.close();
  }
}

try {
  const [web, economy, tools, business] = await Promise.all(
    Array.from({ length: 4 }, unusedPort),
  );
  source = httpServer((request, response) => {
    if (request.url !== "/market") {
      response.writeHead(404).end();
      return;
    }
    reads++;
    response.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
    response.end(fixtureBody);
  });
  await new Promise((resolve) => source.listen(0, "127.0.0.1", resolve));
  const sourceOrigin = `http://127.0.0.1:${source.address().port}`;
  const token = () => randomBytes(32).toString("hex");
  const runId = randomUUID();
  const password = token();
  const origin = `http://127.0.0.1:${web}`;
  const env = {
    ...process.env,
    WIDEMIND_PROFILE: "foundation",
    WIDEMIND_DATABASE: join(directory, "runtime.sqlite"),
    WIDEMIND_RUNTIME_DATABASE: join(directory, "runtime.sqlite"),
    WIDEMIND_ECONOMY_DB: join(directory, "economy.sqlite"),
    WIDEMIND_BUSINESS_DB: join(directory, "business.sqlite"),
    WIDEMIND_TOOLS_STATE: join(directory, "tools/control"),
    WIDEMIND_TOOLS_WORKSPACE: join(directory, "tools/workspace"),
    WIDEMIND_TOOLS_CONFIG: join(directory, "owner.json"),
    WIDEMIND_COMMERCE_RUN_ID: runId,
    WIDEMIND_BUSINESS_MODE: "test",
    WIDEMIND_PAYMENT_PROVIDER: "paypal",
    WIDEMIND_PAYPAL_MODE: "test",
    WIDEMIND_LEASE_MS: "3000",
    WIDEMIND_IDLE_MS: "1000",
    WIDEMIND_HOST: "127.0.0.1",
    WIDEMIND_PORT: String(web),
    WIDEMIND_PUBLIC_ORIGIN: origin,
    WIDEMIND_SECURE_COOKIES: "false",
    WIDEMIND_OPERATOR_PASSWORD_HASH: await hashPassword(password),
    WIDEMIND_ECONOMY_URL: `http://127.0.0.1:${economy}`,
    WIDEMIND_TOOLS_URL: `http://127.0.0.1:${tools}`,
    WIDEMIND_BUSINESS_URL: `http://127.0.0.1:${business}`,
    WIDEMIND_ECONOMY_OPERATOR_TOKEN: token(),
    WIDEMIND_ECONOMY_GATEWAY_TOKEN: token(),
    WIDEMIND_ECONOMY_AGENT_TOKEN: token(),
    WIDEMIND_TOOLS_TOKEN: token(),
    WIDEMIND_BUSINESS_TOKEN: token(),
  };
  // No provider credential from the calling environment can turn this fixture
  // command into a real checkout, model request, email or publishing operation.
  for (const name of Object.keys(env))
    if (
      /^(WIDEMIND_(?:STRIPE|PAYPAL)_(?:API_KEY|WEBHOOK_SECRET|ACCOUNT|CLIENT_ID|CLIENT_SECRET|MERCHANT_ID|WEBHOOK_ID|API_BASE|ALLOW_TEST_ENDPOINT)|WIDEMIND_ENABLE_LIVE_EFFECTS|WIDEMIND_BUSINESS_ENABLED|WIDEMIND_BUSINESS_TOOLS_PROFILE|WIDEMIND_OPENAI_API_KEY)$/.test(
        name,
      )
    )
      delete env[name];
  delete env.PYTHONPATH;
  delete env.PYTHONHOME;
  await writeFile(
    env.WIDEMIND_TOOLS_CONFIG,
    JSON.stringify({
      control_dir: env.WIDEMIND_TOOLS_STATE,
      workspace_dir: env.WIDEMIND_TOOLS_WORKSPACE,
      policy: {
        profile: "sandbox",
        allow_external_effects: true,
        allowed_email_recipients: [],
        network: { trusted_fixture_origins: [sourceOrigin] },
      },
      tools: {
        "research.fetch": {
          enabled: true,
          trusted_fixture_origins: [sourceOrigin],
          allowed_hosts: [],
          max_text_chars: 12000,
          max_links: 0,
        },
        files: { enabled: true },
        graphics: { enabled: true },
        "graphics.create": { enabled: true },
      },
    }),
    { mode: 0o600 },
  );
  Object.assign(process.env, env);
  // Remove inherited opt-ins in this process too; bootstrap uses explicit env
  // and the actual worker/service children receive the same isolated selectors.
  app = await bootstrap(env);
  const store = new Store(app.db);
  store.create("foundation", Date.now(), runId);
  const launch = (args) => {
    const child = spawn(process.execPath, args, {
      env,
      stdio: ["ignore", "pipe", "pipe"],
    });
    child.stdout.on("data", (data) => {
      logs = (logs + data).slice(-24000);
    });
    child.stderr.on("data", (data) => {
      logs = (logs + data).slice(-24000);
    });
    children.push(child);
    return child;
  };
  const services = launch(["scripts/services.mjs", "start"]);
  await waitFor(async () => {
    if (services.exitCode !== null || services.signalCode !== null)
      throw new Error("Isolated service launcher exited before readiness");
    try {
      return (
        await fetch(env.WIDEMIND_BUSINESS_URL + "/health", {
          headers: { Authorization: "Bearer " + env.WIDEMIND_BUSINESS_TOKEN },
        })
      ).ok;
    } catch {
      return false;
    }
  }, "isolated services");
  const economyRequest = async (path, body) => {
    const response = await fetch(env.WIDEMIND_ECONOMY_URL + path, {
      method: body ? "POST" : "GET",
      headers: {
        Authorization: "Bearer " + env.WIDEMIND_ECONOMY_OPERATOR_TOKEN,
        "Content-Type": "application/json",
      },
      ...(body ? { body: JSON.stringify(body) } : {}),
    });
    const value = await response.json();
    assert.ok(response.ok, `Economy ${path}: ${JSON.stringify(value)}`);
    return value;
  };
  await economyRequest("/v1/runs", { run_id: runId, mode: "test" });
  server = webServer(app);
  await new Promise((resolve) => server.listen(web, "127.0.0.1", resolve));
  const post = async (path, body, headers = {}) => {
    const response = await fetch(origin + path, {
      method: "POST",
      headers: {
        Origin: origin,
        "Content-Type": "application/json",
        ...headers,
      },
      body: JSON.stringify(body),
    });
    const value = await response.json();
    assert.ok(response.ok, `${path}: ${JSON.stringify(value)}`);
    return { response, value };
  };
  const login = await post("/api/operator/login", { password });
  const headers = {
    Cookie: login.response.headers.get("set-cookie").split(";")[0],
    "x-csrf-token": login.value.csrf,
  };
  const command = async (name, body) =>
    (await post(`/api/modules/business/${name}`, { runId, ...body }, headers))
      .value;
  const status = async () => {
    const response = await fetch(
      `${origin}/api/modules/business/discovery-status?runId=${runId}`,
      { headers },
    );
    const value = await response.json();
    assert.ok(response.ok, "Discovery status unavailable");
    return value;
  };
  const config = {
    enabled: true,
    fixture_mode: true,
    allowance_micro_usd: 110000,
    adaptation_limit: 5,
    adaptation_min_samples: 2,
    markets: [
      {
        id: "explicit-fixture-market",
        label: "Explicit local market fixture",
        need_terms: ["need a graphic"],
        source_urls: [sourceOrigin + "/market"],
        service: "svg-graphic",
        scope: {
          title: "Explicit fixture prototype",
          description: "Actual bounded SVG; no customer or sale claimed",
        },
        kind: "experiment",
        proposed_offering: "A release-notes graphic prototype",
        research_allowance_micro_usd: 10000,
        interval_seconds: 300,
        freshness_seconds: 3600,
        cooldown_seconds: 300,
        max_attempts: 1,
        priority: 20,
      },
    ],
  };
  await post(`/api/operator/runs/${runId}/resume`, {}, headers);
  await command("discovery-configure", { config });
  let worker = launch(["src/runtime/main.ts"]);
  console.log(
    "DISCOVERY fixture source -> budget-authorized gateway research -> opportunity",
  );
  let discovered;
  await waitFor(async () => {
    const value = await status();
    discovered = value.discoveries?.[0];
    if (
      !discovered &&
      value.attempts?.some((attempt) =>
        ["failed", "unknown"].includes(attempt.state),
      )
    )
      throw new Error(
        "Discovery attempt needs operator review; inspect the bounded tool receipt",
      );
    return !!discovered;
  }, "source-backed discovery");
  assert.equal(reads, 1, "exactly one bounded source request");
  assert.equal(readBusiness("SELECT COUNT(*) AS n FROM customers")[0].n, 0);
  assert.equal(readBusiness("SELECT COUNT(*) AS n FROM orders")[0].n, 0);
  const opportunities = readBusiness(
    "SELECT id,customer_id,source_reference FROM opportunities",
  );
  assert.equal(opportunities.length, 1);
  assert.equal(opportunities[0].customer_id, null);
  assert.equal(opportunities[0].source_reference, sourceOrigin + "/market");
  assert.equal(discovered.evidence.url, sourceOrigin + "/market");
  assert.equal(
    discovered.evidence.source_id,
    "sha256:" + createHash("sha256").update(fixtureBody).digest("hex"),
  );
  assert.equal(discovered.evidence.fixture, true);
  assert.ok(discovered.evidence.retrieved_at <= Date.now() / 1000 + 5);
  assert.ok(discovered.evidence.retrieved_at > Date.now() / 1000 - 60);
  const opportunityId = opportunities[0].id;
  assert.ok(JSON.stringify(discovered).includes("need a graphic"));

  await stop(worker);
  console.log(
    "DISCOVERY queued experiment -> independent worker restart -> one actual prototype",
  );
  const deadline = Date.now() / 1000 + 25;
  const spec = {
    hypothesis:
      "A small release-notes sample could attract a future paid request; this fixture has no real market validation.",
    allowance_micro_usd: 100000,
    deadline,
    success_criteria: {
      artifact_valid: true,
      min_conversions: 1,
      min_contribution_micro_usd: 0,
    },
    stop_conditions: [
      "deadline",
      "allowance_exhausted",
      "single_attempt",
      "scope_failure",
      "operator_stop",
    ],
  };
  const started = await command("experiment-start", {
    opportunityId,
    spec,
    requestId: "fixture-experiment-1",
  });
  const replay = await command("experiment-start", {
    opportunityId,
    spec,
    requestId: "fixture-experiment-1",
  });
  assert.deepEqual(replay, started, "duplicate explicit start is idempotent");
  worker = launch(["src/runtime/main.ts"]);
  let experiments;
  await waitFor(async () => {
    experiments = (await status()).experiments;
    return (
      readBusiness(
        "SELECT id FROM effects WHERE provider='tools' AND action='graphics.create' AND state='succeeded'",
      ).length === 1
    );
  }, "one actual sample creation");
  assert.equal(reads, 1);
  assert.equal(readBusiness("SELECT COUNT(*) AS n FROM customers")[0].n, 0);
  assert.equal(readBusiness("SELECT COUNT(*) AS n FROM quotes")[0].n, 0);
  assert.equal(readBusiness("SELECT COUNT(*) AS n FROM orders")[0].n, 0);
  const sample = readBusiness(
    "SELECT result FROM effects WHERE provider='tools' AND action='graphics.create'",
  )[0];
  const artifact = JSON.parse(sample.result);
  assert.ok(artifact.content.includes("</svg>"));
  const artifactPath = resolve(
    env.WIDEMIND_TOOLS_WORKSPACE,
    artifact.artifact.path,
  );
  assert.ok(artifactPath.startsWith(env.WIDEMIND_TOOLS_WORKSPACE + "/"));
  assert.equal(await readFile(artifactPath, "utf8"), artifact.content);
  await stop(worker);
  worker = launch(["src/runtime/main.ts"]);
  console.log(
    "DISCOVERY bounded experiment stays terminal through its deadline without repeating spending",
  );
  await waitFor(
    async () => {
      const value = await status();
      experiments = value.experiments;
      return (
        Date.now() / 1000 >= deadline + 1 &&
        experiments?.some((experiment) =>
          [
            "stopped",
            "expired",
            "completed",
            "failed",
            "deadline",
            "ended",
          ].includes(experiment.status ?? experiment.state),
        )
      );
    },
    "experiment deadline",
    60000,
  );
  assert.equal(
    readBusiness(
      "SELECT COUNT(*) AS n FROM effects WHERE provider='tools' AND action='graphics.create'",
    )[0].n,
    1,
  );
  assert.equal(reads, 1);

  console.log("DISCOVERY lifetime allowance exhaustion stays free");
  await command("discovery-configure", {
    config: {
      ...config,
      markets: [
        config.markets[0],
        { ...config.markets[0], id: "fixture-no-budget", max_attempts: 2 },
      ],
    },
  });
  let exhausted;
  await waitFor(async () => {
    exhausted = await status();
    return readBusiness(
      "SELECT state FROM discovery_markets WHERE market_id='fixture-no-budget'",
    ).some((market) => market.state === "budget_exhausted");
  }, "lifetime allowance exhaustion");
  await delay(1200);
  assert.equal(reads, 1, "exhausted research cannot fetch again");
  const privatePage = `/operator/modules/business/discovery?runId=${runId}`;
  const anonymous = await fetch(origin + privatePage);
  assert.equal(anonymous.status, 401);
  assert.ok(!(await anonymous.text()).includes(fixtureNeed));
  const publicResponse = await fetch(origin + "/api/public");
  const publicText = await publicResponse.text();
  assert.ok(!publicText.includes("explicit-fixture-market"));
  assert.ok(!publicText.includes("fixture-experiment-1"));
  assert.ok(!publicText.includes("need a graphic"));
  const forbidden = await fetch(
    `${origin}/api/modules/business/discovery-status?runId=${runId}`,
  );
  assert.equal(forbidden.status, 401);

  if (flags.includes("--browser")) {
    browser = await browserSession(directory, output);
    await browser.navigate(origin + "/operator");
    await browser.fill('input[name="password"]', password);
    await browser.click('form[action="/operator/login"] button');
    await browser.waitFor(() =>
      browser.evaluate("document.body.textContent.includes('Run control')"),
    );
    for (const width of [1280, 390, 320]) {
      await browser.viewport(width, 900, width < 500);
      await browser.navigate(origin + privatePage);
      assert.equal(
        await browser.evaluate(
          "document.documentElement.scrollWidth <= innerWidth",
        ),
        true,
      );
      assert.equal(
        await browser.evaluate(
          "document.body.textContent.includes('need a graphic')",
        ),
        true,
      );
      assert.equal(
        await browser.evaluate("document.querySelectorAll('script').length"),
        0,
      );
      await browser.screenshot("discovery-" + width);
    }
    assert.deepEqual(browser.errors, []);
  }
  const balance = await economyRequest(`/v1/runs/${runId}/balance`);
  assert.equal(balance.posted_micro_usd, 5000000);
  const charges = await economyRequest(`/v1/runs/${runId}/charges`);
  const report = {
    passed: true,
    mode: "explicit local fixtures",
    realOpportunityVerified: false,
    realCustomerPaymentOrProfit: false,
    actualServices: ["economy", "tools", "business"],
    independentPersistentWorker: true,
    workerRestarts: 2,
    authorizedResearchRequests: reads,
    sourceEvidence:
      "Exact source URL, full-body SHA256 and gateway retrieval timestamp verified",
    discoveredOpportunities: opportunities.length,
    inventedCustomers: 0,
    inventedOrders: 0,
    actualPrototype: "SVG file through the authorized existing tools gateway",
    prototypeEffects: 1,
    idempotentExperimentStart: true,
    deadlineStopsWithoutRepeatedSpending: true,
    lifetimeAllowanceStopsResearch: true,
    publicPrivacy: true,
    browserViewports: flags.includes("--browser") ? [1280, 390, 320] : [],
    browserErrors: browser?.errors ?? [],
    infrastructureCosts:
      "Owner-subsidized local compute/network/storage; not profit",
    balance,
    charges,
    experiments,
  };
  await mkdir(output, { recursive: true });
  await writeFile(
    join(output, "report.json"),
    JSON.stringify(report, null, 2) + "\n",
  );
  console.log(JSON.stringify(report));
} catch (error) {
  console.error(error.message);
  if (app) {
    console.error(
      "Runtime state",
      JSON.stringify(
        app.db
          .prepare("SELECT state,reason,last_error FROM runtime_runs")
          .all(),
      ),
    );
    console.error(
      "Runtime actions",
      JSON.stringify(
        app.db
          .prepare("SELECT state,reason,attempts FROM runtime_actions")
          .all(),
      ),
    );
  }
  for (const sql of [
    "SELECT kind,state,error FROM events",
    "SELECT provider,action,state,reason FROM effects",
  ]) {
    try {
      console.error(sql, JSON.stringify(readBusiness(sql)));
    } catch {}
  }
  console.error(logs.slice(-14000));
  process.exitCode = 1;
} finally {
  await browser?.close();
  await Promise.all(children.reverse().map(stop));
  if (server) await new Promise((resolve) => server.close(resolve));
  app?.db.close();
  if (source) await new Promise((resolve) => source.close(resolve));
  await rm(directory, { recursive: true, force: true });
}
