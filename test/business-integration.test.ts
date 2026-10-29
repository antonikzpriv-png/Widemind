/** Explicit synthetic providers through the real business/runtime/Python ledger. */
import test from "node:test";
import assert from "node:assert/strict";
import { spawn, type ChildProcess } from "node:child_process";
import { once } from "node:events";
import { createHmac } from "node:crypto";
import {
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
  existsSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { createServer } from "node:net";
import { setTimeout as delay } from "node:timers/promises";
import { openDatabase } from "../src/db/database.ts";
import { AgentRuntime } from "../src/runtime/engine.ts";
import { Store } from "../src/runtime/store.ts";
import { registerModules } from "../src/modules/registry.ts";
import { createEconomyModule } from "../src/modules/economy/module.ts";
import { BusinessService } from "../src/modules/business/engine.ts";
import { EconomyCommerce } from "../src/modules/business/commerce.ts";
import { migrations } from "../src/modules/business/migrations.ts";
import { defaultPolicy } from "../src/modules/business/policy.ts";
import { money } from "../src/contracts/index.ts";
import type {
  Json,
  Reservation,
  RuntimeConfig,
  ToolAction,
  ToolReceipt,
  Tools,
} from "../src/contracts/index.ts";

const OPERATOR = "fixture-business-operator-" + "o".repeat(48);
const GATEWAY = "fixture-business-gateway-" + "g".repeat(48);
const CONTROL = "fixture-business-control-" + "c".repeat(48);
const SIGNING = "whsec_fixture_signing_secret";
const TOOLS = [
  "gmail.read",
  "gmail.send",
  "research.collect",
  "commerce.checkout",
  "commerce.fulfill",
];
const fixtureScope = {
  topic: "Explicit fixture comparison of durable workers",
  sections: ["Evidence", "Tradeoffs"],
  minSources: 2,
  sourceUrls: [
    "https://fixture.example/worker",
    "https://fixture.example/storage",
  ],
};

async function setup(
  options: { lostResearch?: boolean; lostDelivery?: boolean } = {},
) {
  const directory = mkdtempSync(join(tmpdir(), "widemind-business-fixture-"));
  const probe = createServer();
  await new Promise<void>((resolve) => probe.listen(0, "127.0.0.1", resolve));
  const port = (probe.address() as { port: number }).port;
  await new Promise<void>((resolve) => probe.close(() => resolve()));
  const backend = `http://127.0.0.1:${port}`;
  let child: ChildProcess | undefined;
  let spawnError: Error | undefined;
  let logs = "";
  let db: ReturnType<typeof openDatabase> | undefined;
  async function close() {
    db?.close();
    if (child?.pid && child.exitCode === null && child.signalCode === null) {
      const done = once(child, "exit");
      child.kill("SIGTERM");
      const timer = setTimeout(() => child?.kill("SIGKILL"), 3000);
      try {
        await done;
      } finally {
        clearTimeout(timer);
      }
    }
    rmSync(directory, { recursive: true, force: true });
  }
  try {
    child = spawn(
      process.env.WIDEMIND_TEST_PYTHON ?? "python3",
      [
        resolve("test/business-provider-fixture.py"),
        "--explicit-fixtures",
        "--database",
        join(directory, "economy.sqlite"),
        "--port",
        String(port),
      ],
      {
        env: { ...process.env, PYTHONPATH: resolve("src") },
        stdio: ["ignore", "pipe", "pipe"],
      },
    );
    child.on("error", (error) => {
      spawnError = error;
      logs = String(error);
    });
    for (const stream of [child.stdout, child.stderr])
      stream?.on("data", (part) => {
        logs = (logs + String(part)).slice(-8192);
      });
    const deadline = Date.now() + 15000;
    let ready = false;
    while (Date.now() < deadline) {
      if (spawnError) throw spawnError;
      if (child.exitCode !== null)
        throw new Error(`Explicit fixture backend exited: ${logs}`);
      try {
        if (
          (
            await fetch(backend + "/health", {
              signal: AbortSignal.timeout(400),
            })
          ).ok
        ) {
          ready = true;
          break;
        }
      } catch {
        /* Local readiness only. */
      }
      await delay(25);
    }
    if (!ready) throw new Error(`Fixture backend readiness failed: ${logs}`);
    async function request(path: string, body: unknown, token = GATEWAY) {
      const response = await fetch(backend + path, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify(body),
        signal: AbortSignal.timeout(5000),
      });
      assert.equal(response.status, 200, await response.clone().text());
      return response.json() as Promise<Record<string, unknown>>;
    }
    const rpc = (operation: string, body: unknown, token = GATEWAY) =>
      request(`/v1/runtime/${operation}`, body, token);
    const config: RuntimeConfig = {
      database: join(directory, "runtime.sqlite"),
      host: "127.0.0.1",
      port: 3000,
      publicOrigin: "http://127.0.0.1:3000",
      profile: "foundation",
      secureCookies: false,
      leaseMs: 3000,
      idleMs: 1000,
      dependencyMs: 5000,
    };
    db = openDatabase(config.database);
    const commerce = new EconomyCommerce({
      url: backend,
      operatorToken: OPERATOR,
      gatewayToken: GATEWAY,
      timeoutMs: 4000,
      mode: "sandbox",
    });
    const business = new BusinessService(db, commerce);
    const services = registerModules({ db, config }, [
      {
        id: "economy",
        contractVersion: 1,
        create: (context) =>
          createEconomyModule(context, {
            WIDEMIND_ECONOMY_URL: backend,
            WIDEMIND_GATEWAY_TOKEN: GATEWAY,
            WIDEMIND_OPERATOR_TOKEN: OPERATOR,
          }),
      },
      {
        id: "business",
        contractVersion: 1,
        migrations,
        create: () => ({ business }),
      },
    ]);
    const store = new Store(db);
    let runtime = new AgentRuntime(store, services, config);
    let lostResearch = options.lostResearch ?? false;
    let lostDelivery = options.lostDelivery ?? false;
    const calls: ToolAction[] = [];
    const disabled = new Set<string>();
    let incomingMessages: Json[] = [];
    let acknowledgment: Record<string, Json> | null = null;
    const journal = (id: string) =>
      join(directory, `fixture-provider-${id}.json`);
    const fixtureTools: Tools = {
      health: () => ({ available: true }),
      capabilities: () =>
        TOOLS.map((id) => ({
          id,
          availability: disabled.has(id)
            ? {
                available: false as const,
                reason: "Explicit fixture unavailable",
              }
            : { available: true as const },
        })),
      async execute(action, reservation, signal) {
        assert.equal(signal.aborted, false);
        const previous = existsSync(journal(action.actionId))
          ? (JSON.parse(
              readFileSync(journal(action.actionId), "utf8"),
            ) as ToolReceipt)
          : null;
        if (previous) return { kind: "receipt", receipt: previous };
        const verified = await rpc("verify", {
          action,
          reservation,
          dispatch: true,
        });
        assert.equal(verified.kind, "verified", JSON.stringify(verified));
        // Trusted fixture gateway records the first actual central dispatch,
        // rather than the possibly much older runtime plan creation time.
        const executionStartedAt = new Date().toISOString();
        calls.push(action);
        let result: Json;
        if (action.tool === "commerce.checkout") {
          const input = action.input as Record<string, Json>;
          const checkout = await commerce.createPaymentLink({
            runId: action.runId,
            jobId: action.jobId!,
            requestId: action.actionId,
            amountMicros: String(input.amountMicros),
            currency: "USD",
            description: String(input.description),
          });
          assert.equal(checkout.kind, "available", JSON.stringify(checkout));
          if (checkout.kind !== "available")
            throw new Error("Fixture Checkout unavailable");
          result = { order: checkout.order as unknown as Json };
        } else if (action.tool === "commerce.fulfill") {
          const input = action.input as Record<string, Json>;
          assert.ok(
            db!
              .prepare(
                "SELECT 1 FROM business_delivery_evidence WHERE run_id=? AND job_id=? AND evidence_ref=?",
              )
              .get(
                action.runId,
                action.jobId!,
                String(input.deliveryReference),
              ),
            "Only persisted authenticated delivery can recognize earnings",
          );
          const fulfillment = await commerce.recordFulfillment({
            runId: action.runId,
            jobId: action.jobId!,
            deliveryReference: String(input.deliveryReference),
            idempotencyKey: action.actionId,
          });
          assert.equal(
            fulfillment.kind,
            "available",
            JSON.stringify(fulfillment),
          );
          if (fulfillment.kind !== "available")
            throw new Error("Fixture fulfillment unavailable");
          result = { order: fulfillment.order as unknown as Json };
        } else if (action.tool === "research.collect") {
          const input = action.input as { sections: string[] };
          result = {
            executionStartedAt,
            sources: [
              {
                url: fixtureScope.sourceUrls[0]!,
                title: "Explicit worker fixture",
                excerpt: "A local HTTP fixture describes durable workers.",
              },
              {
                url: fixtureScope.sourceUrls[1]!,
                title: "Explicit storage fixture",
                excerpt: "A local HTTP fixture describes persistent storage.",
              },
            ],
            sections: input.sections.map((heading) => ({
              heading,
              text: "Explicit fixture research content; no live factual research is claimed.",
              sourceUrls: fixtureScope.sourceUrls,
            })),
          };
        } else if (action.tool === "gmail.send")
          result = {
            messageId: `fixture-sent-${action.actionId}`,
            deliveryState: "accepted",
          };
        else if (action.tool === "gmail.read") {
          result = {
            messages: incomingMessages,
            deliveryUpdates: acknowledgment ? [acknowledgment] : [],
          };
          incomingMessages = [];
          acknowledgment = null;
        } else throw new Error("Unsupported explicit fixture tool");
        const receipt: ToolReceipt = {
          version: 1,
          actionId: action.actionId,
          status: "succeeded",
          result,
          usage: {
            actualCharge: money(
              action.tool === "research.collect" ? "125000" : "0",
            ),
            provider: action.tool.startsWith("commerce.")
              ? "commerce"
              : action.tool === "research.collect"
                ? "openai"
                : "gmail",
            accountRef: action.tool.startsWith("commerce.")
              ? "fixture-commerce"
              : action.tool === "research.collect"
                ? "fixture-openai"
                : "fixture-mailbox",
            externalEventId: `fixture-final-${action.actionId}`,
            billableFailure: false,
            provisional: false,
            evidenceRef: `explicit-fixture:${action.actionId}`,
          },
        };
        writeFileSync(journal(action.actionId), JSON.stringify(receipt), {
          mode: 0o600,
        });
        if (action.tool === "research.collect" && lostResearch) {
          lostResearch = false;
          return {
            kind: "unknown",
            reason: "Explicit fixture research response lost",
          };
        }
        if (
          action.tool === "gmail.send" &&
          (action.input as Record<string, Json>).attachment &&
          lostDelivery
        ) {
          lostDelivery = false;
          return {
            kind: "unknown",
            reason: "Explicit fixture delivery response lost",
          };
        }
        return { kind: "receipt", receipt };
      },
      async reconcile(action, reservation) {
        const verified = await rpc("verify", {
          action,
          reservation,
          dispatch: false,
        });
        assert.equal(verified.kind, "verified", JSON.stringify(verified));
        return existsSync(journal(action.actionId))
          ? {
              kind: "receipt",
              receipt: JSON.parse(
                readFileSync(journal(action.actionId), "utf8"),
              ) as ToolReceipt,
            }
          : { kind: "unknown", reason: "No definitive fixture outcome" };
      },
    };
    services.tools = fixtureTools;
    async function newRun() {
      const runId = store.create("foundation");
      await rpc("seed", { runId, mode: "sandbox" }, OPERATOR);
      return runId;
    }
    async function tick(runId: string) {
      const run = store.run(runId)!;
      if (run.desired === "run") store.control(runId, "wake");
      await runtime.tick();
    }
    async function until(
      runId: string,
      predicate: () => boolean,
      maximum = 50,
    ) {
      for (let i = 0; i < maximum; i++) {
        if (predicate()) return;
        await tick(runId);
      }
      assert.ok(
        predicate(),
        `Fixture workflow stalled: ${JSON.stringify(store.run(runId))}; actions=${JSON.stringify(store.actions(runId).map((a) => ({ state: a.state, reason: a.reason, action: a.action_json })))}`,
      );
    }
    function rows(table: string, runId: string) {
      assert.ok(["business_opportunities", "business_orders"].includes(table));
      return db!
        .prepare(`SELECT * FROM ${table} WHERE run_id=? ORDER BY created_at,id`)
        .all(runId) as Record<string, unknown>[];
    }
    function order(runId: string) {
      return rows("business_orders", runId)[0];
    }
    async function offer(runId: string) {
      business.enqueue(runId, "fixture-policy", {
        command: "policy",
        policy: {
          ...defaultPolicy,
          prices: { "research-brief": "10000000" },
        } as unknown as Json,
      });
      const incoming = {
        messageId: "explicit-fixture-incoming-request",
        senderVerified: true,
        from: "explicit-fixture-customer@example.invalid",
        command: {
          type: "request",
          service: "research-brief",
          scope: fixtureScope,
        },
      };
      incomingMessages = [
        incoming,
        incoming,
        {
          ...incoming,
          messageId: "explicit-fixture-unverified-sender",
          senderVerified: false,
        },
      ];
      business.enqueue(runId, "fixture-request-inbox", {
        command: "sync-inbox",
      });
      store.control(runId, "resume");
      await until(runId, () =>
        Boolean(
          db!
            .prepare(
              "SELECT 1 FROM business_quotes q JOIN business_opportunities o ON o.id=q.opportunity_id WHERE o.run_id=? AND q.status='offered'",
            )
            .get(runId),
        ),
      );
      return db!
        .prepare(
          "SELECT q.id FROM business_quotes q JOIN business_opportunities o ON o.id=q.opportunity_id WHERE o.run_id=?",
        )
        .get(runId)!.id as string;
    }
    async function accept(runId: string, quoteId: string) {
      const incoming = {
        messageId: "explicit-fixture-incoming-acceptance",
        senderVerified: true,
        from: "explicit-fixture-customer@example.invalid",
        command: { type: "accept", quoteId },
      };
      incomingMessages = [incoming, incoming];
      const command = { command: "sync-inbox" };
      business.enqueue(runId, "fixture-accept", command);
      assert.equal(
        business.enqueue(runId, "fixture-accept", command).duplicate,
        true,
      );
      await until(runId, () => order(runId)?.status === "awaitingPayment");
      assert.equal(
        rows("business_orders", runId).length,
        1,
        "Duplicate authenticated messages cannot create a second order",
      );
    }
    async function payment(runId: string, refund = false) {
      if (refund)
        await request("/_fixture/control", { operation: "refund" }, CONTROL);
      const event = {
        id: refund
          ? "evt_fixture_business_refund"
          : "evt_fixture_business_paid",
        livemode: false,
        type: refund ? "charge.refunded" : "checkout.session.completed",
        data: { object: { id: refund ? "ch_fixture" : "cs_created" } },
      };
      const raw = JSON.stringify(event),
        timestamp = Math.floor(Date.now() / 1000);
      const digest = createHmac("sha256", SIGNING)
        .update(`${timestamp}.${raw}`)
        .digest("hex");
      for (let duplicate = 0; duplicate < 2; duplicate++) {
        const response = await fetch(backend + "/v1/webhooks/stripe", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "Stripe-Signature": `t=${timestamp},v1=${digest}`,
          },
          body: raw,
        });
        assert.equal(response.status, 202, await response.clone().text());
      }
      const drained = await request(
        "/_fixture/control",
        { operation: "drain" },
        CONTROL,
      );
      assert.equal(drained.completed, 1);
      store.control(runId, "wake");
    }
    function acknowledge(runId: string) {
      const sent = calls.find(
        (action) =>
          action.runId === runId &&
          action.tool === "gmail.send" &&
          (action.input as Record<string, Json>).attachment,
      );
      assert.ok(
        sent,
        "A real persisted fixture delivery action must precede its acknowledgment",
      );
      acknowledgment = {
        actionId: sent.actionId,
        jobId: sent.jobId!,
        deliveryState: "confirmed",
        evidenceRef: `explicit-fixture-authenticated-ack:${sent.actionId}`,
      };
      business.enqueue(runId, "fixture-inbox-ack", { command: "sync-inbox" });
    }
    return {
      close,
      db,
      business,
      store,
      services,
      config,
      calls,
      disabled,
      rpc,
      request,
      backend,
      newRun,
      tick,
      until,
      offer,
      accept,
      payment,
      order,
      rows,
      acknowledge,
      restartRuntime: () => {
        runtime = new AgentRuntime(store, services, config);
      },
    };
  } catch (error) {
    await close();
    throw error;
  }
}

