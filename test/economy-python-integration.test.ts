/** Real Node/Python/SQLite integration; all tool usage is explicitly synthetic. */
import test from "node:test";
import assert from "node:assert/strict";
import { spawn, type ChildProcess } from "node:child_process";
import { once } from "node:events";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { createServer as netServer } from "node:net";
import { setTimeout as delay } from "node:timers/promises";
import { openDatabase } from "../src/db/database.ts";
import { Store } from "../src/runtime/store.ts";
import { AgentRuntime } from "../src/runtime/engine.ts";
import { registerModules } from "../src/modules/registry.ts";
import { createEconomyModule } from "../src/modules/economy/module.ts";
import { hashPassword } from "../src/auth/password.ts";
import { webServer } from "../src/web/server.ts";
import { money } from "../src/contracts/index.ts";
import type {
  BudgetLimit,
  Json,
  Reservation,
  RuntimeConfig,
  ToolAction,
  ToolReceipt,
} from "../src/contracts/index.ts";

const operatorToken = "fixture-operator-" + "o".repeat(48);
const gatewayToken = "fixture-gateway-" + "g".repeat(48);
const profiles = {
  "fixture.tool": {
    provider: "fixture-provider",
    account: "fixture-account",
    mode: "sandbox",
  },
};
const limit = (maximum = "1000000"): BudgetLimit => ({
  maximum: money(maximum),
  maxDurationMs: 1000,
  maxOutputBytes: 1024,
});

async function freePort(): Promise<number> {
  const server = netServer();
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const port = (server.address() as { port: number }).port;
  await new Promise<void>((resolve, reject) =>
    server.close((error) => (error ? reject(error) : resolve())),
  );
  return port;
}

