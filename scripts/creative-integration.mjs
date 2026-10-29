/** Real integrated services/worker and Docker. PayPal and model HTTP are explicit local fixtures. */
import assert from "node:assert/strict";
import { createHash, randomBytes, randomUUID } from "node:crypto";
import { spawn } from "node:child_process";
import { DatabaseSync } from "node:sqlite";
import { createServer as httpServer } from "node:http";
import { createServer } from "node:net";
import { mkdtemp, mkdir, readFile, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { setTimeout as delay } from "node:timers/promises";
import { paypalFixture } from "../test/fixtures/paypal-server.mjs";
import { bootstrap } from "../src/bootstrap.ts";
import { Store } from "../src/runtime/store.ts";
import { webServer } from "../src/web/server.ts";
import { hashPassword } from "../src/auth/password.ts";
import { browserSession } from "./browser-session.mjs";

const image = process.env.WIDEMIND_CREATIVE_IMAGE;
if (!/^sha256:[a-f0-9]{64}$/.test(image ?? ""))
  throw new Error(
    "Build the creative sandbox and set WIDEMIND_CREATIVE_IMAGE to its immutable local image ID.",
  );
const directory = await mkdtemp(
    join(tmpdir(), "widemind-creative-integration-"),
  ),
  output = resolve("var/creative-integration");
await mkdir(output, { recursive: true });
const graphicScope = JSON.parse(
  await readFile(
    "services/tools/examples/creative-graphics/brief.json",
    "utf8",
  ),
);
const graphicDesign = JSON.parse(
  await readFile(
    "services/tools/examples/creative-graphics/design.json",
    "utf8",
  ),
);
const calculatorScope = JSON.parse(
  await readFile(
    "examples/creative/software/project-estimator/scope.json",
    "utf8",
  ),
);
calculatorScope.requirements = [
  "Calculate the agreed results",
  "Update results when an input changes",
  "Allow resetting inputs to their starting values",
  "Reject invalid or out-of-range inputs",
  "Work on narrow screens",
  "Support keyboard entry",
];
const calculatorDesign = JSON.parse(
  await readFile(
    "examples/creative/software/project-estimator/design.json",
    "utf8",
  ),
);
const modelCalls = [],
  modelReplies = new Map();
const models = httpServer(async (req, res) => {
  let raw = "";
  for await (const chunk of req) {
    raw += chunk;
    if (raw.length > 100000) {
      res.writeHead(413).end();
      return;
    }
  }
  assert.equal(req.headers.authorization, "Bearer synthetic-model-key");
  assert.equal(req.url, "/v1/responses");
  const key = req.headers["x-client-request-id"];
  assert.match(key ?? "", /^[a-f0-9]{64}$/);
  if (!modelReplies.has(key)) {
    const body = JSON.parse(raw),
      request = JSON.parse(body.input),
      scope = request.accepted_scope;
    modelCalls.push({
      title: scope.title,
      feedback: !!request.customer_feedback_within_existing_scope,
    });
    let design = structuredClone(
      scope.category === "calculator" ? calculatorDesign : graphicDesign,
    );
    if (
      scope.category === "calculator" &&
      modelCalls.filter((c) => c.title === scope.title).length === 1
    )
      design.outputs[0].expression = {
        op: "add",
        args: [{ input: "hours" }, { input: "rate" }],
      }; // Must fail actual independent numerical checks.
    if (request.customer_feedback_within_existing_scope && scope.width)
      design.elements.find((e) => e.type === "circle").cx += 12;
    modelReplies.set(key, {
      id: "resp_synthetic_" + modelCalls.length,
      object: "response",
      model: "synthetic-design-model",
      status: "completed",
      output: [
        {
          type: "message",
          role: "assistant",
          content: [{ type: "output_text", text: JSON.stringify(design) }],
        },
      ],
      usage: { input_tokens: 20, output_tokens: 15, total_tokens: 35 },
    });
  }
  res.writeHead(200, {
    "Content-Type": "application/json",
    "x-request-id": "synthetic-model-" + modelReplies.size,
  });
  res.end(JSON.stringify(modelReplies.get(key)));
});
await new Promise((resolve) => models.listen(0, "127.0.0.1", resolve));
const provider = await paypalFixture(),
  children = [];
let app,
  server,
  browser,
  worker,
  fixtureEnv,
  failed = false,
  logs = "";
async function port() {
  const s = createServer();
  await new Promise((r) => s.listen(0, "127.0.0.1", r));
  const n = s.address().port;
  await new Promise((r) => s.close(r));
  return n;
}
async function waitFor(fn, label, timeout = 90000) {
  const until = Date.now() + timeout;
  while (Date.now() < until) {
    if (await fn()) return;
    try {
      const db = new DatabaseSync(join(directory, "business.sqlite"), {
        readOnly: true,
      });
      const unknown = db
        .prepare("SELECT id FROM effects WHERE state='unknown' LIMIT 1")
        .get();
      const declined = db
        .prepare(
          "SELECT service,assessment FROM opportunities WHERE status='needs_review' LIMIT 1",
        )
        .get();
      db.close();
      if (declined)
        throw new Error("Opportunity unavailable: " + JSON.stringify(declined));
      if (unknown)
        throw new Error("An inner effect needs reconciliation: " + unknown.id);
    } catch (error) {
      if (/^(An inner effect|Opportunity unavailable)/.test(error.message))
        throw error;
    }
    await delay(1200);
  }
  throw new Error("Creative integration timed out: " + label);
}
async function stop(child) {
  if (!child || child.exitCode !== null || child.signalCode !== null) return;
  const done = new Promise((r) => child.once("exit", r));
  child.kill("SIGTERM");
  const t = setTimeout(() => child.kill("SIGKILL"), 3000);
  await done;
  clearTimeout(t);
}
try {
  const [web, economy, tools, business] = await Promise.all([
      port(),
      port(),
      port(),
      port(),
    ]),
    token = () => randomBytes(32).toString("hex"),
    runId = randomUUID(),
    origin = "http://127.0.0.1:" + web;
  const env = {
    ...process.env,
    WIDEMIND_DATABASE: join(directory, "runtime.sqlite"),
    WIDEMIND_RUNTIME_DATABASE: join(directory, "runtime.sqlite"),
    WIDEMIND_ECONOMY_DB: join(directory, "economy.sqlite"),
    WIDEMIND_BUSINESS_DB: join(directory, "business.sqlite"),
    WIDEMIND_TOOLS_STATE: join(directory, "tools/control"),
    WIDEMIND_TOOLS_WORKSPACE: join(directory, "tools/workspace"),
    WIDEMIND_PROFILE: "foundation",
    WIDEMIND_LEASE_MS: "3000",
    WIDEMIND_IDLE_MS: "1000",
    WIDEMIND_DEPENDENCY_MS: "30000",
    WIDEMIND_HOST: "127.0.0.1",
    WIDEMIND_PORT: String(web),
    WIDEMIND_PUBLIC_ORIGIN: origin,
    WIDEMIND_SECURE_COOKIES: "false",
    WIDEMIND_OPERATOR_PASSWORD_HASH: await hashPassword(token()),
    WIDEMIND_ECONOMY_URL: "http://127.0.0.1:" + economy,
    WIDEMIND_TOOLS_URL: "http://127.0.0.1:" + tools,
    WIDEMIND_BUSINESS_URL: "http://127.0.0.1:" + business,
    WIDEMIND_ECONOMY_OPERATOR_TOKEN: token(),
    WIDEMIND_ECONOMY_GATEWAY_TOKEN: token(),
    WIDEMIND_ECONOMY_AGENT_TOKEN: token(),
    WIDEMIND_TOOLS_TOKEN: token(),
    WIDEMIND_BUSINESS_TOKEN: token(),
    WIDEMIND_COMMERCE_RUN_ID: runId,
    WIDEMIND_BUSINESS_MODE: "test",
    WIDEMIND_PAYMENT_PROVIDER: "paypal",
    WIDEMIND_RECONCILE_POLL_SECONDS: "1",
    WIDEMIND_PAYPAL_MODE: "test",
    WIDEMIND_PAYPAL_CLIENT_ID: "fixture-client",
    WIDEMIND_PAYPAL_CLIENT_SECRET: "fixture-secret",
    WIDEMIND_PAYPAL_MERCHANT_ID: "FIXTURE_MERCHANT",
    WIDEMIND_PAYPAL_WEBHOOK_ID: "FIXTURE_WEBHOOK",
    WIDEMIND_CHECKOUT_SUCCESS_URL: origin + "/commerce",
    WIDEMIND_CHECKOUT_CANCEL_URL: origin + "/commerce",
    WIDEMIND_PAYPAL_API_BASE: provider.url,
    WIDEMIND_PAYPAL_ALLOW_TEST_ENDPOINT: "true",
    WIDEMIND_CREATIVE_FIXTURE_KEY: "synthetic-model-key",
    WIDEMIND_TOOLS_CONFIG: join(directory, "owner.json"),
  };
  // This file is disposable fixture configuration, not a provider credential.
  await writeFile(
    env.WIDEMIND_TOOLS_CONFIG,
    JSON.stringify({
      control_dir: env.WIDEMIND_TOOLS_STATE,
      workspace_dir: env.WIDEMIND_TOOLS_WORKSPACE,
      policy: { profile: "sandbox", allow_external_effects: true },
      secret_environment: {
        "fixture-model-key": "WIDEMIND_CREATIVE_FIXTURE_KEY",
      },
      tools: {
        files: { enabled: true },
        graphics: { enabled: true },
        "graphics.create": { enabled: true },
        "software.create": { enabled: true },
        "creative.graphics": { enabled: true },
        "creative.software": { enabled: true, verification_runtime: "node" },
        "code.execute": {
          enabled: true,
          image,
          owner_compute_subsidy: true,
          wall_seconds: 30,
          memory_bytes: 134217728,
          pids: 32,
        },
        "ai.generate": {
          enabled: true,
          model: "synthetic-design-model",
          secret_ref: "fixture-model-key",
          endpoint: "http://127.0.0.1:" + models.address().port + "/v1",
          allow_local_http: true,
          model_context_tokens: 8192,
          max_output_tokens: 2048,
          max_input_bytes: 32000,
          max_output_bytes: 32000,
          maximum_microusd: 100000,
          tariff: {
            input_microusd_per_million_tokens: 1000,
            output_microusd_per_million_tokens: 1000,
          },
        },
      },
    }),
    { mode: 0o600 },
  );
  fixtureEnv = env;
  Object.assign(process.env, env);
  app = await bootstrap(env);
  const store = new Store(app.db);
  store.create("foundation", Date.now(), runId);
  store.control(runId, "resume");
  const launch = (args) => {
    const c = spawn(process.execPath, args, {
      env,
      stdio: ["ignore", "pipe", "pipe"],
    });
    for (const stream of [c.stdout, c.stderr])
      stream.on(
        "data",
        (chunk) => (logs = (logs + String(chunk)).slice(-20000)),
      );
    children.push(c);
    return c;
  };
  launch(["scripts/services.mjs", "start"]);
  await waitFor(async () => {
    try {
      return (await fetch(env.WIDEMIND_BUSINESS_URL + "/health")).ok;
    } catch {
      return false;
    }
  }, "services startup");
  const econ = async (path, body) => {
    const r = await fetch(env.WIDEMIND_ECONOMY_URL + path, {
      method: body ? "POST" : "GET",
      headers: {
        Authorization: "Bearer " + env.WIDEMIND_ECONOMY_OPERATOR_TOKEN,
        "Content-Type": "application/json",
      },
      ...(body ? { body: JSON.stringify(body) } : {}),
    });
    const v = await r.json();
    assert.ok(r.ok, "Economy " + path + " " + JSON.stringify(v));
    return v;
  };
  await econ("/v1/runs", { run_id: runId, mode: "test" });
  server = webServer(app);
  await new Promise((r) => server.listen(web, "127.0.0.1", r));
  const post = async (path, body) => {
    const r = await fetch(origin + path, {
      method: "POST",
      headers: { Origin: origin, "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const v = await r.json();
    assert.ok(r.ok, path + " " + JSON.stringify(v));
    return v;
  };
  worker = launch(["src/runtime/main.ts"]);
  await waitFor(async () => {
    const v = await (
      await fetch(origin + "/api/modules/business/offerings")
    ).json();
    return (
      v.offerings?.filter(
        (s) =>
          ["custom-graphic", "interactive-calculator"].includes(s.id) &&
          s.available,
      ).length === 2
    );
  }, "enhanced capability gates including actual Docker runtime");
  async function order(service, scope, index) {
    const customerToken = token();
    await post("/api/modules/business/request", {
      requestId: "creative-fixture-" + index,
      customerToken,
      email: "synthetic-creative@example.invalid",
      service,
      scope,
    });
    let customer;
    const read = async () =>
      (customer = await post("/api/modules/business/customer", {
        token: customerToken,
      }));
    await waitFor(async () => {
      await read();
      return customer.quotes?.some((q) => q.status === "offered");
    }, "scoped quote " + service);
    assert.equal(
      modelCalls.filter((c) => c.title === scope.title).length,
      0,
      "No paid planning before accepted, funded scope",
    );
    await post("/api/modules/business/accept", {
      token: customerToken,
      quoteId: customer.quotes[0].quoteId,
    });
    await waitFor(async () => {
      await read();
      return customer.orders?.[0]?.checkoutUrl;
    }, "PayPal checkout " + service);
    const orderId = customer.orders[0].orderId,
      paymentId = new URL(customer.orders[0].checkoutUrl).searchParams.get(
        "token",
      );
    if (index === 1) {
      const exited = new Promise((r) => worker.once("exit", r));
      worker.kill("SIGKILL");
      await exited;
    }
    const event = {
        id: "CREATIVE_APPROVAL_" + index,
        event_type: "CHECKOUT.ORDER.APPROVED",
        resource: { id: paymentId },
      },
      headers = {
        "paypal-auth-algo": "SHA256withRSA",
        "paypal-cert-url":
          "https://api-m.sandbox.paypal.com/v1/notifications/certs/FIXTURE",
        "paypal-transmission-id": "creative-fixture-" + index,
        "paypal-transmission-time": new Date().toISOString(),
        "paypal-transmission-sig": "fixture-valid",
        "Content-Type": "application/json",
      };
    for (let i = 0; i < 2; i++) {
      const r = await fetch(origin + "/api/modules/economy/webhook", {
        method: "POST",
        headers,
        body: JSON.stringify(event),
      });
      assert.ok(r.ok);
    }
    if (index === 1) worker = launch(["src/runtime/main.ts"]);
    await waitFor(async () => {
      await read();
      return customer.orders?.[0]?.artifactVersions?.length === 1;
    }, "verified original delivery " + service);
    const delivery = await post("/api/modules/business/delivery", {
        token: customerToken,
        orderId,
      }),
      manifest = await post("/api/modules/business/manifest", {
        token: customerToken,
        orderId,
        version: 1,
      });
    assert.equal(manifest.manifest_version, 1);
    assert.deepEqual(manifest.scope, scope);
    assert.ok(manifest.creation_costs.length >= 2);
    assert.ok(
      manifest.creation_costs.every((c) =>
        Number.isInteger(c.charged_micro_usd),
      ),
    );
    assert.ok(
      manifest.creation_costs.some(
        (c) =>
          c.stage === "generate" &&
          c.charged_micro_usd > 0 &&
          c.provisional === true,
      ),
    );
    assert.equal(
      createHash("sha256").update(delivery.content).digest("hex"),
      delivery.sha256,
    );
    const dest = join(output, service);
    await mkdir(dest, { recursive: true });
    for (const file of customer.orders[0].artifactVersions[0].files) {
      const value = await post("/api/modules/business/delivery", {
        token: customerToken,
        orderId,
        version: 1,
        filename: file.filename,
      });
      const bytes = Buffer.from(
        value.content,
        value.encoding === "base64" ? "base64" : "utf8",
      );
      assert.equal(
        createHash("sha256").update(bytes).digest("hex"),
        file.sha256,
      );
      await writeFile(join(dest, file.filename), bytes);
    }
    await writeFile(
      join(dest, "private-synthetic-manifest-v1.json"),
      JSON.stringify(manifest, null, 2) + "\n",
    );
    const denied = await fetch(origin + "/api/modules/business/manifest", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token: token(), orderId }),
    });
    assert.equal(denied.ok, false);
    return { token: customerToken, orderId, read, manifest, delivery };
  }
  console.log(
    "CREATIVE real SVG/PNG with recorded model usage and private delivery",
  );
  const graphic = await order("custom-graphic", graphicScope, 1);
  await post("/api/modules/business/change-request", {
    token: graphic.token,
    orderId: graphic.orderId,
    requestId: "included-revision",
    kind: "revision",
    feedback:
      "Move the mint accent a little to the right; keep every accepted word and color.",
  });
  await waitFor(async () => {
    const c = await graphic.read();
    return c.orders[0].artifactVersions?.length === 2;
  }, "bounded graphic revision");
  const revised = await post("/api/modules/business/manifest", {
    token: graphic.token,
    orderId: graphic.orderId,
    version: 2,
  });
  assert.deepEqual(revised.scope, graphic.manifest.scope);
  assert.equal(
    revised.accepted_budget_micro_usd,
    graphic.manifest.accepted_budget_micro_usd,
  );
  assert.equal(revised.revision_history.length, 1);
  assert.deepEqual(
    await post("/api/modules/business/manifest", {
      token: graphic.token,
      orderId: graphic.orderId,
      version: 1,
    }),
    graphic.manifest,
    "Original manifest remains immutable after revision",
  );
  console.log(
    "CREATIVE actual generated calculator JS; intentionally wrong first formula must trigger one bounded repair",
  );
  const software = await order("interactive-calculator", calculatorScope, 2);
  assert.equal(
    modelCalls.filter((c) => c.title === calculatorScope.title).length,
    2,
  );
  assert.equal(software.manifest.checks.generated_javascript_executed, true);
  assert.equal(
    software.manifest.checks.acceptance_cases,
    calculatorScope.acceptance_cases.length,
  );
  const savedCalls = modelCalls.length;
  await delay(1500);
  await software.read();
  await graphic.read();
  assert.equal(
    modelCalls.length,
    savedCalls,
    "No recurring paid calls while idle or inspecting deliveries",
  );
  const publicData = await (await fetch(origin + "/api/public")).json();
  assert.equal(
    JSON.stringify(publicData).includes("synthetic-creative@example.invalid"),
    false,
  );
  assert.equal(publicData.financial.mode, "sandbox");
  const ledger = await econ("/v1/runs/" + runId + "/ledger"),
    reservations = await econ("/v1/runs/" + runId + "/reservations");
  assert.ok(ledger.some((e) => e.category === "operating_charge"));
  assert.equal(provider.stats().captured, 2);
  browser = await browserSession(directory, output);
  await browser.viewport(1280, 900);
  await browser.navigate(origin + "/commerce#customer=" + software.token);
  await browser.waitFor(() =>
    browser.evaluate(
      "!!Array.from(document.querySelectorAll('button')).find(b=>b.textContent==='Open isolated preview')",
    ),
  );
  await browser.evaluate(
    "window.creativePreviewLoaded=false;document.querySelector('iframe').addEventListener('load',()=>window.creativePreviewLoaded=true,{once:true});Array.from(document.querySelectorAll('button')).find(b=>b.textContent==='Open isolated preview').click()",
  );
  await browser.waitFor(() =>
    browser.evaluate(
      "window.creativePreviewLoaded&&!document.querySelector('iframe').hidden",
    ),
  );
  assert.equal(
    await browser.evaluate(
      "document.querySelector('iframe').getAttribute('sandbox')",
    ),
    "allow-scripts",
  );
  const previewName = await browser.evaluate(
    "document.querySelector('iframe').name",
  );
  const inPreview = (expression) =>
    browser.evaluateFrame(previewName, expression);
  assert.equal(
    await inPreview("document.getElementById('output-estimate').textContent"),
    "480",
  );
  assert.equal(
    await inPreview(
      "(()=>{const form=document.getElementById('calculator-form');form.elements.hours.value='2.5';form.elements.rate.value='80';form.dispatchEvent(new Event('input',{bubbles:true}));return document.getElementById('output-estimate').textContent})()",
    ),
    "200",
  );
  assert.equal(
    await inPreview(
      "(()=>{const form=document.getElementById('calculator-form');form.elements.hours.value='501';form.dispatchEvent(new Event('input',{bubbles:true}));return document.getElementById('calculator-status').dataset.state})()",
    ),
    "error",
  );
  await inPreview("document.getElementById('calculator-form').reset()");
  await delay(100);
  assert.equal(
    await inPreview("document.getElementById('output-estimate').textContent"),
    "480",
  );
  assert.equal(
    await inPreview(
      "(()=>{document.getElementById('input-hours').focus();return document.activeElement.id})()",
    ),
    "input-hours",
  );
  await browser.evaluate(
    "document.querySelector('iframe').scrollIntoView({block:'center'})",
  );
  await delay(300);
  await browser.screenshot("calculator-private-desktop");
  await browser.viewport(390, 844, true);
  assert.equal(
    await browser.evaluate("document.documentElement.scrollWidth<=innerWidth"),
    true,
  );
  await browser.evaluate(
    "document.querySelector('iframe').scrollIntoView({block:'center'})",
  );
  await delay(300);
  await browser.screenshot("calculator-private-phone");
  assert.deepEqual(browser.errors, []);
  const report = {
    passed: true,
    fixtureBoundaries: ["PayPal HTTP protocol", "model Responses HTTP"],
    realComponents: [
      "persistent Node worker",
      "three authenticated services",
      "native AI adapter usage receipts",
      "SQLite stage recovery",
      "GraphicsAdapter SVG/PNG renderers",
      "networkless Docker generated-JS execution",
      "private artifact manifests/downloads",
      "opaque-origin preview",
    ],
    providerStats: provider.stats(),
    modelCalls: modelCalls.length,
    graphicVersions: 2,
    calculatorRepairAttempts: 2,
    manifestHashesVerified: true,
    privateOwnershipVerified: true,
    publicProjectionRedacted: true,
    revisionScopeAndBudgetFrozen: true,
    workerRestart: true,
    idleModelCalls: 0,
    provisionalUsagePreserved: true,
    calculatorBrowserChecks: [
      "initial calculation",
      "live recalculation",
      "invalid input",
      "reset",
      "keyboard focus",
      "phone layout",
    ],
    codeImage: image,
    pageErrors: browser.errors,
    reservationCount: reservations.length,
  };
  await writeFile(
    join(output, "verification.json"),
    JSON.stringify(report, null, 2) + "\n",
  );
  console.log(JSON.stringify(report));
} catch (error) {
  failed = true;
  try {
    const health = await fetch(
      fixtureEnv.WIDEMIND_TOOLS_URL + "/capabilities",
      {
        headers: { Authorization: "Bearer " + fixtureEnv.WIDEMIND_TOOLS_TOKEN },
      },
    );
    console.error(
      "Fixture capability diagnostics:",
      JSON.stringify(await health.json()),
    );
  } catch {}
  try {
    const db = new DatabaseSync(join(directory, "business.sqlite"), {
      readOnly: true,
    });
    console.error(
      "Fixture stage diagnostics:",
      JSON.stringify({
        jobs: db
          .prepare("SELECT state,revision,attempt,errors FROM creative_jobs")
          .all(),
        opportunities: db
          .prepare("SELECT service,status,assessment FROM opportunities")
          .all(),
        events: db
          .prepare(
            "SELECT kind,state,error FROM events WHERE state IN ('queued','running','blocked','failed')",
          )
          .all(),
        effects: db
          .prepare(
            "SELECT provider,action,state,reason FROM effects WHERE state!='succeeded'",
          )
          .all(),
        stages: db
          .prepare("SELECT revision,attempt,kind,state FROM creative_stages")
          .all(),
        modelCalls: modelCalls.length,
      }),
    );
    db.close();
    const journal = new DatabaseSync(
      join(directory, "tools/control/actions.sqlite3"),
      { readOnly: true },
    );
    const operations = journal
      .prepare("SELECT id,status,started,state,result FROM operations")
      .all();
    journal.close();
    console.error(
      "Fixture tool journal:",
      JSON.stringify(
        operations.map((row) => ({
          id: row.id,
          status: row.status,
          started: row.started,
          stateKeys: Object.keys(JSON.parse(row.state)),
          outcome: row.result ? JSON.parse(row.result).kind : null,
          reason: row.result ? JSON.parse(row.result).reason : null,
        })),
      ),
    );
    for (const row of operations.filter(
      (row) => row.status === "unknown" || row.status === "running",
    )) {
      const response = await fetch(
        fixtureEnv.WIDEMIND_TOOLS_URL + "/reconcile",
        {
          method: "POST",
          headers: {
            Authorization: "Bearer " + fixtureEnv.WIDEMIND_TOOLS_TOKEN,
            "Content-Type": "application/json",
          },
          body: JSON.stringify({ action_id: row.id }),
        },
      );
      const observation = await response.json();
      console.error("Fixture reconciliation response", response.status, {
        state: observation.state,
        kind: observation.kind,
        error: observation.error,
        reason: observation.reason,
        message: observation.message,
      });
    }
  } catch {}
  console.error(
    "Service diagnostic tail (no request bodies):",
    logs.slice(-5000),
  );
  throw error;
} finally {
  await browser?.close();
  for (const child of children.reverse()) await stop(child);
  if (server) await new Promise((r) => server.close(r));
  app?.db.close();
  await provider.close();
  await new Promise((r) => models.close(r));
  if (failed && process.env.WIDEMIND_CREATIVE_KEEP_FIXTURE === "1")
    console.error("Private synthetic fixture retained at", directory);
  else
    await rm(directory, {
      recursive: true,
      force: true,
      maxRetries: 5,
      retryDelay: 100,
    });
}
