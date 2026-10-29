/** Public/RPC boundary regressions; all content and receipts are synthetic. */
import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import type {
  Json,
  PublicWebsiteData,
  ToolReceipt,
} from "../src/contracts/index.ts";
import {
  EconomyClient,
  validateSummary,
} from "../src/modules/economy/client.ts";
import { createEconomyModule } from "../src/modules/economy/module.ts";
import { publicProjection } from "../src/web/projections.ts";
import { webServer } from "../src/web/server.ts";
import { fixture } from "./helpers.ts";

const gateway = "fixture-gateway-" + "g".repeat(48);
const operator = "fixture-operator-" + "o".repeat(48);
const summary = () => ({
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
  asOf: "2026-10-29T12:00:00Z",
});

async function projectContent(content: unknown) {
  const f = fixture("foundation");
  try {
    f.services.website.publicContent = async () =>
      content as Pick<
        PublicWebsiteData,
        "offerings" | "activity" | "availability"
      >;
    return await publicProjection(f.store, f.services);
  } finally {
    f.close();
  }
}

test("actual public HTTP availability drops private metadata from a valid structural value", async () => {
  const f = fixture("foundation");
  const availability = {
    available: false as const,
    reason: "Provider setup required",
    diagnostics: {
      apiKey: "fixture-private-availability-canary",
      accountRef: "fixture-owner-account",
    },
  };
  f.services.website.publicContent = async () => ({
    offerings: [],
    activity: [],
    availability,
  });
  const server = webServer({ ...f, secrets: { operatorPasswordHash: null } });
  try {
    await new Promise<void>((resolve) =>
      server.listen(0, "127.0.0.1", resolve),
    );
    const address = server.address();
    assert(address && typeof address !== "string");
    const response = await fetch(`http://127.0.0.1:${address.port}/api/public`);
    assert.equal(response.status, 200);
    const text = await response.text();
    assert.doesNotMatch(
      text,
      /fixture-private-availability-canary|fixture-owner-account|diagnostics|apiKey|accountRef/,
    );
    assert.deepEqual(JSON.parse(text).availability, {
      available: false,
      reason: "Provider setup required",
    });
  } finally {
    server.closeAllConnections();
    await new Promise<void>((resolve) => server.close(() => resolve()));
    f.close();
  }
});

test("public offering and activity projection keeps only validated string leaves", async () => {
  const validOffer = {
    id: "offering",
    title: "Research",
    description: "A public research service",
    privateAccount: "private-extra-canary",
  };
  const validActivity = {
    id: "activity",
    at: "2026-10-29T12:00:00Z",
    text: "Public fixture activity",
    privateReceipt: "private-extra-canary",
  };
  const result = await projectContent({
    offerings: [
      validOffer,
      { ...validOffer, title: { apiKey: "private-title-canary" } },
      { ...validOffer, description: ["private-description-canary"] },
      null,
    ],
    activity: [
      validActivity,
      { ...validActivity, text: { customer: "private-text-canary" } },
      { ...validActivity, at: { privateDate: "private-date-canary" } },
      null,
    ],
    availability: { available: true, debug: "private-extra-canary" },
  });
  assert.deepEqual(result.offerings, [
    {
      id: "offering",
      title: "Research",
      description: "A public research service",
    },
  ]);
  assert.deepEqual(result.activity, [
    {
      id: "activity",
      at: "2026-10-29T12:00:00Z",
      text: "Public fixture activity",
    },
  ]);
  assert.deepEqual(result.availability, { available: true });
  assert.doesNotMatch(
    JSON.stringify(result),
    /private-.*canary|apiKey|privateReceipt|privateAccount|debug/,
  );
});

for (const availability of [
  null,
  [],
  { available: "true" },
  { available: 1 },
  { available: false, reason: { secret: "private-canary" } },
  { available: false, reason: "x".repeat(2001) },
  { available: false, reason: "bad\u0000reason" },
]) {
  test(`malformed public availability is unavailable without reflecting input: ${JSON.stringify(availability).slice(0, 60)}`, async () => {
    const result = await projectContent({
      offerings: [],
      activity: [],
      availability,
    });
    assert.deepEqual(result.availability, {
      available: false,
      reason: "Website availability unavailable",
    });
  });
}

test("malformed content collections and oversized leaves never become public objects", async () => {
  const malformed = await projectContent({
    offerings: { secret: "private-canary" },
    activity: "private-canary",
    availability: { available: true },
  });
  assert.deepEqual(malformed.offerings, []);
  assert.deepEqual(malformed.activity, []);
  const oversized = await projectContent({
    offerings: [{ id: "offer", title: "x".repeat(201), description: "Public" }],
    activity: [{ id: "activity", at: "not-a-date", text: "Public" }],
    availability: { available: true },
  });
  assert.deepEqual(oversized.offerings, []);
  assert.deepEqual(oversized.activity, []);
});

for (const mode of [["sandbox"], ["live"], {}, true, null, 0]) {
  test(`economy summary rejects nonprimitive mode ${JSON.stringify(mode)}`, () => {
    assert.throws(() => validateSummary({ ...summary(), mode }));
  });
}