async function setup() {
  const directory = mkdtempSync(join(tmpdir(), "widemind-cross-language-"));
  const backendPort = await freePort();
  const backendUrl = `http://127.0.0.1:${backendPort}`;
  let child: ChildProcess | null = null;
  let output = "";
  const env = {
    ...process.env,
    PYTHONPATH: resolve("src"),
    WIDEMIND_ECONOMY_DB: join(directory, "economy.sqlite3"),
    WIDEMIND_OPERATOR_TOKEN: operatorToken,
    WIDEMIND_GATEWAY_TOKEN: gatewayToken,
    WIDEMIND_AGENT_TOKEN: "fixture-agent-" + "a".repeat(48),
    WIDEMIND_AGENT_RUN_ID: "fixture-default",
    WIDEMIND_RUNTIME_AUTH_SECRET: "fixture-runtime-" + "r".repeat(48),
    WIDEMIND_RUNTIME_TOOL_PROFILES: JSON.stringify(profiles),
    // Never inherit an owner's real provider account into a fixture process.
    WIDEMIND_STRIPE_API_KEY: "",
    WIDEMIND_STRIPE_ACCOUNT: "",
    WIDEMIND_STRIPE_WEBHOOK_SECRET: "",
    WIDEMIND_CHECKOUT_SUCCESS_URL: "",
    WIDEMIND_CHECKOUT_CANCEL_URL: "",
    WIDEMIND_STRIPE_MODE: "test",
  };
  async function startBackend() {
    output = "";
    child = spawn(
      process.env.WIDEMIND_TEST_PYTHON ?? "python3",
      [
        "-m",
        "uvicorn",
        "widemind.economy.app:create_app",
        "--factory",
        "--host",
        "127.0.0.1",
        "--port",
        String(backendPort),
        "--no-access-log",
      ],
      { env, stdio: ["ignore", "pipe", "pipe"] },
    );
    child.on("error", (error) => {
      output = String(error);
    });
    for (const stream of [child.stdout, child.stderr])
      stream?.on("data", (chunk) => {
        output = (output + chunk.toString()).slice(-8192);
      });
    const deadline = Date.now() + 15000;
    while (Date.now() < deadline) {
      if (child.exitCode !== null)
        throw new Error(`Python economy exited before readiness: ${output}`);
      try {
        const response = await fetch(backendUrl + "/health", {
          signal: AbortSignal.timeout(500),
        });
        if (response.ok) return;
      } catch {
        /* Bounded readiness polling, no provider calls. */
      }
      await delay(30);
    }
    throw new Error(`Python economy readiness failed: ${output}`);
  }
  async function stopBackend() {
    if (!child || child.exitCode !== null) return;
    const current = child;
    const exited = once(current, "exit");
    current.kill("SIGTERM");
    const timer = setTimeout(() => {
      if (current.exitCode === null) current.kill("SIGKILL");
    }, 5000);
    try {
      await exited;
    } finally {
      clearTimeout(timer);
    }
    child = null;
  }
  let server: ReturnType<typeof webServer> | undefined;
  let db: ReturnType<typeof openDatabase> | undefined;
  try {
    await startBackend();
    const config: RuntimeConfig = {
      database: join(directory, "runtime.sqlite3"),
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
    const services = registerModules({ db, config }, [
      {
        id: "economy",
        contractVersion: 1,
        create: (context) =>
          createEconomyModule(context, {
            WIDEMIND_ECONOMY_URL: backendUrl,
            WIDEMIND_GATEWAY_TOKEN: gatewayToken,
            WIDEMIND_OPERATOR_TOKEN: operatorToken,
          }),
      },
    ]);
    const store = new Store(db);
    const runtime = new AgentRuntime(store, services, config);
    server = webServer({
      db,
      config,
      services,
      secrets: {
        operatorPasswordHash: await hashPassword("fixture-operator-password"),
      },
    });
    await new Promise<void>((resolve) =>
      server!.listen(0, "127.0.0.1", resolve),
    );
    const origin = `http://127.0.0.1:${(server.address() as { port: number }).port}`;
    config.publicOrigin = origin;
    const login = await fetch(origin + "/api/operator/login", {
      method: "POST",
      headers: { Origin: origin, "Content-Type": "application/json" },
      body: JSON.stringify({ password: "fixture-operator-password" }),
    });
    assert.equal(login.status, 200);
    const cookie = login.headers.get("set-cookie")!.split(";")[0]!;
    const { csrf } = (await login.json()) as { csrf: string };
    const headers = {
      Cookie: cookie,
      Origin: origin,
      "X-CSRF-Token": csrf,
      "Content-Type": "application/json",
    };
    async function rpc(operation: string, body: unknown, role = "gateway") {
      const response = await fetch(backendUrl + `/v1/runtime/${operation}`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${role === "operator" ? operatorToken : gatewayToken}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify(body),
      });
      assert.equal(response.status, 200, await response.clone().text());
      return response.json() as Promise<Record<string, unknown>>;
    }
    async function newRun(funded = true) {
      const created = await fetch(origin + "/api/operator/runs", {
        method: "POST",
        headers,
        body: "{}",
      });
      assert.equal(created.status, 201);
      const { runId } = (await created.json()) as { runId: string };
      if (funded) {
        const opened = await fetch(origin + "/api/modules/economy/open", {
          method: "POST",
          headers,
          body: JSON.stringify({ runId, mode: "sandbox" }),
        });
        assert.equal(opened.status, 200, await opened.clone().text());
      }
      return runId;
    }
    async function verify(
      action: ToolAction,
      reservation: Reservation,
      dispatch: boolean,
    ) {
      const value = await rpc("verify", { action, reservation, dispatch });
      assert.equal(value.kind, "verified", JSON.stringify(value));
    }
    function selectOnce(maximum = "1000000") {
      services.business = {
        health: () => ({ available: true }),
        selectWork: async (snapshot) =>
          snapshot.actions.length
            ? { kind: "wait", reason: "Synthetic fixture complete" }
            : {
                kind: "work",
                candidate: {
                  workKey: "fixture-once",
                  tool: "fixture.tool",
                  input: { fixture: true },
                  limit: limit(maximum),
                },
              },
      };
    }
    function toolReceipt(
      action: ToolAction,
      micros: string,
      provisional = false,
      eventSuffix = "final",
      status: "succeeded" | "failed" = "succeeded",
    ): ToolReceipt {
      return {
        version: 1,
        actionId: action.actionId,
        status,
        result: { fixture: true },
        usage: {
          actualCharge: money(micros),
          provider: "fixture-provider",
          accountRef: "fixture-account",
          externalEventId: `${action.actionId}:${eventSuffix}`,
          billableFailure: status === "failed",
          provisional,
          evidenceRef: "synthetic-provider-usage",
        },
      };
    }
    async function close() {
      if (server)
        await new Promise<void>((resolve) => server!.close(() => resolve()));
      db?.close();
      await stopBackend();
      rmSync(directory, { recursive: true, force: true });
    }
    return {
      services,
      store,
      runtime,
      config,
      db,
      origin,
      headers,
      backendUrl,
      rpc,
      newRun,
      verify,
      selectOnce,
      toolReceipt,
      startBackend,
      stopBackend,
      close,
    };
  } catch (error) {
    if (server)
      await new Promise<void>((resolve) => server!.close(() => resolve()));
    db?.close();
    await stopBackend();
    rmSync(directory, { recursive: true, force: true });
    throw error;
  }
}