test(
  "explicit fixture business request-to-delivery uses real runtime and central ledger across interruptions, duplicate payments, and a later refund",
  { timeout: 45000 },
  async () => {
    const f = await setup({ lostResearch: true, lostDelivery: true });
    try {
      const runId = await f.newRun();
      const quoteId = await f.offer(runId);
      assert.equal(
        f.rows("business_opportunities", runId).length,
        1,
        "Duplicate and unverified intake cannot create extra opportunities",
      );
      assert.equal(
        f.calls.filter((a) => a.workKey.endsWith(":offer")).length,
        1,
      );
      await f.accept(runId, quoteId);
      assert.equal(f.order(runId)!.price_micros, "10000000");
      assert.equal(
        (await f.services.economy.summary(runId))!.customerReceiptsMicros,
        "0",
        "A quote and Checkout are not cash",
      );
      await f.payment(runId);
      await f.until(runId, () =>
        f.store
          .actions(runId)
          .some(
            (a) =>
              a.state === "unresolved" &&
              JSON.parse(a.action_json).tool === "research.collect",
          ),
      );
      const held = (await f.services.economy.summary(runId))!;
      assert.equal(
        held.reservedMicros,
        "250000",
        "Lost paid outcome retains its full reservation before reconciliation",
      );
      f.store.control(runId, "stop");
      f.restartRuntime();
      await f.tick(runId);
      assert.equal(f.store.run(runId)!.state, "stopped");
      assert.equal(
        (await f.services.economy.summary(runId))!.operatingChargesMicros,
        "125000",
        "Stopped runtime still settles the known bill",
      );
      const recoveredResearch = f.store
        .actions(runId)
        .find((a) => JSON.parse(a.action_json).tool === "research.collect")!;
      const originalStartedAt = JSON.parse(recoveredResearch.receipt_json!)
        .result.executionStartedAt as string;
      assert.ok(Number.isFinite(Date.parse(originalStartedAt)));
      f.store.control(runId, "resume");
      await f.until(runId, () =>
        f.store
          .actions(runId)
          .some(
            (a) =>
              a.state === "unresolved" &&
              JSON.parse(a.action_json).workKey.endsWith(":delivery"),
          ),
      );
      const dueAt = f.order(runId)!.delivery_due_at;
      assert.equal(
        dueAt,
        new Date(
          Date.parse(originalStartedAt) +
            defaultPolicy.turnaroundSeconds * 1000,
        ).toISOString(),
        "Response-loss recovery preserves the original authorized execution time",
      );
      f.restartRuntime();
      await f.until(
        runId,
        () => f.order(runId)!.status === "awaitingAcknowledgment",
      );
      assert.equal(
        f.order(runId)!.delivery_due_at,
        dueAt,
        "Restart cannot extend the agreed deadline",
      );
      assert.equal(
        f.calls.filter((a) => a.tool === "research.collect").length,
        1,
      );
      assert.equal(
        f.calls.filter((a) => a.workKey.endsWith(":delivery")).length,
        1,
        "Unknown send is reconciled, never resent",
      );
      assert.equal(
        (await f.services.economy.summary(runId))!.earnedRevenueMicros,
        "0",
        "Transport acceptance is not fulfillment",
      );
      assert.match(String(f.order(runId)!.artifact), /scopeSections|citations/);
      f.acknowledge(runId);
      await f.until(runId, () => f.order(runId)!.status === "completed");
      const completed = (await f.services.economy.summary(runId))!;
      assert.equal(completed.customerReceiptsMicros, "10000000");
      assert.equal(completed.earnedRevenueMicros, "10000000");
      assert.equal(completed.operatingChargesMicros, "125000");
      assert.equal(completed.postedMicros, "14285000");
      const stats = await f.request(
        "/_fixture/control",
        { operation: "stats" },
        CONTROL,
      );
      assert.equal(stats.checkoutPosts, 1);
      await f.payment(runId, true);
      const refunded = (await f.services.economy.summary(runId))!;
      assert.equal(refunded.customerReceiptsMicros, "10000000");
      assert.equal(refunded.refundsMicros, "10000000");
      assert.equal(refunded.earnedRevenueMicros, "0");
      assert.equal(refunded.operatingChargesMicros, "125000");
      assert.equal(
        refunded.postedMicros,
        "4285000",
        "Processor fees and delivery costs survive a refund",
      );
      await f.until(
        runId,
        () =>
          JSON.parse(String(f.order(runId)!.payment)).refundsMicros ===
          "10000000",
      );
      assert.equal(
        f.order(runId)!.status,
        "completed",
        "A later refund changes financial evidence without inventing another delivery",
      );
      const evaluation = JSON.parse(
        String(
          f.db
            .prepare(
              "SELECT document FROM business_memory WHERE run_id=? AND kind='evaluation' ORDER BY created_at DESC,rowid DESC LIMIT 1",
            )
            .get(runId)!.document,
        ),
      );
      assert.equal(evaluation.earnedRevenueMicros, "0");
      assert.equal(
        evaluation.measuredContributionMicros,
        "-715000",
        "The stored lesson includes actual research cost and retained processor fees after refund",
      );
      assert.match(
        evaluation.lesson,
        /does not establish full business profitability/,
      );
      const before = f.calls.length;
      for (let i = 0; i < 5; i++) await f.tick(runId);
      assert.equal(
        f.calls.length,
        before,
        "Idle work never starts a paid searching loop",
      );
      if (process.env.WIDEMIND_EXPLICIT_BUSINESS_DEMO === "1")
        console.log(
          JSON.stringify({
            mode: "explicit-offline-fixtures",
            liveEffects: false,
            request: true,
            quote: true,
            paymentFixtureVerified: true,
            chargedResearchCalls: 1,
            uncertainSendReconciled: true,
            recipientAcknowledgmentFixture: true,
            completed: true,
            refundPreservedFeesAndCosts: true,
            finalPostedMicros: refunded.postedMicros,
            warning:
              "Synthetic customers, provider messages, usage and payments. No real service sale, delivery, revenue or profit.",
          }),
        );
    } finally {
      await f.close();
    }
  },
);

