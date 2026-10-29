/** Actual Node→Python HTTP/SQLite/Stripe-adapter integration, synthetic provider records only. */
import test from "node:test";
import assert from "node:assert/strict";
import { spawn, type ChildProcess } from "node:child_process";
import { once } from "node:events";
import { createHmac } from "node:crypto";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { createServer } from "node:net";
import { setTimeout as delay } from "node:timers/promises";
import {
  EconomyCommerce,
  createCommerceTools,
  commerceSettings,
  validateCommerceOrder,
  type CommerceOrder,
} from "../src/modules/brief-business/commerce.ts";
import { EconomyClient } from "../src/modules/economy/client.ts";
import { fixture as runtimeFixture } from "./helpers.ts";
import type { ToolAction } from "../src/contracts/index.ts";

const OPERATOR = "fixture-business-operator-" + "o".repeat(48);
const GATEWAY = "fixture-business-gateway-" + "g".repeat(48);
const CONTROL = "fixture-business-control-" + "c".repeat(48);
const identity = { runId: "commerce-run", jobId: "commerce-job" };
const quote = {
  ...identity,
  requestId: "commerce-checkout",
  amountMicros: "10000000",
  currency: "USD" as const,
  description: "Explicit synthetic commerce order",
};

async function fixture() {
  const directory = mkdtempSync(join(tmpdir(), "widemind-commerce-"));
  const probe = createServer();
  await new Promise<void>((resolve) => probe.listen(0, "127.0.0.1", resolve));
  const port = (probe.address() as { port: number }).port;
  await new Promise<void>((resolve) => probe.close(() => resolve()));
  const url = `http://127.0.0.1:${port}`;
  let child: ChildProcess | undefined;
  let output = "";
  const close = async () => {
    if (child && child.exitCode === null && child.signalCode === null) {
      const current = child,
        exited = once(current, "exit");
      current.kill("SIGTERM");
      const timer = setTimeout(() => current.kill("SIGKILL"), 3000);
      try {
        await exited;
      } finally {
        clearTimeout(timer);
      }
    }
    rmSync(directory, { recursive: true, force: true });
  };
  try {
    child = spawn(
      process.env.WIDEMIND_TEST_PYTHON ?? "python3",
      [
        resolve("test/business-provider-fixture.py"),
        "--explicit-fixtures",
        "--database",
        join(directory, "economy.sqlite3"),
        "--port",
        String(port),
      ],
      {
        env: { ...process.env, PYTHONPATH: resolve("src") },
        stdio: ["ignore", "pipe", "pipe"],
      },
    );
    child.on("error", (error) => {
      output = String(error);
    });
    for (const stream of [child.stdout, child.stderr])
      stream?.on("data", (chunk) => {
        output = (output + chunk.toString()).slice(-4096);
      });
    let ready = false;
    for (let attempt = 0; attempt < 150; attempt++) {
      if (child.exitCode !== null)
        throw new Error("Explicit fixture backend failed: " + output);
      try {
        if (
          (await fetch(url + "/health", { signal: AbortSignal.timeout(250) }))
            .ok
        ) {
          ready = true;
          break;
        }
      } catch {
        /* Bounded loopback startup polling only. */
      }
      await delay(30);
    }
    assert.ok(ready, output);
    const settings = {
      url,
      operatorToken: OPERATOR,
      gatewayToken: GATEWAY,
      timeoutMs: 4000,
      mode: "sandbox" as const,
    };
    const commerce = new EconomyCommerce(settings),
      economy = new EconomyClient(settings);
    async function rpc(path: string, body: unknown, token = OPERATOR) {
      const response = await fetch(url + path, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify(body),
      });
      assert.ok(response.ok, await response.clone().text());
      return response.json() as Promise<Record<string, unknown>>;
    }
    async function webhook(eventId: string, type: string, paymentId: string) {
      const raw = JSON.stringify({
        id: eventId,
        livemode: false,
        type,
        data: { object: { id: paymentId } },
      });
      const timestamp = Math.floor(Date.now() / 1000);
      const signature = createHmac("sha256", "whsec_fixture_signing_secret")
        .update(`${timestamp}.${raw}`)
        .digest("hex");
      const response = await fetch(url + "/v1/webhooks/stripe", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Stripe-Signature": `t=${timestamp},v1=${signature}`,
        },
        body: raw,
      });
      assert.equal(response.status, 202, await response.clone().text());
      await rpc("/_fixture/control", { operation: "drain" }, CONTROL);
    }
    await rpc("/v1/runs", { run_id: identity.runId, mode: "test" });
    return { url, commerce, economy, rpc, webhook, settings, close };
  } catch (error) {
    await close();
    throw error;
  }
}