test(
  "real application funds only an authenticated existing run and atomically admits concurrent caps",
  { timeout: 30000 },
  async () => {
    const f = await setup();
    try {
      const runId = await f.newRun(false);
      assert.equal(await f.services.economy.summary(runId), null);
      const unauth = await fetch(f.origin + "/api/modules/economy/open", {
        method: "POST",
        headers: { Origin: f.origin, "Content-Type": "application/json" },
        body: JSON.stringify({ runId, mode: "sandbox" }),
      });
      assert.equal(unauth.status, 401);
      for (let i = 0; i < 2; i++) {
        const response = await fetch(f.origin + "/api/modules/economy/open", {
          method: "POST",
          headers: f.headers,
          body: JSON.stringify({ runId, mode: "sandbox" }),
        });
        assert.equal(response.status, 200);
        assert.equal(
          ((await response.json()) as { postedMicros: string }).postedMicros,
          "5000000",
        );
      }
      const authorizations = await Promise.all(
        ["first", "second"].map((actionId) =>
          f.services.economy.authorize({
            runId,
            actionId,
            tool: "fixture.tool",
            limit: limit("4000000"),
          }),
        ),
      );
      assert.equal(
        authorizations.filter((x) => x.kind === "authorized").length,
        1,
      );
      assert.equal(
        authorizations.filter((x) => x.kind === "insufficient").length,
        1,
      );
      assert.equal(
        (await f.services.economy.summary(runId))!.spendableMicros,
        "1000000",
      );
      const granted = authorizations.find((x) => x.kind === "authorized")!;
      assert.equal(granted.kind, "authorized");
      if (granted.kind !== "authorized")
        throw new Error("Expected authorization");
      const action: ToolAction = {
        version: 1,
        actionId: granted.reservation.actionId,
        runId,
        workKey: "first",
        tool: "fixture.tool",
        input: {},
        limit: limit("4000000"),
      };
      const forged = await f.rpc("verify", {
        action,
        reservation: { ...granted.reservation, authorization: "forged" },
        dispatch: true,
      });
      assert.equal(forged.kind, "unavailable");
      const projection = (await (
        await fetch(f.origin + "/api/public")
      ).json()) as { financial: { spendableMicros: string } };
      assert.equal(projection.financial.spendableMicros, "1000000");
      assert.ok(!JSON.stringify(projection).includes(gatewayToken));
    } finally {
      await f.close();
    }
  },
);

test(
  "runtime executes true zero-cost work and records failed-but-billed synthetic usage through the same ledger",
  { timeout: 30000 },
  async () => {
    for (const [cap, cost, status] of [
      ["0", "0", "succeeded"],
      ["1000000", "100001", "failed"],
    ] as const) {
      const f = await setup();
      try {
        const runId = await f.newRun();
        f.selectOnce(cap);
        let calls = 0;
        f.services.tools = {
          health: () => ({ available: true }),
          capabilities: () => [
            { id: "fixture.tool", availability: { available: true } },
          ],
          execute: async (action, reservation) => {
            await f.verify(action, reservation, true);
            calls++;
            return {
              kind: "receipt",
              receipt: f.toolReceipt(action, cost, false, "final", status),
            };
          },
          reconcile: async () => ({
            kind: "unknown",
            reason: "Must not replay",
          }),
        };
        f.store.control(runId, "resume");
        assert.equal(await f.runtime.tick(), true);
        assert.equal(
          f.store.actions(runId)[0]!.state,
          status === "failed" ? "failed" : "completed",
        );
        assert.equal(
          (await f.services.economy.summary(runId))!.postedMicros,
          String(5000000n - BigInt(cost)),
        );
        assert.equal(
          (await f.services.economy.summary(runId))!.reservedMicros,
          "0",
        );
        await f.runtime.tick();
        assert.equal(calls, 1);
        const rows = (await (
          await fetch(f.backendUrl + `/v1/runs/${runId}/reservations`, {
            headers: { Authorization: `Bearer ${gatewayToken}` },
          })
        ).json()) as Array<{ amount_micro_usd: number }>;
        assert.equal(rows[0]!.amount_micro_usd, Number(cap));
      } finally {
        await f.close();
      }
    }
  },
);