test(
  "explicit fixture runtime refuses new business effects while paused and unavailable research requires review",
  { timeout: 30000 },
  async () => {
    const f = await setup();
    try {
      const runId = await f.newRun();
      f.business.enqueue(runId, "paused-fixture", {
        command: "intake",
        service: "research-brief",
        customerEmail: "explicit-fixture@example.invalid",
        scope: fixtureScope,
      });
      await f.tick(runId);
      assert.equal(f.calls.length, 0);
      assert.equal(
        f.rows("business_opportunities", runId).length,
        0,
        "Paused intake remains in the durable inbox",
      );
      f.disabled.add("research.collect");
      f.store.control(runId, "resume");
      await f.until(
        runId,
        () =>
          f.rows("business_opportunities", runId)[0]?.status === "needsReview",
      );
      assert.equal(f.calls.length, 0);
      assert.equal(
        f.db.prepare("SELECT COUNT(*) n FROM business_quotes").get()!.n,
        0,
      );
    } finally {
      await f.close();
    }
  },
);

test(
  "explicit fixture actual central reservation exhaustion prevents research dispatch after a customer payment",
  { timeout: 30000 },
  async () => {
    const f = await setup();
    try {
      const runId = await f.newRun();
      await f.accept(runId, await f.offer(runId));
      await f.payment(runId);
      const blocked = await f.rpc("authorize", {
        runId,
        actionId: "fixture-other-open-reservation",
        tool: "research.collect",
        limit: {
          maximum: money("4900000"),
          maxTokens: 1000,
          maxDurationMs: 1000,
          maxOutputBytes: 4096,
        },
      });
      assert.equal(blocked.kind, "authorized");
      await f.until(runId, () => f.store.run(runId)!.state === "exhausted");
      assert.equal(
        f.calls.filter((a) => a.tool === "research.collect").length,
        0,
      );
      assert.equal(
        f.calls.filter((a) => a.workKey.endsWith(":commitment")).length,
        0,
        "No delivery promise precedes operating authorization",
      );
      assert.equal(f.order(runId)!.delivery_due_at, null);
      assert.equal(
        (await f.services.economy.summary(runId))!.operatingChargesMicros,
        "0",
      );
      assert.notEqual(f.order(runId)!.status, "completed");
      const plannedResearch = f.store
        .actions(runId)
        .find((a) => JSON.parse(a.action_json).tool === "research.collect")!;
      assert.equal(plannedResearch.state, "planned");
      // Explicit test-only aging: a plan can wait days for operating credit.
      const agedPlanAt = Date.now() - 7 * 86400000;
      f.db
        .prepare("UPDATE runtime_actions SET created_at=? WHERE id=?")
        .run(agedPlanAt, plannedResearch.id);
      const otherReservation = blocked.reservation as { reservationId: string };
      await f.request(
        `/v1/reservations/${otherReservation.reservationId}/cancel`,
        {},
      );
      const fundsReleasedAt = Date.now();
      f.store.control(runId, "resume");
      await f.until(
        runId,
        () => f.order(runId)!.status === "awaitingAcknowledgment",
      );
      const performed = f.store
        .actions(runId)
        .find((a) => a.id === plannedResearch.id)!;
      const actualStart = JSON.parse(performed.receipt_json!).result
        .executionStartedAt as string;
      assert.ok(
        Date.parse(actualStart) >= fundsReleasedAt,
        "A waiting plan has no execution start until funds are authorized",
      );
      assert.equal(
        f.order(runId)!.delivery_due_at,
        new Date(
          Date.parse(actualStart) + defaultPolicy.turnaroundSeconds * 1000,
        ).toISOString(),
      );
      assert.ok(
        Date.parse(String(f.order(runId)!.delivery_due_at)) > Date.now(),
        "An aged unfunded plan cannot create an already expired delivery promise",
      );
      assert.equal(
        f.calls.filter((a) => a.tool === "research.collect").length,
        1,
      );
    } finally {
      await f.close();
    }
  },
);
