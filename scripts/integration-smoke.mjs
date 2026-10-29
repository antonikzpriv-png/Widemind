/** Full local system acceptance. Only the PayPal network boundary is a fixture. */
import { DatabaseSync } from "node:sqlite";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, rm, mkdir, writeFile, readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { randomBytes, randomUUID } from "node:crypto";
import { createServer } from "node:net";
import { setTimeout as delay } from "node:timers/promises";
import { browserSession } from "./browser-session.mjs";
import { paypalFixture } from "../test/fixtures/paypal-server.mjs";
import { bootstrap } from "../src/bootstrap.ts";
import { Store } from "../src/runtime/store.ts";
import { webServer } from "../src/web/server.ts";
import { hashPassword } from "../src/auth/password.ts";
const directory = await mkdtemp(join(tmpdir(), "widemind-integrated-")),
  provider = await paypalFixture(),
  children = [];
let logs = "";
let server, app, browser;
const withBrowser = process.argv.includes("--browser");
async function port() {
  const p = createServer();
  await new Promise((resolve) => p.listen(0, "127.0.0.1", resolve));
  const n = p.address().port;
  await new Promise((resolve) => p.close(resolve));
  return n;
}
async function waitFor(fn, label) {
  const until = Date.now() + 60000;
  while (Date.now() < until) {
    if (await fn()) return;
    await delay(1200);
  }
  throw new Error("Timed out: " + label);
}
async function stop(child) {
  if (child.exitCode !== null || child.signalCode !== null) return;
  const done = new Promise((resolve) => child.once("exit", resolve));
  child.kill("SIGTERM");
  const timer = setTimeout(() => child.kill("SIGKILL"), 3000);
  try {
    await done;
  } finally {
    clearTimeout(timer);
  }
}
try {
  const [web, economy, tools, business] = await Promise.all([
    port(),
    port(),
    port(),
    port(),
  ]);
  const token = () => randomBytes(32).toString("hex"),
    runId = randomUUID(),
    origin = "http://127.0.0.1:" + web,
    password = token();
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
    WIDEMIND_HOST: "127.0.0.1",
    WIDEMIND_PORT: String(web),
    WIDEMIND_PUBLIC_ORIGIN: origin,
    WIDEMIND_SECURE_COOKIES: "false",
    WIDEMIND_OPERATOR_PASSWORD_HASH: await hashPassword(password),
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
  };
  Object.assign(process.env, env);
  app = await bootstrap();
  const store = new Store(app.db);
  store.create("foundation", Date.now(), runId);
  const launch = (args) => {
    const child = spawn(process.execPath, args, {
      env,
      stdio: ["ignore", "pipe", "pipe"],
    });
    child.stdout.on("data", (chunk) => {
      logs += String(chunk);
    });
    child.stderr.on("data", (chunk) => {
      logs += String(chunk);
    });
    children.push(child);
    return child;
  };
  const launcher = launch(["scripts/services.mjs", "start"]);
  await waitFor(async () => {
    try {
      return (
        await fetch(env.WIDEMIND_BUSINESS_URL + "/health", {
          headers: { Authorization: "Bearer " + env.WIDEMIND_BUSINESS_TOKEN },
        })
      ).ok;
    } catch {
      return false;
    }
  }, "three service startup");
  const econ = async (path, body) => {
    const response = await fetch(env.WIDEMIND_ECONOMY_URL + path, {
      method: body ? "POST" : "GET",
      headers: {
        Authorization: "Bearer " + env.WIDEMIND_ECONOMY_OPERATOR_TOKEN,
        "Content-Type": "application/json",
      },
      ...(body ? { body: JSON.stringify(body) } : {}),
    });
    const value = await response.json();
    assert.ok(response.ok, "economy " + path + " " + JSON.stringify(value));
    return value;
  };
  await econ("/v1/runs", { run_id: runId, mode: "test" });
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
    assert.ok(response.ok, path + " " + JSON.stringify(value));
    return { response, value };
  };
  const login = await post("/api/operator/login", { password });
  const headers = {
    Cookie: login.response.headers.get("set-cookie").split(";")[0],
    "x-csrf-token": login.value.csrf,
  };
  await post("/api/operator/runs/" + runId + "/resume", {}, headers);
  let worker = launch(["src/runtime/main.ts"]);
  console.log("INTEGRATION request -> quote");
  let offerings;
  await waitFor(async () => {
    const response = await fetch(origin + "/api/modules/business/offerings");
    offerings = await response.json();
    return (
      response.ok &&
      offerings.offerings?.some(
        (item) => item.id === "svg-graphic" && item.available,
      )
    );
  }, "real local graphics capability");
  let customerToken = token();
  if (withBrowser) {
    browser = await browserSession(
      directory,
      resolve("var/integration-browser"),
    );
    await browser.viewport(1280, 900);
    await browser.navigate(origin + "/commerce");
    await browser.waitFor(() =>
      browser.evaluate(
        "!!document.querySelector('option[value=\"svg-graphic\"]')",
      ),
    );
    await browser.fill(
      "input[name=email]",
      "controlled-customer@example.invalid",
    );
    await browser.evaluate(
      "document.querySelector('select[name=service]').value='svg-graphic'",
    );
    await browser.fill("input[name=title]", "Local integration pilot");
    await browser.fill(
      "textarea[name=description]",
      "An actual SVG delivered through the private portal",
    );
    await browser.click("#commerce-request button[type=submit]");
    await browser.waitFor(() =>
      browser.evaluate(
        "document.querySelector('#request-status').textContent.includes('Request saved')",
      ),
    );
    customerToken = await browser.evaluate(
      "new URLSearchParams(location.hash.slice(1)).get('customer')",
    );
    assert.equal(customerToken.length, 64);
    await browser.screenshot("customer-request-desktop");
  } else
    await post("/api/modules/business/request", {
      requestId: "controlled-pilot-request",
      customerToken,
      email: "controlled-customer@example.invalid",
      service: "svg-graphic",
      scope: {
        title: "Local integration pilot",
        description: "An actual SVG delivered through the private portal",
      },
    });
  let customer;
  const readCustomer = async () => {
    customer = (
      await post("/api/modules/business/customer", { token: customerToken })
    ).value;
    return customer;
  };
  await waitFor(async () => {
    await readCustomer();
    return customer.quotes?.some((quote) => quote.status === "offered");
  }, "quote");
  if (withBrowser) {
    await browser.click("#customer-open button");
    await browser.waitFor(() =>
      browser.evaluate(
        "!!Array.from(document.querySelectorAll('#customer-content button')).find(el=>el.textContent==='Accept this quote')",
      ),
    );
    await browser.click("#customer-content button");
  } else
    await post("/api/modules/business/accept", {
      token: customerToken,
      quoteId: customer.quotes.find((quote) => quote.status === "offered")
        .quoteId,
    });
  await waitFor(async () => {
    await readCustomer();
    return customer.orders?.some((order) => order.checkoutUrl);
  }, "PayPal checkout");
  console.log("INTEGRATION quote accepted -> payment");
  const order = customer.orders[0];
  const paymentId = new URL(order.checkoutUrl).searchParams.get("token");
  if (withBrowser) {
    await browser.click("#customer-open button");
    await browser.waitFor(() =>
      browser.evaluate(
        "!!document.querySelector('#customer-content a[href*=paypal]')",
      ),
    );
    assert.equal(
      new URL(
        await browser.evaluate(
          "document.querySelector('#customer-content a[href*=paypal]').href",
        ),
      ).hostname,
      "www.sandbox.paypal.com",
    );
  }
  const approval = {
    id: "CONTROLLED_APPROVAL_EVENT",
    event_type: "CHECKOUT.ORDER.APPROVED",
    resource: { id: paymentId },
  };
  const paypalHeaders = {
    "paypal-auth-algo": "SHA256withRSA",
    "paypal-cert-url":
      "https://api-m.sandbox.paypal.com/v1/notifications/certs/FIXTURE",
    "paypal-transmission-id": "controlled-transmission",
    "paypal-transmission-time": new Date().toISOString(),
    "paypal-transmission-sig": "fixture-valid",
  };
  const invalid = await fetch(origin + "/api/modules/economy/webhook", {
    method: "POST",
    headers: {
      ...paypalHeaders,
      "paypal-transmission-sig": "invalid",
      "Content-Type": "application/json",
    },
    body: JSON.stringify(approval),
  });
  assert.equal(invalid.ok, false);
  assert.equal(provider.stats().captured, 0);
  // Kill the actual worker with an accepted commitment outstanding. Payment
  // reconciles while it is down; a new process must recover durable work.
  const exited = new Promise((resolve) => worker.once("exit", resolve));
  worker.kill("SIGKILL");
  await exited;
  await post("/api/modules/economy/webhook", approval, paypalHeaders);
  await post("/api/modules/economy/webhook", approval, paypalHeaders);
  worker = launch(["src/runtime/main.ts"]);
  console.log("INTEGRATION payment captured -> delivery");
  await waitFor(async () => {
    await readCustomer();
    return customer.orders?.some((order) => order.status === "completed");
  }, "paid private delivery");
  const delivery = (
    await post("/api/modules/business/delivery", {
      token: customerToken,
      orderId: order.orderId,
    })
  ).value;
  assert.match(delivery.content, /<svg/);
  if (withBrowser) {
    await browser.click("#customer-open button");
    await browser.waitFor(() =>
      browser.evaluate(
        "!!Array.from(document.querySelectorAll('#customer-content button')).find(el=>el.textContent==='Download your delivery')",
      ),
    );
    await browser.click("#customer-content button");
    await waitFor(async () => {
      try {
        return (
          await readFile(
            join(directory, "downloads", delivery.filename),
            "utf8",
          )
        ).includes("<svg");
      } catch {
        return false;
      }
    }, "actual browser artifact download");
    await browser.viewport(390, 844, true);
    assert.equal(
      await browser.evaluate(
        "document.documentElement.scrollWidth<=innerWidth",
      ),
      true,
    );
    assert.equal(
      await browser.evaluate(
        "document.querySelector('#customer-content').textContent.includes('maximum_delivery_seconds')",
      ),
      false,
    );
    assert.equal(
      await browser.evaluate(
        "!!document.querySelector('#customer-content a[href*=paypal]')",
      ),
      false,
    );
    await browser.screenshot("customer-delivery-phone");
    await browser.navigate(origin + "/operator");
    await browser.fill("input[name=password]", password);
    await browser.click("form button");
    await browser.waitFor(() =>
      browser.evaluate("document.title.startsWith('Run control')"),
    );
    await browser.navigate(origin + "/operator/integrations");
    await browser.waitFor(() =>
      browser.evaluate(
        "document.querySelector('#operator-message').textContent.includes('Status refreshed')",
      ),
    );
    assert.equal(
      await browser.evaluate(
        "document.querySelector('#economy-status').textContent.includes('6900000')",
      ),
      true,
    );
    assert.equal(
      await browser.evaluate(
        "document.querySelector('#tools-status').textContent.includes('graphics')",
      ),
      true,
    );
    assert.equal(
      await browser.evaluate(
        "document.documentElement.scrollWidth<=innerWidth",
      ),
      true,
    );
    await browser.click("#business-policy button");
    await browser.waitFor(() =>
      browser.evaluate(
        "document.querySelector('#operator-message').textContent==='Policy saved.'",
      ),
    );
    await browser.fill(
      "#business-reconcile input[name=actionId]",
      "nonexistent-controlled-effect",
    );
    await browser.click("#business-reconcile button");
    await browser.waitFor(() =>
      browser.evaluate(
        "document.querySelector('#operator-message').textContent.includes('unavailable')",
      ),
    );
    await browser.screenshot("integrations-phone");
    assert.deepEqual(browser.errors, []);
  }
  const denied = await fetch(origin + "/api/modules/business/delivery", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token: token(), orderId: order.orderId }),
  });
  assert.equal(denied.ok, false);
  const before = provider.stats();
  await readCustomer();
  assert.deepEqual(provider.stats(), before);
  await post("/api/operator/runs", {}, headers);
  const publicData = await (await fetch(origin + "/api/public")).json();
  assert.equal(publicData.financialRunId, runId);
  assert.ok(publicData.offerings.some((item) => item.id === "svg-graphic"));
  assert.ok(publicData.activity.some((item) => /completed/i.test(item.text)));
  const curated = JSON.stringify({
    offerings: publicData.offerings,
    activity: publicData.activity,
  });
  assert.equal(curated.includes("controlled-customer"), false);
  assert.equal(curated.includes("Local integration pilot"), false);
  assert.equal(publicData.financial.mode, "sandbox");
  assert.ok(BigInt(publicData.financial.customerReceiptsMicros) > 0n);
  assert.ok(BigInt(publicData.financial.earnedRevenueMicros) > 0n);
  assert.equal(before.orders, 1);
  assert.equal(before.captured, 1);
  const financial = publicData.financial;
  assert.equal(
    BigInt(financial.postedMicros),
    BigInt(financial.openingCreditMicros) +
      BigInt(financial.ownerFundingMicros) +
      BigInt(financial.customerReceiptsMicros) -
      BigInt(financial.operatingChargesMicros) -
      BigInt(financial.processorFeesMicros) -
      BigInt(financial.refundsMicros) +
      BigInt(financial.adjustmentsMicros),
  );
  assert.equal(
    BigInt(financial.spendableMicros),
    BigInt(financial.postedMicros) -
      BigInt(financial.reservedMicros) -
      BigInt(financial.holdsMicros),
  );
  if (withBrowser) {
    await browser.navigate(origin + "/");
    assert.equal(
      await browser.evaluate(
        "document.body.textContent.includes('$6.90 spendable')",
      ),
      true,
    );
    assert.equal(
      await browser.evaluate(
        "document.body.textContent.includes('Completed svg graphic work.')",
      ),
      true,
    );
    assert.equal(
      await browser.evaluate(
        "document.documentElement.scrollWidth<=innerWidth",
      ),
      true,
    );
    await browser.screenshot("headquarters-phone");
  }
  const refundId = provider.simulateExternalRefund(paymentId, "2.00");
  const refundEvent = {
    id: "CONTROLLED_REFUND_EVENT",
    event_type: "PAYMENT.CAPTURE.REFUNDED",
    resource: { id: refundId },
  };
  await post("/api/modules/economy/webhook", refundEvent, paypalHeaders);
  await post("/api/modules/economy/webhook", refundEvent, paypalHeaders);
  let afterRefund;
  await waitFor(async () => {
    afterRefund = await (await fetch(origin + "/api/public")).json();
    return afterRefund.financial?.refundsMicros === "2000000";
  }, "queued externally initiated PayPal refund");
  assert.equal(afterRefund.financial.postedMicros, "4900000");
  assert.equal(afterRefund.financial.spendableMicros, "4900000");
  assert.equal(provider.stats().captured, 1);
  assert.equal(provider.stats().refunds, 1);
  if (withBrowser) assert.deepEqual(browser.errors, []);
  const report = {
    passed: true,
    externalPayment:
      "explicit local PayPal protocol fixture; no actual payment",
    actualServices: ["economy", "tools", "business"],
    persistentWorker: true,
    hardWorkerRestart: true,
    publicHeadquartersWired: true,
    publicFinancialRunPinned: true,
    browserCustomerAndOperator: withBrowser,
    customerIsolation: true,
    privateDelivery: true,
    artifact: delivery.filename,
    queuedCaptureAndRefundEvents: true,
    providerEffects: provider.stats(),
    financialBeforeRefund: publicData.financial,
    financial: afterRefund.financial,
  };
  await mkdir("var", { recursive: true });
  await writeFile(
    "var/integration-report.json",
    JSON.stringify(report, null, 2) + "\n",
  );
  console.log(JSON.stringify(report));
} catch (error) {
  if (app) {
    console.error(
      "Runtime state:",
      JSON.stringify(
        app.db
          .prepare("SELECT state,reason,last_error FROM runtime_runs")
          .all(),
      ),
    );
    console.error(
      "Actions:",
      JSON.stringify(
        app.db
          .prepare("SELECT state,reason,attempts FROM runtime_actions")
          .all(),
      ),
    );
  }
  try {
    const businessDb = new DatabaseSync(join(directory, "business.sqlite"), {
      readOnly: true,
    });
    for (const sql of [
      "SELECT kind,state,error FROM events",
      "SELECT status FROM orders",
      "SELECT provider,action,state,reason FROM effects",
    ])
      console.error(sql, JSON.stringify(businessDb.prepare(sql).all()));
    businessDb.close();
  } catch {}
  console.error(error.message);
  console.error(logs.slice(-14000));
  process.exitCode = 1;
} finally {
  await browser?.close();
  await Promise.all(children.reverse().map(stop));
  if (server) await new Promise((resolve) => server.close(resolve));
  app?.db.close();
  await provider.close();
  await rm(directory, { recursive: true, force: true });
}