test(
  "commerce uses the real protected Python authority for immutable checkout, settlement, delivery, and refund",
  { timeout: 30000 },
  async () => {
    const f = await fixture();
    try {
      assert.equal((await f.commerce.order(identity)).kind, "unavailable");
      const link = await f.commerce.createPaymentLink(quote);
      assert.equal(link.kind, "available");
      if (link.kind !== "available")
        throw new Error("Expected actual fixture-backed authority response");
      assert.equal(link.order.paymentStatus, "pending");
      assert.equal(link.order.receiptsMicros, "0");
      assert.equal(link.order.earnedRevenueMicros, "0");
      assert.equal(link.order.paymentId, "cs_created");
      assert.equal(
        (
          await f.commerce.createPaymentLink({
            ...quote,
            requestId: "replacement-checkout",
          })
        ).kind,
        "unavailable",
      );
      assert.equal(
        (
          await f.commerce.createPaymentLink({
            ...quote,
            amountMicros: "20000000",
          })
        ).kind,
        "unavailable",
      );
      assert.deepEqual(await f.commerce.createPaymentLink(quote), link);
      assert.equal(
        (await f.rpc("/_fixture/control", { operation: "stats" }, CONTROL))
          .checkoutPosts,
        1,
      );
      await f.webhook(
        "evt_commerce_paid",
        "checkout.session.completed",
        "cs_created",
      );
      await f.webhook(
        "evt_commerce_paid",
        "checkout.session.completed",
        "cs_created",
      );
      const paid = await f.commerce.order(identity);
      assert.equal(paid.kind, "available");
      if (paid.kind !== "available")
        throw new Error("Expected verified payment projection");
      assert.equal(paid.order.paymentStatus, "settled");
      assert.equal(paid.order.receiptsMicros, "10000000");
      assert.equal(paid.order.processorFeesMicros, "590000");
      assert.equal(paid.order.heldMicros, "9410000");
      assert.equal(paid.order.earnedRevenueMicros, "0");
      assert.equal(
        (await f.economy.summary(identity.runId))?.spendableMicros,
        "5000000",
      );
      const delivery = {
        ...identity,
        deliveryReference: "synthetic-authenticated-recipient-ack",
        idempotencyKey: "commerce-fulfill",
      };
      assert.equal(
        (await f.commerce.releaseHold(delivery)).kind,
        "unavailable",
      );
      const fulfilled = await f.commerce.recordFulfillment(delivery);
      assert.equal(fulfilled.kind, "available");
      if (fulfilled.kind !== "available")
        throw new Error("Expected trusted fixture delivery recognition");
      assert.equal(fulfilled.order.deliveryObligation, "fulfilled");
      assert.equal(fulfilled.order.earnedRevenueMicros, "10000000");
      assert.equal(fulfilled.order.heldMicros, "0");
      assert.deepEqual(await f.commerce.recordFulfillment(delivery), fulfilled);
      assert.equal(
        (await f.economy.summary(identity.runId))?.earnedRevenueMicros,
        "10000000",
      );
      await f.rpc("/_fixture/control", { operation: "refund" }, CONTROL);
      await f.webhook("evt_commerce_refund", "charge.refunded", "ch_fixture");
      const refunded = await f.commerce.order(identity);
      assert.equal(refunded.kind, "available");
      if (refunded.kind !== "available")
        throw new Error("Expected verified refund projection");
      assert.equal(refunded.order.paymentStatus, "refunded");
      assert.equal(refunded.order.refundsMicros, "10000000");
      assert.equal(refunded.order.earnedRevenueMicros, "0");
      assert.equal(refunded.order.deliveryObligation, "fulfilled");
      assert.equal(
        (await f.economy.summary(identity.runId))?.postedMicros,
        "4410000",
      );
      assert.equal(
        (await f.economy.summary(identity.runId))?.earnedRevenueMicros,
        "0",
      );
      const wrongMode = new EconomyCommerce({ ...f.settings, mode: "live" });
      assert.equal((await wrongMode.order(identity)).kind, "unavailable");
      const wrongRole = new EconomyCommerce({
        ...f.settings,
        operatorToken: GATEWAY,
        gatewayToken: OPERATOR,
      });
      assert.equal(
        (
          await wrongRole.recordFulfillment({
            ...delivery,
            idempotencyKey: "cannot-attest",
          })
        ).kind,
        "unavailable",
      );
    } finally {
      await f.close();
    }
  },
);

