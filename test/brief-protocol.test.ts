import assert from "node:assert/strict";
import test from "node:test";
import { assertCentralBusinessProtocol } from "../src/modules/brief-business/protocol.ts";
import { createBusinessModule } from "../src/modules/brief-business/module.ts";
import { createBusinessToolsModule } from "../src/modules/business-tools/module.ts";
import { fixture } from "./helpers.ts";

const central: NodeJS.ProcessEnv = {
  WIDEMIND_ECONOMY_URL: "http://127.0.0.1:8080",
  WIDEMIND_OPERATOR_TOKEN: "fixture-operator-" + "o".repeat(48),
  WIDEMIND_GATEWAY_TOKEN: "fixture-gateway-" + "g".repeat(48),
  WIDEMIND_BUSINESS_ENABLED: "true",
  WIDEMIND_BUSINESS_TOOLS_PROFILE: "provider-test",
  WIDEMIND_STRIPE_MODE: "test",
  WIDEMIND_PAYMENT_PROVIDER: "stripe",
  WIDEMIND_COMMERCE_MODE: "sandbox",
  WIDEMIND_COMMERCE_ACCOUNT: "fixture-commerce",
};

test("central business accepts its exact family, unavailable configuration, and retired profiles without effects", () => {
  for (const env of [
    {},
    central,
    { ...central, WIDEMIND_RUNTIME_TOOL_PROFILES: "{}" },
    {
      ...central,
      WIDEMIND_ECONOMY_OPERATOR_TOKEN: "",
      WIDEMIND_BUSINESS_URL: " ",
      WIDEMIND_RUNTIME_TOOL_PROFILES: JSON.stringify({
        "commerce.checkout": {
          provider: "commerce",
          account: "fixture-commerce",
          mode: "test",
        },
      }),
    },
  ])
    assert.doesNotThrow(() => assertCentralBusinessProtocol(env));
  assert.doesNotThrow(() =>
    assertCentralBusinessProtocol({
      WIDEMIND_BUSINESS_TOOLS_PROFILE: "live",
      WIDEMIND_COMMERCE_MODE: "live",
      WIDEMIND_STRIPE_MODE: "live",
      WIDEMIND_BUSINESS_MODE: "live",
      WIDEMIND_PAYMENT_PROVIDER: "stripe",
    }),
  );
});

test("central business rejects every service credential/endpoint and a conflicting payment source", () => {
  const foreign = [
    "WIDEMIND_ECONOMY_OPERATOR_TOKEN",
    "WIDEMIND_ECONOMY_GATEWAY_TOKEN",
    "WIDEMIND_ECONOMY_AGENT_TOKEN",
    "WIDEMIND_BUSINESS_URL",
    "WIDEMIND_BUSINESS_TOKEN",
    "WIDEMIND_TOOLS_URL",
    "WIDEMIND_TOOLS_TOKEN",
  ];
  const f = fixture("foundation");
  try {
    for (const key of foreign) {
      const env = {
        ...central,
        [key]: key.endsWith("URL")
          ? "http://127.0.0.1:8082"
          : "private-service-token-must-not-appear-in-errors",
      };
      for (const invoke of [
        () => assertCentralBusinessProtocol(env),
        () => createBusinessModule({ db: f.db, config: f.config, env }),
        () => createBusinessToolsModule({ db: f.db, config: f.config }, env),
      ])
        assert.throws(
          invoke,
          (error) =>
            error instanceof Error &&
            error.message.includes("protocol conflict") &&
            !error.message.includes("private-service-token"),
        );
    }
    assert.throws(
      () =>
        assertCentralBusinessProtocol({
          ...central,
          WIDEMIND_PAYMENT_PROVIDER: "paypal",
        }),
      /Stripe payment provider/,
    );
    assert.equal(f.store.runs().length, 0);
    assert.equal(
      f.db
        .prepare("SELECT name FROM sqlite_master WHERE name LIKE 'business_%'")
        .all().length,
      0,
    );
  } finally {
    f.close();
  }
});

test("central business refuses test/live mode and provider/account profile swaps", () => {
  for (const change of [
    { WIDEMIND_STRIPE_MODE: "live" },
    { WIDEMIND_COMMERCE_MODE: "live" },
    { WIDEMIND_BUSINESS_TOOLS_PROFILE: "live" },
    { WIDEMIND_BUSINESS_MODE: "live" },
    { WIDEMIND_COMMERCE_MODE: "test" },
    { WIDEMIND_STRIPE_MODE: "sandbox" },
    { WIDEMIND_RUNTIME_TOOL_PROFILES: "not-json" },
    { WIDEMIND_RUNTIME_TOOL_PROFILES: "[]" },
    ...[
      { provider: "commerce", account: "fixture-commerce", mode: "live" },
      { provider: "paypal", account: "fixture-commerce", mode: "sandbox" },
      { provider: "commerce", account: "another-account", mode: "sandbox" },
      {
        provider: "commerce",
        account: "fixture-commerce",
        mode: "provider-test",
      },
    ].map((profile) => ({
      WIDEMIND_RUNTIME_TOOL_PROFILES: JSON.stringify({
        "commerce.checkout": profile,
      }),
    })),
  ])
    assert.throws(
      () => assertCentralBusinessProtocol({ ...central, ...change }),
      /protocol conflict/,
    );
});

test("mode aliases reject Object prototype properties even without another declared mode", () => {
  for (const value of ["toString", "__proto__", "constructor"]) {
    for (const setting of [
      "WIDEMIND_STRIPE_MODE",
      "WIDEMIND_COMMERCE_MODE",
      "WIDEMIND_BUSINESS_MODE",
      "WIDEMIND_BUSINESS_TOOLS_PROFILE",
    ])
      assert.throws(
        () => assertCentralBusinessProtocol({ [setting]: value }),
        /protocol conflict/,
      );
    assert.throws(
      () =>
        assertCentralBusinessProtocol({
          WIDEMIND_RUNTIME_TOOL_PROFILES: JSON.stringify({
            "commerce.checkout": {
              provider: "commerce",
              account: "fixture-commerce",
              mode: value,
            },
          }),
        }),
      /protocol conflict/,
    );
  }
});

test("disabled central factories preserve upstream service operation without checking credentials or touching data", () => {
  const f = fixture("foundation");
  try {
    const env = {
      WIDEMIND_ECONOMY_OPERATOR_TOKEN: "service-operator-" + "o".repeat(48),
      WIDEMIND_ECONOMY_GATEWAY_TOKEN: "service-gateway-" + "g".repeat(48),
      WIDEMIND_ECONOMY_AGENT_TOKEN: "service-agent-" + "a".repeat(48),
      WIDEMIND_PAYMENT_PROVIDER: "paypal",
      WIDEMIND_BUSINESS_URL: "http://127.0.0.1:8082",
      WIDEMIND_TOOLS_URL: "http://127.0.0.1:8081",
    };
    assert.deepEqual(
      createBusinessModule({ db: f.db, config: f.config, env }),
      {},
    );
    assert.deepEqual(
      createBusinessToolsModule({ db: f.db, config: f.config }, env),
      {},
    );
    assert.equal(f.store.runs().length, 0);
    assert.equal(
      f.db
        .prepare("SELECT name FROM sqlite_master WHERE name LIKE 'business_%'")
        .all().length,
      0,
    );
  } finally {
    f.close();
  }
});
