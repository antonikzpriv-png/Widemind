import test from "node:test";
import assert from "node:assert/strict";
import { createEconomyModule } from "../src/modules/economy/module.ts";
import { fixture } from "./helpers.ts";
const central = {
  WIDEMIND_ECONOMY_URL: "http://127.0.0.1:8080",
  WIDEMIND_OPERATOR_TOKEN: "o".repeat(48),
  WIDEMIND_GATEWAY_TOKEN: "g".repeat(48),
};
const service = {
  WIDEMIND_ECONOMY_URL: "http://127.0.0.1:8080",
  WIDEMIND_ECONOMY_OPERATOR_TOKEN: "o".repeat(48),
  WIDEMIND_ECONOMY_GATEWAY_TOKEN: "g".repeat(48),
  WIDEMIND_ECONOMY_AGENT_TOKEN: "a".repeat(48),
};
test("accounting protocols never mix credentials or silently fall back", () => {
  const f = fixture("foundation");
  try {
    const context = { db: f.db, config: f.config };
    assert.throws(
      () => createEconomyModule(context, { ...central, ...service }),
      /one economy authority/,
    );
    assert.throws(
      () =>
        createEconomyModule(context, {
          ...central,
          WIDEMIND_TOOLS_URL: "http://127.0.0.1:8081",
        }),
      /matching service economy/,
    );
    assert.equal(
      createEconomyModule(context, central).economy?.health().available,
      true,
    );
    assert.equal(
      createEconomyModule(context, service).economy?.health().available,
      true,
    );
    assert.deepEqual(createEconomyModule({ ...context, env: {} }), {});
    assert.equal(f.store.runs().length, 0);
  } finally {
    f.close();
  }
});