test("operator opening rejects mode arrays before any backend request or funding effect", async () => {
  let requests = 0;
  const backend = createServer((_request, response) => {
    requests++;
    response.setHeader("Content-Type", "application/json");
    response.end(JSON.stringify(summary()));
  });
  await new Promise<void>((resolve) => backend.listen(0, "127.0.0.1", resolve));
  const address = backend.address();
  assert(address && typeof address !== "string");
  const f = fixture("foundation");
  try {
    const module = createEconomyModule(
      { db: f.db, config: f.config },
      {
        WIDEMIND_ECONOMY_URL: `http://127.0.0.1:${address.port}`,
        WIDEMIND_GATEWAY_TOKEN: gateway,
        WIDEMIND_OPERATOR_TOKEN: operator,
      },
    );
    const route = module.routes![0]!;
    const runId = f.store.create("foundation");
    const modes: Json[] = [["sandbox"], ["live"], {}, true, null, 0];
    for (const mode of modes) {
      const response = await route.handle({
        operator: true,
        body: { runId, mode },
        query: {},
        headers: {},
      });
      assert.equal(response.status, 422);
    }
    assert.equal(requests, 0);
  } finally {
    backend.closeAllConnections();
    await new Promise<void>((resolve) => backend.close(() => resolve()));
    f.close();
  }
});

test("malformed receipt status is not coerced before accounting transport", async () => {
  let calls = 0;
  const transport = (async () => {
    calls++;
    return new Response('{"kind":"settled"}');
  }) as typeof fetch;
  const client = new EconomyClient(
    {
      url: "http://127.0.0.1:8080",
      gatewayToken: gateway,
      operatorToken: operator,
      timeoutMs: 100,
    },
    transport,
  );
  const reservation = {
    reservationId: "reservation",
    actionId: "action",
    runId: "run",
    authorization: "fixture-permit",
    limit: {
      maximum: { currency: "USD" as const, micros: "100" },
      maxDurationMs: 100,
      maxOutputBytes: 100,
    },
  };
  for (const status of [["succeeded"], ["failed"], {}, true]) {
    const receipt = {
      version: 1,
      actionId: "action",
      status,
      result: {},
      usage: {
        actualCharge: { currency: "USD", micros: "10" },
        provider: "fixture",
        accountRef: "fixture-account",
        externalEventId: "fixture-event",
        billableFailure: false,
        provisional: false,
        evidenceRef: "fixture-evidence",
      },
    } as unknown as ToolReceipt;
    assert.equal(
      (await client.settle(reservation, receipt)).kind,
      "unavailable",
    );
  }
  assert.equal(calls, 0);
});

test("public text preserves ordinary line breaks while projecting only declared strings", async () => {
  const result = await projectContent({
    offerings: [
      {
        id: "offering",
        title: "Research",
        description: "A public description\nwith two lines",
      },
    ],
    activity: [
      { id: "activity", at: "2026-10-29T12:00:00Z", text: "Public\nactivity" },
    ],
    availability: {
      available: false,
      reason: "Setup required\nContact the operator",
    },
  });
  assert.equal(
    result.offerings[0]?.description,
    "A public description\nwith two lines",
  );
  assert.equal(result.activity[0]?.text, "Public\nactivity");
  assert.deepEqual(result.availability, {
    available: false,
    reason: "Setup required\nContact the operator",
  });
});

test("resolved malformed content cannot take the public page or API offline", async () => {
  const f = fixture("foundation");
  let content: unknown;
  f.services.website.publicContent = async () =>
    content as Pick<
      PublicWebsiteData,
      "offerings" | "activity" | "availability"
    >;
  const server = webServer({ ...f, secrets: { operatorPasswordHash: null } });
  try {
    await new Promise<void>((resolve) =>
      server.listen(0, "127.0.0.1", resolve),
    );
    const address = server.address();
    assert(address && typeof address !== "string");
    const origin = `http://127.0.0.1:${address.port}`;
    for (const malformed of [
      null,
      undefined,
      false,
      0,
      "private-content-canary",
      [],
      ["private-content-canary"],
    ]) {
      content = malformed;
      const response = await fetch(origin + "/api/public");
      assert.equal(response.status, 200);
      const projected = (await response.json()) as PublicWebsiteData;
      assert.deepEqual(projected.offerings, []);
      assert.deepEqual(projected.activity, []);
      assert.deepEqual(projected.availability, {
        available: false,
        reason: "Website availability unavailable",
      });
      assert.doesNotMatch(JSON.stringify(projected), /private-content-canary/);
      const page = await fetch(origin + "/");
      assert.equal(page.status, 200);
      const html = await page.text();
      assert.match(html, /Headquarters|Operating funds/);
      assert.doesNotMatch(html, /private-content-canary/);
    }
  } finally {
    server.closeAllConnections();
    await new Promise<void>((resolve) => server.close(() => resolve()));
    f.close();
  }
});
