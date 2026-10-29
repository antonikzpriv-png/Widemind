import test from "node:test";
import assert from "node:assert/strict";
import { loadConfig } from "../src/config.ts";
import { money } from "../src/contracts/index.ts";
import { migrate } from "../src/db/migrate.ts";
import { registerModules } from "../src/modules/registry.ts";
import { fixture } from "./helpers.ts";
test("integer money boundaries reject fractional, signed and overflowing amounts", () => {
  assert.deepEqual(money("5000000"), { currency: "USD", micros: "5000000" });
  assert.equal(money(0n).micros, "0");
  for (const value of ["1.2", "-1", "01", "1e6", "9223372036854775808", "NaN"])
    assert.throws(() => money(value));
});
test("core and module migrations apply once and changed applied SQL fails closed", () => {
  const f = fixture("foundation");
  try {
    const migration = {
      id: "example",
      contractVersion: 1 as const,
      migrations: [
        { version: 1, sql: "CREATE TABLE example_items(id TEXT PRIMARY KEY);" },
      ],
    };
    migrate(f.db, [migration]);
    migrate(f.db, [migration]);
    assert.equal(
      f.db
        .prepare(
          "SELECT COUNT(*) AS n FROM schema_migrations WHERE module='example'",
        )
        .get()?.n,
      1,
    );
    assert.throws(
      () =>
        migrate(f.db, [
          {
            ...migration,
            migrations: [
              { version: 1, sql: "CREATE TABLE example_other(id TEXT);" },
            ],
          },
        ]),
      /changed/,
    );
    assert.equal(
      f.db
        .prepare("SELECT name FROM sqlite_master WHERE name='example_other'")
        .get(),
      undefined,
    );
    assert.throws(
      () =>
        registerModules({ db: f.db, config: f.config }, [
          {
            id: "one",
            contractVersion: 1,
            create: () => ({ business: f.services.business }),
          },
          {
            id: "two",
            contractVersion: 1,
            create: () => ({ business: f.services.business }),
          },
        ]),
      /Multiple providers/,
    );
  } finally {
    f.close();
  }
});
test("configuration fails closed for unknown profiles and insecure public binding", () => {
  assert.throws(
    () => loadConfig({ WIDEMIND_PROFILE: "live-magic" }),
    /profile/,
  );
  assert.throws(
    () => loadConfig({ WIDEMIND_HOST: "0.0.0.0" }),
    /secure cookies/,
  );
  assert.throws(
    () => loadConfig({ WIDEMIND_PUBLIC_ORIGIN: "https://example.test" }),
    /secure cookies/,
  );
  assert.throws(
    () => loadConfig({ WIDEMIND_LEASE_MS: "NaN" }),
    /configuration/,
  );
  const { config, secrets } = loadConfig({
    WIDEMIND_OPERATOR_PASSWORD_HASH: "private-fixture",
  });
  assert.equal("operatorPasswordHash" in config, false);
  assert.equal(secrets.operatorPasswordHash, "private-fixture");
});

test("secure-cookie deployment rejects a non-HTTPS public origin", () => {
  assert.throws(
    () =>
      loadConfig({
        WIDEMIND_HOST: "0.0.0.0",
        WIDEMIND_SECURE_COOKIES: "true",
        WIDEMIND_PUBLIC_ORIGIN: "http://example.test",
      }),
    /HTTPS public origin/,
  );
});
