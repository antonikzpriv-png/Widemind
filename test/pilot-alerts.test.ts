import test from "node:test";
import assert from "node:assert/strict";
import type { FinancialSummary } from "../src/contracts/index.ts";
import {
  alertPass,
  alertSettings,
  type AlertSettings,
} from "../src/runtime/alerts.ts";
import { fixture } from "./helpers.ts";

const summary = (spendableMicros: string): FinancialSummary => ({
  currency: "USD",
  openingCreditMicros: "5000000",
  postedMicros: spendableMicros,
  reservedMicros: "0",
  holdsMicros: "0",
  spendableMicros,
  customerReceiptsMicros: "0",
  earnedRevenueMicros: "0",
  operatingChargesMicros: "0",
  ownerFundingMicros: "0",
  subsidiesMicros: "0",
  mode: "live",
  asOf: "2026-11-04T12:00:00Z",
});

function hook(status = 200) {
  const calls: { url: string; body: any; auth?: string | null }[] = [];
  let letters: unknown[] | null = null;
  const transport = (async (url: string, init?: RequestInit) => {
    const headers = new Headers(init?.headers);
    if (url.includes("/v1/reconciliation/tasks")) {
      calls.push({ url, body: null, auth: headers.get("Authorization") });
      return letters === null
        ? new Response("{}", { status: 503 })
        : Response.json(letters);
    }
    calls.push({ url, body: JSON.parse(String(init?.body)) });
    return new Response("", { status });
  }) as typeof fetch;
  return {
    calls,
    transport,
    setLetters(value: unknown[] | null) {
      letters = value;
    },
    webhooks: () => calls.filter((call) => call.body !== null),
  };
}

test("alert settings reject insecure or credentialed webhooks and short repeats", () => {
  assert.throws(
    () => alertSettings({ WIDEMIND_ALERT_WEBHOOK_URL: "http://example.com/x" }),
    /HTTPS/,
  );
  assert.throws(
    () =>
      alertSettings({
        WIDEMIND_ALERT_WEBHOOK_URL: "https://u:p@example.com/x",
      }),
    /credentials/,
  );
  assert.throws(
    () => alertSettings({ WIDEMIND_ALERT_REPEAT_MINUTES: "1" }),
    /at least 5/,
  );
  const ok = alertSettings({
    WIDEMIND_ALERT_WEBHOOK_URL: "https://hooks.example.com/secret-path",
    WIDEMIND_ECONOMY_URL: "http://127.0.0.1:8080",
    WIDEMIND_ECONOMY_OPERATOR_TOKEN: "t",
  });
  assert.equal(ok.repeatMs, 360 * 60_000);
  assert.deepEqual(ok.economy, {
    url: "http://127.0.0.1:8080",
    operatorToken: "t",
  });
});

test("alerts notify on raise, repeat and clear, retry failed delivery, and never leak secrets", async () => {
  const f = fixture();
  try {
    const id = f.store.create("demo");
    const channel = hook();
    const settings: AlertSettings = {
      webhookUrl: "https://hooks.example.com/secret-path",
      repeatMs: 60 * 60_000,
      economy: {
        url: "http://127.0.0.1:8080",
        operatorToken: "operator-secret",
      },
    };
    let spendable = "1000000";
    f.services.economy.health = () => ({ available: true });
    f.services.economy.summary = async () => summary(spendable);
    const pass = (now: number, transport = channel.transport) =>
      alertPass({
        db: f.db,
        services: f.services,
        config: f.config,
        runId: id,
        settings,
        transport,
        now,
      });

    // No worker heartbeat; dead letters unobservable.
    let t = Date.parse("2026-11-04T10:00:00Z");
    let result = await pass(t);
    assert.deepEqual(result.active, ["worker_down"]);
    assert.equal(
      result.conditions.find((c) => c.id === "reconciliation_dead_letters")
        ?.active,
      null,
    );
    assert.equal(channel.webhooks().length, 1);
    assert.equal(channel.webhooks()[0]!.body.alerts[0].change, "raised");

    // Same state again: nothing new to send.
    result = await pass((t += 60_000));
    assert.equal(result.notices.length, 0);
    assert.equal(channel.webhooks().length, 1);

    // Exhaustion and dead letters raise; delivery fails, then is retried.
    spendable = "0";
    channel.setLetters([{ task_id: "private-task" }]);
    const failing = hook(500);
    failing.setLetters([{ task_id: "private-task" }]);
    result = await pass((t += 60_000), failing.transport);
    assert.equal(result.delivered, false);
    result = await pass((t += 60_000));
    assert.deepEqual(
      result.notices.map((n) => `${n.condition}:${n.change}`).sort(),
      ["credit_exhausted:raised", "reconciliation_dead_letters:raised"],
    );
    const auth = channel.calls.find((call) => call.auth)?.auth;
    assert.equal(auth, "Bearer operator-secret");

    // Repeat after the interval for still-active conditions.
    result = await pass((t += 61 * 60_000));
    assert.deepEqual(
      result.notices.map((n) => n.change),
      ["repeat", "repeat", "repeat"],
    );

    // Recovery clears.
    spendable = "2000000";
    channel.setLetters([]);
    result = await pass((t += 60_000));
    assert.deepEqual(
      result.notices.map((n) => `${n.condition}:${n.change}`).sort(),
      ["credit_exhausted:cleared", "reconciliation_dead_letters:cleared"],
    );

    const sent = JSON.stringify(channel.webhooks().map((call) => call.body));
    for (const secret of ["operator-secret", "secret-path", "private-task", id])
      assert.equal(sent.includes(secret), false, secret);
    assert.equal(JSON.stringify(result).includes("secret-path"), false);
  } finally {
    f.close();
  }
});

test("economy failure raises its own alert and unresolved actions are counted", async () => {
  const f = fixture();
  try {
    const id = f.store.create("demo");
    f.services.economy.health = () => ({ available: true });
    f.services.economy.summary = async () => {
      throw new Error("down");
    };
    f.db
      .prepare(
        "INSERT INTO runtime_actions(id,run_id,work_key,state,action_json,created_at,updated_at) VALUES ('a1',?,'k1','unresolved','{}',0,0)",
      )
      .run(id);
    const result = await alertPass({
      db: f.db,
      services: f.services,
      config: f.config,
      runId: id,
      settings: { webhookUrl: null, repeatMs: 3_600_000, economy: null },
    });
    assert.ok(result.active.includes("economy_unavailable"));
    assert.ok(result.active.includes("unresolved_actions"));
    assert.equal(
      result.conditions.find((c) => c.id === "credit_exhausted")?.active,
      null,
    );
    assert.equal(result.channel, "stdout");

    // The real client returns null instead of throwing when unreachable.
    f.services.economy.summary = async () => null;
    const missing = await alertPass({
      db: f.db,
      services: f.services,
      config: f.config,
      runId: id,
      settings: { webhookUrl: null, repeatMs: 3_600_000, economy: null },
    });
    assert.ok(missing.active.includes("economy_unavailable"));
    assert.equal(result.delivered, true);
  } finally {
    f.close();
  }
});
