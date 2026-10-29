import test from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { randomBytes } from "node:crypto";
import { createServer } from "node:net";
import { setTimeout as delay } from "node:timers/promises";
import {
  createEconomyClient,
  createEconomyAdapter,
  exactAmount,
} from "../src/modules/economy/client.ts";
import { money } from "../src/contracts/index.ts";
const python =
  process.env.WIDEMIND_ECONOMY_PYTHON ??
  (existsSync(resolve(".venvs/economy/bin/python"))
    ? resolve(".venvs/economy/bin/python")
    : resolve("services/economy/.venv/bin/python"));
test("economy adapter configuration and exact monetary input fail closed", () => {
  assert.equal(createEconomyClient({}), null);
  assert.throws(() =>
    createEconomyClient({ WIDEMIND_ECONOMY_URL: "http://127.0.0.1:8080" }),
  );
  for (const amount of [1, "1.2", "-1", "01", "9000000000000001"])
    assert.throws(() => exactAmount(amount));
  assert.equal(exactAmount("5000000"), 5000000);
});
test(
  "real isolated HTTP ledger integration: opening, concurrent reservations, zero-cost and provisional settlement",
  {
    skip: !existsSync(python)
      ? "Install services/economy in its isolated venv to run this integration"
      : false,
  },
  async () => {
    const directory = await mkdtemp(join(tmpdir(), "widemind-economy-node-"));
    const probe = createServer();
    await new Promise<void>((resolve) => probe.listen(0, "127.0.0.1", resolve));
    const address = probe.address();
    assert.ok(address && typeof address !== "string");
    const port = address.port;
    await new Promise<void>((resolve) => probe.close(() => resolve()));
    const tokens = {
      operator: randomBytes(32).toString("hex"),
      gateway: randomBytes(32).toString("hex"),
      agent: randomBytes(32).toString("hex"),
    };
    const child = spawn(
      python,
      [
        "-m",
        "widemind.economy.cli",
        "--host",
        "127.0.0.1",
        "--port",
        String(port),
      ],
      {
        env: {
          ...process.env,
          WIDEMIND_ECONOMY_DB: join(directory, "economy.sqlite"),
          WIDEMIND_OPERATOR_TOKEN: tokens.operator,
          WIDEMIND_GATEWAY_TOKEN: tokens.gateway,
          WIDEMIND_AGENT_TOKEN: tokens.agent,
          WIDEMIND_AGENT_RUN_ID: "integration-run",
          WIDEMIND_PAYMENT_PROVIDER: "paypal",
        },
        stdio: ["ignore", "ignore", "pipe"],
      },
    );
    child.stderr?.resume();
    try {
      const origin = `http://127.0.0.1:${port}`;
      let ready = false;
      for (let i = 0; i < 100; i++) {
        try {
          if ((await fetch(origin + "/health")).ok) {
            ready = true;
            break;
          }
        } catch {}
        await delay(30);
      }
      assert.ok(ready, "Actual Python HTTP service starts");
      const client = createEconomyClient({
        WIDEMIND_ECONOMY_URL: origin,
        WIDEMIND_ECONOMY_OPERATOR_TOKEN: tokens.operator,
        WIDEMIND_ECONOMY_GATEWAY_TOKEN: tokens.gateway,
        WIDEMIND_ECONOMY_AGENT_TOKEN: tokens.agent,
      })!;
      const economy = createEconomyAdapter(client);
      assert.equal(await economy.summary("integration-run"), null);
      await client.request("operator", "/v1/runs", {
        run_id: "integration-run",
        mode: "test",
      });
      assert.equal(
        (await economy.summary("integration-run"))?.spendableMicros,
        "5000000",
      );
      assert.equal((await economy.summary("integration-run"))?.mode, "sandbox");
      const limit = {
        maximum: money("3000000"),
        maxDurationMs: 1000,
        maxOutputBytes: 4096,
      };
      const contenders = await Promise.all(
        ["action-one", "action-two"].map((actionId) =>
          economy.authorize({
            runId: "integration-run",
            actionId,
            tool: "files",
            limit,
          }),
        ),
      );
      assert.equal(
        contenders.filter((item) => item.kind === "authorized").length,
        1,
      );
      assert.equal(
        contenders.filter((item) => item.kind === "insufficient").length,
        1,
      );
      const authorization = contenders.find(
        (item) => item.kind === "authorized",
      )!;
      assert.equal(authorization.kind, "authorized");
      if (authorization.kind !== "authorized") throw new Error("No grant");
      const reservation = authorization.reservation;
      await client.request(
        "gateway",
        `/v1/reservations/${reservation.reservationId}/start`,
        {},
        "POST",
      );
      const receipt = {
        version: 1 as const,
        actionId: reservation.actionId,
        status: "failed" as const,
        result: null,
        usage: {
          actualCharge: money("100000"),
          provider: "files",
          accountRef: client.toolsAccount,
          externalEventId: "provider-charge-one",
          billableFailure: true,
          provisional: true,
          evidenceRef: "fixture-observed-provider-usage",
        },
      };
      assert.equal(
        (await economy.settle(reservation, receipt)).kind,
        "unavailable",
      );
      assert.equal(
        (await economy.summary("integration-run"))?.reservedMicros,
        "2900000",
      );
      receipt.usage.provisional = false;
      assert.equal(
        (await economy.settle(reservation, receipt)).kind,
        "settled",
      );
      assert.equal(
        (await economy.summary("integration-run"))?.operatingChargesMicros,
        "100000",
      );
      assert.equal(
        (await economy.summary("integration-run"))?.reservedMicros,
        "0",
      );
      assert.equal(
        (await economy.settle(reservation, receipt)).kind,
        "settled",
      );
      const free = await economy.authorize({
        runId: "integration-run",
        actionId: "free-action",
        tool: "files",
        limit: { ...limit, maximum: money("0") },
      });
      assert.equal(free.kind, "authorized");
      if (free.kind !== "authorized") throw new Error("No free grant");
      await client.request(
        "gateway",
        `/v1/reservations/${free.reservation.reservationId}/start`,
        {},
        "POST",
      );
      assert.equal(
        (
          await economy.settle(free.reservation, {
            ...receipt,
            actionId: "free-action",
            status: "succeeded",
            usage: {
              ...receipt.usage,
              actualCharge: money("0"),
              externalEventId: "free-charge",
              billableFailure: false,
            },
          })
        ).kind,
        "settled",
      );
      assert.equal(
        (
          await client.request<{ ok: boolean }>(
            "operator",
            "/v1/runs/integration-run/integrity",
          )
        ).ok,
        true,
      );
      await assert.rejects(() =>
        client.request("operator", "/v1/runs", {
          run_id: "unattested-live",
          mode: "live",
        }),
      );
    } finally {
      if (child.exitCode === null) {
        const done = new Promise<void>((resolve) =>
          child.once("exit", () => resolve()),
        );
        child.kill("SIGTERM");
        const timer = setTimeout(() => child.kill("SIGKILL"), 2000);
        await done;
        clearTimeout(timer);
      }
      await rm(directory, { recursive: true, force: true });
    }
  },
);

test("economy bridge rejects unsafe numeric JSON instead of rounding", async () => {
  const { createServer } = await import("node:http");
  const server = createServer((_request, response) => {
    response.setHeader("Content-Type", "application/json");
    response.end('{"postedMicros":9007199254740993}');
  });
  try {
    await new Promise<void>((resolve) =>
      server.listen(0, "127.0.0.1", resolve),
    );
    const address = server.address();
    assert.ok(address && typeof address !== "string");
    const client = createEconomyClient({
      WIDEMIND_ECONOMY_URL: `http://127.0.0.1:${address.port}`,
      WIDEMIND_ECONOMY_OPERATOR_TOKEN: "operator-fixture-" + "o".repeat(32),
      WIDEMIND_ECONOMY_GATEWAY_TOKEN: "gateway-fixture-" + "g".repeat(32),
      WIDEMIND_ECONOMY_AGENT_TOKEN: "agent-fixture-" + "a".repeat(32),
    })!;
    await assert.rejects(
      () => client.request("operator", "/v1/unsafe"),
      /Unsafe numeric/,
    );
  } finally {
    await new Promise<void>((resolve) => server.close(() => resolve()));
  }
});
