/** Real loopback RPC fixtures; these tests make no Stripe or other paid calls. */
import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import type { ServerResponse } from "node:http";
import type { TestContext } from "node:test";
import {
  EconomyClient,
  bridgeSettings,
  validateSummary,
} from "../src/modules/economy/client.ts";
import { createEconomyModule } from "../src/modules/economy/module.ts";
import { fixture } from "./helpers.ts";
import type {
  BudgetLimit,
  FinancialSummary,
  Json,
  Reservation,
  ToolReceipt,
} from "../src/contracts/index.ts";

const gateway = "fixture-gateway-" + "g".repeat(48),
  operator = "fixture-operator-" + "o".repeat(48);
const limit: BudgetLimit = {
  maximum: { currency: "USD", micros: "9007199254740993" },
  maxTokens: 100,
  maxDurationMs: 1000,
  maxOutputBytes: 4096,
};
const request = {
  runId: "run",
  actionId: "action",
  tool: "fixture.tool",
  limit,
};
function reservation(): Reservation {
  return {
    runId: request.runId,
    actionId: request.actionId,
    reservationId: "reservation",
    limit: structuredClone(limit),
    authorization: "opaque-private-permit",
  };
}
function summary(overrides: Record<string, unknown> = {}): FinancialSummary {
  return {
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
    asOf: "2026-10-29T12:00:00+00:00",
    ...overrides,
  } as FinancialSummary;
}
function receipt(): ToolReceipt {
  return {
    version: 1,
    actionId: "action",
    status: "failed",
    result: { privateCustomer: "private-canary" },
    usage: {
      actualCharge: { currency: "USD", micros: "6000000" },
      provider: "fixture/provider",
      accountRef: "account/ref",
      externalEventId: "charge/ref",
      billableFailure: true,
      provisional: false,
      evidenceRef: "provider:measured-charge",
      nativeAmount: {
        currency: "USD",
        minorUnits: "600",
        conversionRef: "usd-native",
        rounding: "exact-cent",
      },
    },
  };
}
async function rpc(
  t: TestContext,
  handler: (
    path: string,
    body: Record<string, unknown>,
    authorization: string | undefined,
    response: ServerResponse,
  ) => void,
  timeoutMs = 500,
) {
  const calls: Array<{
    path: string;
    body: Record<string, unknown>;
    authorization: string | undefined;
  }> = [];
  const server = createServer(async (req, res) => {
    const parts: Buffer[] = [];
    for await (const chunk of req) parts.push(Buffer.from(chunk));
    const body = JSON.parse(Buffer.concat(parts).toString()) as Record<
      string,
      unknown
    >;
    calls.push({
      path: req.url!,
      body,
      authorization: req.headers.authorization,
    });
    handler(req.url!, body, req.headers.authorization, res);
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  assert(address && typeof address !== "string");
  const url = `http://127.0.0.1:${address.port}`;
  t.after(async () => {
    server.closeAllConnections();
    await new Promise<void>((resolve) => server.close(() => resolve()));
  });
  return {
    client: new EconomyClient({
      url,
      gatewayToken: gateway,
      operatorToken: operator,
      timeoutMs,
    }),
    calls,
    url,
  };
}
function json(response: ServerResponse, value: unknown) {
  response.setHeader("Content-Type", "application/json");
  response.end(JSON.stringify(value));
}

test("bridge is explicitly disabled without its URL and fails closed for partial configuration", () => {
  assert.equal(bridgeSettings({}), null);
  assert.equal(bridgeSettings({ WIDEMIND_GATEWAY_TOKEN: gateway }), null);
  for (const env of [
    { WIDEMIND_ECONOMY_URL: "http://127.0.0.1" },
    {
      WIDEMIND_ECONOMY_URL: "http://127.0.0.1",
      WIDEMIND_GATEWAY_TOKEN: gateway,
    },
    {
      WIDEMIND_ECONOMY_URL: "http://127.0.0.1",
      WIDEMIND_GATEWAY_TOKEN: gateway,
      WIDEMIND_OPERATOR_TOKEN: gateway,
    },
  ])
    assert.throws(() => bridgeSettings(env), /configuration/);
});
for (const url of [
  "http://example.com",
  "https://user:private-key@example.com",
  "https://example.com/path",
  "https://example.com/?secret=value",
  "https://example.com/#fragment",
  "https://example.com:\\invalid",
  "https://example.com\n/",
]) {
  test(`reject unsafe configured URL ${JSON.stringify(url)}`, () => {
    assert.throws(
      () =>
        bridgeSettings({
          WIDEMIND_ECONOMY_URL: url,
          WIDEMIND_GATEWAY_TOKEN: gateway,
          WIDEMIND_OPERATOR_TOKEN: operator,
        }),
      /configuration/,
    );
  });
}

test("summary preserves aggregate amounts above safe Number and signed-64-bit without leaking fields", async (t) => {
  const huge = "1234567890123456789012345678901234567890";
  const { client, calls } = await rpc(t, (_path, _body, auth, res) => {
    assert.equal(auth, `Bearer ${gateway}`);
    json(
      res,
      summary({
        postedMicros: huge,
        spendableMicros: huge,
        ownerFundingMicros: (BigInt(huge) - 5000000n).toString(),
        privateCustomer: "private-canary",
        authorization: "private-token",
      }),
    );
  });
  const result = await client.summary("run");
  assert.equal(result?.postedMicros, huge);
  assert.equal(result?.spendableMicros, huge);
  assert(!JSON.stringify(result).includes("private-canary"));
  assert(!JSON.stringify(result).includes("private-token"));
  assert.deepEqual(calls[0]?.body, { runId: "run" });
  assert.equal(calls[0]?.path, "/v1/runtime/summary");
});
for (const invalid of [
  5000000,
  9007199254740992,
  "01",
  "+1",
  "-0",
  "1e6",
  "1.5",
  "NaN",
  "9".repeat(129),
]) {
  test(`summary rejects unsafe or noncanonical monetary output ${String(invalid).slice(0, 25)}`, () => {
    assert.throws(() =>
      validateSummary(
        summary({ postedMicros: invalid, spendableMicros: invalid }),
      ),
    );
  });
}

test("summary accepts signed metrics and expenses while reconciling every explicit cash category", () => {
  const value = summary({
    postedMicros: "5000046",
    spendableMicros: "5000033",
    reservedMicros: "10",
    holdsMicros: "3",
    customerReceiptsMicros: "100",
    earnedRevenueMicros: "-3",
    operatingChargesMicros: "20",
    ownerFundingMicros: "4",
    subsidiesMicros: "-7",
    processorFeesMicros: "-2",
    refundsMicros: "5",
    chargebacksMicros: "7",
    chargebackReversalsMicros: "11",
    paymentAdjustmentsMicros: "-13",
    correctionsMicros: "-32",
    testFundingMicros: "6",
    otherCashMicros: "0",
  });
  assert.equal(validateSummary(value)?.postedMicros, "5000046");
  assert.throws(() => validateSummary({ ...value, correctionsMicros: "0" }));
  assert.throws(() =>
    validateSummary({ ...value, spendableMicros: "5000046" }),
  );
});

test("authorization preserves exact decimal cap and binds returned IDs and all limits", async (t) => {
  const { client, calls } = await rpc(t, (_path, body, auth, res) => {
    assert.equal(auth, `Bearer ${gateway}`);
    assert.deepEqual(body, request);
    json(res, {
      kind: "authorized",
      reservation: { ...reservation(), privateField: "private-canary" },
    });
  });
  const authorized = await client.authorize(request);
  assert.equal(authorized.kind, "authorized");
  if (authorized.kind === "authorized") {
    assert.equal(
      authorized.reservation.limit.maximum.micros,
      "9007199254740993",
    );
    assert(!JSON.stringify(authorized).includes("private-canary"));
  }
  assert.equal(calls[0]?.path, "/v1/runtime/authorize");
});
for (const field of [
  "runId",
  "actionId",
  "maximum",
  "maxTokens",
  "maxDurationMs",
  "maxOutputBytes",
]) {
  test(`authorization rejects a mismatched ${field}`, async (t) => {
    const changed = reservation();
    if (field === "runId" || field === "actionId") changed[field] = "other";
    else if (field === "maximum") changed.limit.maximum.micros = "1";
    else
      changed.limit[field as "maxTokens" | "maxDurationMs" | "maxOutputBytes"] =
        2;
    const { client } = await rpc(t, (_p, _b, _a, res) =>
      json(res, { kind: "authorized", reservation: changed }),
    );
    assert.equal((await client.authorize(request)).kind, "unavailable");
  });
}

test("zero cap remains exactly zero and invalid caps never contact the control plane", async (t) => {
  const zero = {
    ...request,
    limit: { ...limit, maximum: { currency: "USD" as const, micros: "0" } },
  };
  const { client, calls } = await rpc(t, (_p, _body, _a, res) =>
    json(res, {
      kind: "authorized",
      reservation: { ...reservation(), limit: zero.limit },
    }),
  );
  assert.equal((await client.authorize(zero)).kind, "authorized");
  for (const micros of ["-1", "9223372036854775808", "01"]) {
    assert.equal(
      (
        await client.authorize({
          ...request,
          limit: { ...limit, maximum: { currency: "USD", micros } },
        })
      ).kind,
      "unavailable",
    );
  }
  assert.equal(calls.length, 1);
});

test("provider error text cannot leak through an authorization denial", async (t) => {
  const { client } = await rpc(t, (_p, _body, _a, res) =>
    json(res, { kind: "insufficient", reason: "private-customer-secret" }),
  );
  const result = await client.authorize(request);
  assert.equal(result.kind, "insufficient");
  assert(!JSON.stringify(result).includes("private-customer-secret"));
});

test("settlement carries known charges and native evidence, omitting only runtime-stored private output", async (t) => {
  const { client, calls } = await rpc(t, (path, body, auth, res) => {
    assert.equal(path, "/v1/runtime/settle");
    assert.equal(auth, `Bearer ${gateway}`);
    json(res, { kind: "settled" });
  });
  const known = receipt();
  known.result = { huge: "private-canary".repeat(100000) };
  assert.equal((await client.settle(reservation(), known)).kind, "settled");
  const submitted = calls[0]?.body.receipt as ToolReceipt;
  assert.equal(submitted.usage.actualCharge.micros, "6000000");
  assert.deepEqual(submitted.usage.nativeAmount, receipt().usage.nativeAmount);
  assert.deepEqual(submitted.result, {
    omitted: true,
    reason: "Result retained in runtime action store",
  });
  assert(!JSON.stringify(calls).includes("private-canary"));
  const cyclic: Record<string, unknown> = {};
  cyclic.self = cyclic;
  known.result = cyclic as Json;
  assert.equal((await client.settle(reservation(), known)).kind, "settled");
});

test("provisional usage is submitted but never reported as completed settlement", async (t) => {
  const { client, calls } = await rpc(t, (_p, _b, _a, res) =>
    json(res, { kind: "settled" }),
  );
  const partial = receipt();
  partial.usage.provisional = true;
  assert.equal(
    (await client.settle(reservation(), partial)).kind,
    "unavailable",
  );
  assert.equal(calls.length, 1);
});

test("mismatched receipt identity never calls settlement", async (t) => {
  const { client, calls } = await rpc(t, (_p, _b, _a, res) =>
    json(res, { kind: "settled" }),
  );
  const changed = receipt();
  changed.actionId = "other";
  assert.equal(
    (await client.settle(reservation(), changed)).kind,
    "unavailable",
  );
  assert.equal(calls.length, 0);
});

test("response byte limits and redirects fail closed", async (t) => {
  let which = 0;
  const { client } = await rpc(t, (_p, _b, _a, res) => {
    if (which++ === 0) {
      res.writeHead(302, { Location: "https://untrusted.invalid/private" });
      res.end();
    } else res.end('"' + "x".repeat(65536) + '"');
  });
  assert.equal(await client.summary("run"), null);
  assert.equal(await client.summary("run"), null);
});

test("a stalled native HTTP body respects the control-plane deadline", async (t) => {
  const { client } = await rpc(
    t,
    (_p, _b, _a, res) => {
      res.writeHead(200, { "Content-Type": "application/json" });
      res.write('{"pending":');
    },
    100,
  );
  const start = Date.now();
  assert.equal(await client.summary("run"), null);
  assert(Date.now() - start < 1000);
});

test("even a transport that ignores AbortSignal cannot pin a control call", async () => {
  const client = new EconomyClient(
    {
      url: "http://127.0.0.1",
      gatewayToken: gateway,
      operatorToken: operator,
      timeoutMs: 100,
    },
    (() => new Promise(() => {})) as typeof fetch,
  );
  const start = Date.now();
  assert.equal(await client.summary("run"), null);
  assert(Date.now() - start < 1000);
});

test("normal module registration is optional and opening requires an existing foundation run and operator", async (t) => {
  const f = fixture("foundation");
  t.after(() => f.close());
  assert.deepEqual(createEconomyModule({ db: f.db, config: f.config }, {}), {});
  const { url, calls } = await rpc(t, (path, body, auth, res) => {
    assert.equal(path, "/v1/runtime/seed");
    assert.equal(auth, `Bearer ${operator}`);
    assert.equal(body.mode, "sandbox");
    json(res, summary());
  });
  const env = {
    WIDEMIND_ECONOMY_URL: url,
    WIDEMIND_GATEWAY_TOKEN: gateway,
    WIDEMIND_OPERATOR_TOKEN: operator,
  };
  const registration = createEconomyModule({ db: f.db, config: f.config }, env);
  assert.equal(registration.economy?.health().available, true);
  const route = registration.routes?.[0];
  assert(route);
  assert.equal(route.path, "/api/modules/economy/open");
  assert.equal(route.access, "operator");
  const input = {
    query: {},
    headers: {},
    operator: true,
    body: { runId: "unknown", mode: "sandbox" },
  };
  assert.equal((await route.handle({ ...input, operator: false })).status, 403);
  assert.equal((await route.handle(input)).status, 404);
  const runId = f.store.create("foundation");
  assert.equal(
    (await route.handle({ ...input, body: { runId, mode: "sandbox" } })).status,
    200,
  );
  assert.equal(calls.length, 1);
  assert.throws(
    () =>
      createEconomyModule(
        { db: f.db, config: { ...f.config, profile: "demo" } },
        env,
      ),
    /foundation/,
  );
});

test("invalid optional usage diagnostics cannot suppress a known bill", async (t) => {
  const { client, calls } = await rpc(t, (_p, _b, _a, res) =>
    json(res, { kind: "settled" }),
  );
  const known = receipt();
  known.usage.evidenceRef = { invalid: "object" } as unknown as string;
  known.usage.nativeAmount = {
    currency: "USD",
    minorUnits: 6 as unknown as string,
    conversionRef: "provider-native",
    rounding: "exact",
  };
  known.externalRef = "x".repeat(3000);
  assert.equal((await client.settle(reservation(), known)).kind, "settled");
  const submitted = calls[0]?.body.receipt as ToolReceipt;
  assert.equal(submitted.usage.actualCharge.micros, "6000000");
  assert.equal(submitted.usage.nativeAmount, undefined);
  assert.equal(
    (submitted.result as Record<string, unknown>).usageMetadataOmitted,
    true,
  );
});

test("bounded accounting envelope retains a known overage when optional Unicode evidence is huge", async (t) => {
  const { client, calls } = await rpc(t, (_p, _b, _a, res) =>
    json(res, { kind: "settled" }),
  );
  const known = receipt();
  known.usage.evidenceRef = "漢".repeat(2000);
  known.usage.nativeAmount = {
    currency: "USD",
    minorUnits: "600",
    conversionRef: "漢".repeat(2000),
    rounding: "漢".repeat(2000),
  };
  known.externalRef = "漢".repeat(2000);
  assert.equal((await client.settle(reservation(), known)).kind, "settled");
  const submitted = calls[0]?.body.receipt as ToolReceipt;
  assert.equal(submitted.usage.actualCharge.micros, "6000000");
  assert.equal(
    (submitted.result as Record<string, unknown>).usageMetadataOmitted,
    true,
  );
  assert(Buffer.byteLength(JSON.stringify(calls[0]?.body)) <= 16384);
});

test("definitive not-performed renewal is explicit and fails closed if the backend does not renew", async (t) => {
  let permitted = true;
  const { client, calls } = await rpc(t, (path, body, auth, res) => {
    assert.equal(path, "/v1/runtime/not-performed");
    assert.equal(auth, `Bearer ${gateway}`);
    assert.deepEqual(body, {
      reservation: reservation(),
      evidenceRef: "provider:definitive-no-effect",
    });
    json(
      res,
      permitted
        ? { kind: "renewed" }
        : { kind: "unavailable", reason: "private-provider-canary" },
    );
  });
  assert.deepEqual(
    await client.confirmNotPerformed(
      reservation(),
      "provider:definitive-no-effect",
    ),
    { kind: "renewed" },
  );
  permitted = false;
  const result = await client.confirmNotPerformed(
    reservation(),
    "provider:definitive-no-effect",
  );
  assert.equal(result.kind, "unavailable");
  assert(!JSON.stringify(result).includes("private-provider-canary"));
  assert.equal(calls.length, 2);
});

test("closed no-effect confirmation and backend evidence boundaries retain a known charge", async (t) => {
  const { client, calls } = await rpc(t, (path, _body, _auth, res) =>
    json(res, { kind: path.endsWith("not-performed") ? "closed" : "settled" }),
  );
  assert.deepEqual(
    await client.confirmNotPerformed(
      reservation(),
      "provider:third-definitive-no-effect",
    ),
    { kind: "closed" },
  );
  const exact = receipt();
  exact.usage.externalEventId = "e".repeat(2000);
  exact.usage.evidenceRef = "r".repeat(2000);
  exact.usage.nativeAmount = {
    currency: "x".repeat(20),
    minorUnits: "-" + "9".repeat(127),
    conversionRef: "c".repeat(2000),
    rounding: "r".repeat(2000),
  };
  assert.equal((await client.settle(reservation(), exact)).kind, "settled");
  const valid = calls[1]?.body.receipt as ToolReceipt;
  assert.equal(valid.usage.externalEventId.length, 2000);
  assert.deepEqual(valid.usage.nativeAmount, exact.usage.nativeAmount);
  const invalid = receipt();
  invalid.usage.evidenceRef = "r".repeat(2001);
  invalid.usage.nativeAmount = {
    currency: "x".repeat(21),
    minorUnits: "-" + "9".repeat(128),
    conversionRef: "c".repeat(2001),
    rounding: "r".repeat(2001),
  };
  assert.equal((await client.settle(reservation(), invalid)).kind, "settled");
  const omitted = calls[2]?.body.receipt as ToolReceipt;
  assert.equal(omitted.usage.actualCharge.micros, "6000000");
  assert.equal(omitted.usage.nativeAmount, undefined);
  assert.equal(
    (omitted.result as Record<string, unknown>).usageMetadataOmitted,
    true,
  );
});
