import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { join } from "node:path";
import { test } from "node:test";
import {
  BusinessTools,
  createBusinessToolsModule,
  definition,
} from "../src/modules/business-tools/module.ts";
import { discoverModules, registerModules } from "../src/modules/registry.ts";
import { money } from "../src/contracts/index.ts";
import { fixture } from "./helpers.ts";

function settings(directory: string): NodeJS.ProcessEnv {
  return {
    WIDEMIND_BUSINESS_TOOLS_PROFILE: "provider-test",
    WIDEMIND_BUSINESS_TOOLS_JOURNAL: join(directory, "private", "tools.sqlite"),
    WIDEMIND_ECONOMY_URL: "http://127.0.0.1:8080",
    WIDEMIND_GATEWAY_TOKEN: "fixture-gateway-" + "x".repeat(40),
    WIDEMIND_BUSINESS_ALLOWED_RECIPIENTS: '["customer@example.invalid"]',
    WIDEMIND_GMAIL_ADDRESS: "widemind@example.invalid",
    GMAIL_CLIENT_ID: "FIXTURE_CLIENT",
    GMAIL_CLIENT_SECRET: "FIXTURE_SECRET",
    GMAIL_REFRESH_TOKEN: "FIXTURE_REFRESH",
    WIDEMIND_BUSINESS_GMAIL_ACCOUNT: "fixture-mail",
    WIDEMIND_BUSINESS_OPENAI_ACCOUNT: "fixture-research",
    OPENAI_API_KEY: "FIXTURE_PRIVATE_KEY",
    OPENAI_MODEL: "fixture-model",
    OPENAI_INPUT_MICRO_USD_PER_MILLION_TOKENS: "400000",
    OPENAI_OUTPUT_MICRO_USD_PER_MILLION_TOKENS: "1600000",
    OPENAI_CACHED_INPUT_MICRO_USD_PER_MILLION_TOKENS: "0",
    WIDEMIND_RESEARCH_SOURCE_URLS:
      '["https://example.com/a","https://example.com/b"]',
  };
}

test("business tools discover normally, remain disabled by default, and construct without effects", async () => {
  const f = fixture("foundation");
  try {
    assert.ok(
      (await discoverModules(f.config)).some(
        (item) => item.id === "business-tools",
      ),
    );
    assert.deepEqual(
      createBusinessToolsModule({ db: f.db, config: f.config }, {}),
      {},
    );
    const env = settings(f.dir);
    const registered = registerModules({ db: f.db, config: f.config, env }, [
      definition,
    ]);
    assert.equal(registered.tools.health().available, true);
    assert.deepEqual(
      registered.tools.capabilities().map((row) => row.id),
      ["gmail.read", "gmail.send", "research.collect"],
    );
    assert.equal(existsSync(env.WIDEMIND_BUSINESS_TOOLS_JOURNAL!), false);
    assert.equal(
      JSON.stringify(registered.tools).includes("FIXTURE_PRIVATE_KEY"),
      false,
    );
  } finally {
    f.close();
  }
});

test("missing and malformed provider settings advertise unavailable before execution", () => {
  const f = fixture("foundation");
  try {
    const valid = settings(f.dir);
    assert.equal(
      new BusinessTools({
        WIDEMIND_BUSINESS_TOOLS_PROFILE: "provider-test",
      }).health().available,
      false,
    );
    for (const mutation of [
      { OPENAI_INPUT_MICRO_USD_PER_MILLION_TOKENS: "1e6" },
      { OPENAI_OUTPUT_MICRO_USD_PER_MILLION_TOKENS: "-1" },
      { OPENAI_CACHED_INPUT_MICRO_USD_PER_MILLION_TOKENS: "999999999" },
      { OPENAI_MAX_OUTPUT_TOKENS: "10001" },
      { WIDEMIND_RESEARCH_SOURCE_URLS: "{}" },
      { WIDEMIND_RESEARCH_ALLOWED_HOSTS: '["*.example.com"]' },
      { WIDEMIND_RESEARCH_SOURCE_URLS: '["https://example.com:444/a"]' },
      { WIDEMIND_BUSINESS_OPENAI_ACCOUNT: "" },
    ]) {
      const tools = new BusinessTools({ ...valid, ...mutation });
      assert.equal(
        tools.capabilities().find((row) => row.id === "research.collect")!
          .availability.available,
        false,
      );
    }
    for (const value of ["{}", "[]", '["not-an-email"]']) {
      assert.equal(
        new BusinessTools({
          ...valid,
          WIDEMIND_BUSINESS_ALLOWED_RECIPIENTS: value,
        }).health().available,
        false,
      );
    }
    assert.equal(existsSync(valid.WIDEMIND_BUSINESS_TOOLS_JOURNAL!), false);
  } finally {
    f.close();
  }
});

test("protected journals cannot overlap the runtime database or agent workspace", () => {
  const f = fixture("foundation");
  try {
    for (const path of [
      f.config.database,
      "relative.sqlite",
      join(f.dir, "agent", "state.sqlite"),
    ]) {
      assert.throws(() =>
        createBusinessToolsModule(
          { db: f.db, config: f.config },
          {
            ...settings(f.dir),
            WIDEMIND_BUSINESS_TOOLS_JOURNAL: path,
            WIDEMIND_WORKSPACE: join(f.dir, "agent"),
          },
        ),
      );
    }
    assert.throws(() =>
      createBusinessToolsModule(
        { db: f.db, config: { ...f.config, profile: "demo" } },
        settings(f.dir),
      ),
    );
  } finally {
    f.close();
  }
});

test("an unavailable provider or already cancelled action cannot start a subprocess", async () => {
  const f = fixture("foundation");
  try {
    const limit = {
      maximum: money("0"),
      maxDurationMs: 1000,
      maxOutputBytes: 1000,
    };
    const action = {
      version: 1 as const,
      actionId: "action",
      runId: "run",
      workKey: "work",
      tool: "gmail.read",
      input: {},
      limit,
    };
    const reservation = {
      reservationId: "reservation",
      actionId: "action",
      runId: "run",
      limit,
      authorization: "opaque",
    };
    const unavailable = new BusinessTools({
      WIDEMIND_BUSINESS_PYTHON: "/fixture-does-not-exist",
    });
    assert.equal(
      (
        await unavailable.execute(
          action,
          reservation,
          new AbortController().signal,
        )
      ).kind,
      "unknown",
    );
    const stopped = new AbortController();
    stopped.abort();
    const configured = new BusinessTools({
      ...settings(f.dir),
      WIDEMIND_BUSINESS_PYTHON: "/fixture-does-not-exist",
    });
    assert.equal(
      (await configured.execute(action, reservation, stopped.signal)).kind,
      "unknown",
    );
    assert.equal(
      existsSync(settings(f.dir).WIDEMIND_BUSINESS_TOOLS_JOURNAL!),
      false,
    );
  } finally {
    f.close();
  }
});