test(
  "commerce delegate uses the actual immutable runtime action and one central dispatch gate",
  { timeout: 30000 },
  async () => {
    const f = await fixture(),
      runtime = runtimeFixture("foundation");
    try {
      const env = {
        WIDEMIND_ECONOMY_URL: f.url,
        WIDEMIND_OPERATOR_TOKEN: OPERATOR,
        WIDEMIND_GATEWAY_TOKEN: GATEWAY,
        WIDEMIND_COMMERCE_ACCOUNT: "fixture-commerce",
        WIDEMIND_COMMERCE_MODE: "sandbox",
      };
      assert.equal(
        createCommerceTools({ db: runtime.db, config: runtime.config }, {}),
        null,
      );
      const tools = createCommerceTools(
        { db: runtime.db, config: runtime.config },
        env,
      )!;
      const runId = runtime.store.create("foundation");
      await f.rpc("/v1/runtime/seed", { runId, mode: "sandbox" });
      runtime.db.exec(
        "CREATE TABLE business_orders(id TEXT PRIMARY KEY,run_id TEXT NOT NULL,price_micros TEXT NOT NULL,status TEXT NOT NULL,delivery_reference TEXT); CREATE TABLE business_delivery_evidence(action_id TEXT PRIMARY KEY,run_id TEXT NOT NULL,job_id TEXT NOT NULL,evidence_ref TEXT NOT NULL,created_at TEXT NOT NULL)",
      );
      runtime.db
        .prepare("INSERT INTO business_orders VALUES(?,?,?,'accepted',NULL)")
        .run("delegate-job", runId, "10000000");
      runtime.services.economy = f.economy;
      runtime.services.tools = tools;
      runtime.services.business = {
        health: () => ({ available: true }),
        selectWork: async (snapshot) =>
          snapshot.actions.length
            ? { kind: "wait", reason: "Explicit fixture complete" }
            : {
                kind: "work",
                candidate: {
                  workKey: "fixture-commerce-once",
                  jobId: "delegate-job",
                  tool: "commerce.checkout",
                  input: {
                    amountMicros: "10000000",
                    currency: "USD",
                    description: "Explicit synthetic commerce order",
                  },
                  limit: {
                    maximum: { currency: "USD", micros: "0" },
                    maxDurationMs: 30000,
                    maxOutputBytes: 65536,
                  },
                },
              },
      };
      runtime.store.control(runId, "resume");
      assert.equal(await runtime.runtime.tick(), true);
      const row = runtime.store.actions(runId)[0]!;
      assert.equal(row.state, "completed", row.reason ?? "");
      const action = JSON.parse(row.action_json) as ToolAction;
      const reservation = JSON.parse(row.reservation_json!);
      assert.equal(
        (await tools.execute(action, reservation, new AbortController().signal))
          .kind,
        "unknown",
      );
      assert.equal(
        (await tools.reconcile(action, reservation)).kind,
        "receipt",
      );
      assert.equal(
        (await tools.reconcile({ ...action, jobId: "forged-job" }, reservation))
          .kind,
        "unknown",
      );
      assert.equal(
        (
          await tools.reconcile(
            {
              ...action,
              input: {
                amountMicros: "20000000",
                currency: "USD",
                description: "Changed input",
              },
            },
            reservation,
          )
        ).kind,
        "unknown",
      );
      assert.equal(
        (await f.rpc("/_fixture/control", { operation: "stats" }, CONTROL))
          .checkoutPosts,
        1,
      );
      assert.equal((await f.economy.summary(runId))?.postedMicros, "5000000");
      assert.equal((await f.economy.summary(runId))?.reservedMicros, "0");
      await f.webhook(
        "evt_delegate_paid",
        "checkout.session.completed",
        "cs_created",
      );
      const fulfillmentAction: ToolAction = {
        ...action,
        actionId: "delegate-fulfill",
        workKey: "delegate-fulfill",
        tool: "commerce.fulfill",
        input: { deliveryReference: "synthetic-authenticated-gmail-ack" },
      };
      runtime.db
        .prepare(
          "INSERT INTO runtime_actions(id,run_id,work_key,state,action_json,created_at,updated_at) VALUES(?,?,?,'planned',?,?,?)",
        )
        .run(
          fulfillmentAction.actionId,
          runId,
          fulfillmentAction.workKey,
          JSON.stringify(fulfillmentAction),
          Date.now(),
          Date.now(),
        );
      const authorized = await f.economy.authorize({
        runId,
        actionId: fulfillmentAction.actionId,
        tool: fulfillmentAction.tool,
        limit: fulfillmentAction.limit,
      });
      assert.equal(authorized.kind, "authorized");
      if (authorized.kind !== "authorized")
        throw new Error("Expected zero-cap fixture authorization");
      // Model input alone cannot establish delivery. This explicit fixture later
      // supplies the independently authenticated acknowledgment to the private store.
      assert.equal(
        (
          await tools.execute(
            fulfillmentAction,
            authorized.reservation,
            new AbortController().signal,
          )
        ).kind,
        "unknown",
      );
      assert.equal((await f.economy.summary(runId))?.earnedRevenueMicros, "0");
      runtime.db
        .prepare(
          "UPDATE business_orders SET status='delivered',delivery_reference=? WHERE id=?",
        )
        .run("synthetic-authenticated-gmail-ack", "delegate-job");
      runtime.db
        .prepare("INSERT INTO business_delivery_evidence VALUES(?,?,?,?,?)")
        .run(
          "synthetic-delivery-action",
          runId,
          "delegate-job",
          "synthetic-authenticated-gmail-ack",
          new Date().toISOString(),
        );
      const fulfillmentResult = await tools.execute(
        fulfillmentAction,
        authorized.reservation,
        new AbortController().signal,
      );
      assert.equal(fulfillmentResult.kind, "receipt");
      if (fulfillmentResult.kind !== "receipt")
        throw new Error("Expected evidence-bound fulfillment result");
      assert.equal(
        (
          await f.economy.settle(
            authorized.reservation,
            fulfillmentResult.receipt,
          )
        ).kind,
        "settled",
      );
      assert.equal(
        (await tools.reconcile(fulfillmentAction, authorized.reservation)).kind,
        "receipt",
      );
      assert.equal(
        (await f.economy.summary(runId))?.earnedRevenueMicros,
        "10000000",
      );
    } finally {
      runtime.close();
      await f.close();
    }
  },
);