test(
  "provisional usage survives backend restart and finalizes a new provider event without reexecution",
  { timeout: 30000 },
  async () => {
    const f = await setup();
    try {
      const runId = await f.newRun();
      f.selectOnce();
      let executions = 0,
        reconciliations = 0;
      f.services.tools = {
        health: () => ({ available: true }),
        capabilities: () => [
          { id: "fixture.tool", availability: { available: true } },
        ],
        execute: async (action, reservation) => {
          await f.verify(action, reservation, true);
          executions++;
          return {
            kind: "receipt",
            receipt: f.toolReceipt(action, "250000", true, "partial"),
          };
        },
        reconcile: async (action, reservation) => {
          await f.verify(action, reservation, false);
          reconciliations++;
          return {
            kind: "receipt",
            receipt: f.toolReceipt(action, "500000", false, "final"),
          };
        },
      };
      f.store.control(runId, "resume");
      await f.runtime.tick();
      assert.equal(f.store.actions(runId)[0]!.state, "unresolved");
      assert.equal(
        (await f.services.economy.summary(runId))!.postedMicros,
        "4750000",
      );
      assert.equal(
        (await f.services.economy.summary(runId))!.reservedMicros,
        "750000",
      );
      await f.stopBackend();
      await f.startBackend();
      const restored = new AgentRuntime(new Store(f.db), f.services, f.config);
      f.store.control(runId, "wake");
      await restored.tick();
      assert.equal(f.store.actions(runId)[0]!.state, "completed");
      assert.equal(executions, 1);
      assert.equal(reconciliations, 1);
      assert.equal(
        (await f.services.economy.summary(runId))!.postedMicros,
        "4500000",
      );
      assert.equal(
        (await f.services.economy.summary(runId))!.reservedMicros,
        "0",
      );
    } finally {
      await f.close();
    }
  },
);

test(
  "known overage remains a negative liability when optional output violates the runtime bound",
  { timeout: 30000 },
  async () => {
    const f = await setup();
    try {
      const runId = await f.newRun();
      f.selectOnce();
      let executions = 0;
      f.services.tools = {
        health: () => ({ available: true }),
        capabilities: () => [
          { id: "fixture.tool", availability: { available: true } },
        ],
        execute: async (action, reservation) => {
          await f.verify(action, reservation, true);
          executions++;
          const receipt = f.toolReceipt(action, "6000000");
          receipt.result = { oversized: "x".repeat(20000) };
          return { kind: "receipt", receipt };
        },
        reconcile: async () => ({
          kind: "unknown",
          reason: "No duplicate effect",
        }),
      };
      f.store.control(runId, "resume");
      await f.runtime.tick();
      const summary = (await f.services.economy.summary(runId))!;
      assert.equal(summary.postedMicros, "-1000000");
      assert.equal(summary.reservedMicros, "0");
      assert.equal(f.store.actions(runId)[0]!.state, "failed");
      assert.equal(f.store.run(runId)!.state, "exhausted");
      const denied = await f.services.economy.authorize({
        runId,
        actionId: "another-paid-action",
        tool: "fixture.tool",
        limit: limit("1"),
      });
      assert.equal(denied.kind, "insufficient");
      assert.equal(executions, 1);
    } finally {
      await f.close();
    }
  },
);

test(
  "definitive no-effect proofs close the third attempt and release the cap without a fourth execution",
  { timeout: 30000 },
  async () => {
    const f = await setup();
    try {
      const runId = await f.newRun();
      f.selectOnce();
      let executions = 0;
      f.services.tools = {
        health: () => ({ available: true }),
        capabilities: () => [
          { id: "fixture.tool", availability: { available: true } },
        ],
        execute: async (action, reservation) => {
          await f.verify(action, reservation, true);
          executions++;
          return { kind: "unknown", reason: "Synthetic lost response" };
        },
        reconcile: async () => ({
          kind: "not_performed",
          evidenceRef: `definitive-fixture-zero-${executions}`,
        }),
      };
      f.store.control(runId, "resume");
      for (let i = 0; i < 6; i++) {
        await f.runtime.tick();
        if (f.store.actions(runId)[0]?.state === "failed") break;
        f.store.control(runId, "wake");
      }
      assert.equal(executions, 3);
      assert.equal(f.store.actions(runId)[0]!.state, "failed");
      const summary = (await f.services.economy.summary(runId))!;
      assert.equal(summary.postedMicros, "5000000");
      assert.equal(summary.reservedMicros, "0");
      assert.equal(f.store.actions(runId)[0]!.receipt_json, null);
    } finally {
      await f.close();
    }
  },
);