const authoritativeOrder: CommerceOrder = {
  ...identity,
  mode: "sandbox",
  currency: "USD",
  requestId: quote.requestId,
  paymentId: "cs_created",
  checkoutUrl: "https://checkout.stripe.com/c/pay/fixture",
  priceMicros: quote.amountMicros,
  paymentStatus: "settled",
  revision: "1",
  receiptsMicros: "10000000",
  refundsMicros: "0",
  chargebacksMicros: "0",
  chargebackReversalsMicros: "0",
  processorFeesMicros: "590000",
  paymentAdjustmentsMicros: "0",
  netReceiptsMicros: "9410000",
  earnedRevenueMicros: "0",
  heldMicros: "9410000",
  pendingLiabilityMicros: "0",
  releasedMicros: "0",
  costsFinal: true,
  deliveryObligation: "unfulfilled",
  deliveryReference: null,
};
const settings = {
  url: "http://127.0.0.1:8080",
  operatorToken: OPERATOR,
  gatewayToken: GATEWAY,
  timeoutMs: 100,
  mode: "sandbox" as const,
};

test("commerce rejects numeric/noncanonical money, mismatched identity/mode, and stale-looking invented earnings", async () => {
  for (const changed of [
    { receiptsMicros: 10000000 },
    { receiptsMicros: "010000000" },
    { mode: "live" },
    { jobId: "another-job" },
    { earnedRevenueMicros: "10000000" },
    { netReceiptsMicros: "10000000" },
  ]) {
    const client = new EconomyCommerce(
      settings,
      async () =>
        new Response(JSON.stringify({ ...authoritativeOrder, ...changed })),
    );
    assert.equal((await client.order(identity)).kind, "unavailable");
  }
  const large = "9007199254740993";
  const exact = validateCommerceOrder(
    {
      ...authoritativeOrder,
      receiptsMicros: large,
      processorFeesMicros: "0",
      heldMicros: large,
      netReceiptsMicros: large,
    },
    identity,
    "sandbox",
  );
  assert.equal(exact.receiptsMicros, large);
  assert.equal(exact.heldMicros, large);
  assert.equal(commerceSettings({}), null);
  assert.throws(() => commerceSettings({ WIDEMIND_COMMERCE_MODE: "sandbox" }));
});

test("commerce transport is deadline/body bounded, authenticated, and cannot redirect credentials", async () => {
  let observed: RequestInit | undefined;
  const client = new EconomyCommerce(settings, async (_url, options) => {
    observed = options;
    return new Response(JSON.stringify(authoritativeOrder));
  });
  assert.equal((await client.order(identity)).kind, "available");
  assert.equal(observed?.redirect, "error");
  assert.equal(
    (observed?.headers as Record<string, string>).Authorization,
    `Bearer ${GATEWAY}`,
  );
  const stalled = new EconomyCommerce(
    settings,
    async () => new Promise<Response>(() => {}),
  );
  const start = Date.now();
  assert.equal((await stalled.order(identity)).kind, "unavailable");
  assert.ok(Date.now() - start < 1000);
  const oversized = new EconomyCommerce(
    settings,
    async () => new Response("x".repeat(65537)),
  );
  assert.equal((await oversized.order(identity)).kind, "unavailable");
  const aborted = new AbortController();
  aborted.abort();
  let calls = 0;
  const stopped = new EconomyCommerce(settings, async () => {
    calls++;
    return new Response("{}");
  });
  assert.equal(
    (await stopped.createPaymentLink(quote, aborted.signal)).kind,
    "unavailable",
  );
  assert.equal(calls, 0);
});
