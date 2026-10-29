import test from "node:test";
import assert from "node:assert/strict";
import { DatabaseSync } from "node:sqlite";
import { execFileSync } from "node:child_process";
import {
  existsSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  statSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";
test("shared template plus private service configuration selects an isolated authority", () => {
  const directory = mkdtempSync(join(tmpdir(), "widemind-configuration-"));
  const env = Object.fromEntries(
    Object.entries(process.env).filter(([key]) => !key.startsWith("WIDEMIND_")),
  );
  try {
    const template = resolve(".env.example"),
      launcher = resolve("scripts/services.mjs");
    execFileSync(
      process.execPath,
      ["--env-file=" + template, launcher, "configure"],
      { cwd: directory, env, stdio: "pipe" },
    );
    const file = join(directory, "var/integration.env");
    assert.equal(statSync(file).mode & 0o777, 0o600);
    assert.match(
      readFileSync(file, "utf8"),
      /WIDEMIND_PAYMENT_PROVIDER=paypal/,
    );
    const code = `import {createEconomyClient} from ${JSON.stringify(pathToFileURL(resolve("src/modules/economy/service-client.ts")).href)}; console.log(JSON.stringify({configured:!!createEconomyClient(),runtime:process.env.WIDEMIND_DATABASE,ledger:process.env.WIDEMIND_ECONOMY_DB,unprefixed:!!process.env.WIDEMIND_GATEWAY_TOKEN}));`;
    const data = JSON.parse(
      execFileSync(
        process.execPath,
        [
          "--env-file=" + template,
          "--env-file=" + file,
          "--input-type=module",
          "-e",
          code,
        ],
        { cwd: directory, env, encoding: "utf8" },
      ),
    );
    assert.equal(data.configured, true);
    assert.equal(data.unprefixed, false);
    assert.equal(
      data.runtime,
      join(directory, "var/integrated-runtime.sqlite"),
    );
    assert.equal(data.ledger, join(directory, "var/integrated-economy.sqlite"));
    execFileSync(
      process.execPath,
      [
        "--env-file=" + template,
        "--env-file=" + file,
        resolve("src/db/migrate-main.ts"),
      ],
      { cwd: directory, env, stdio: "pipe" },
    );
    const db = new DatabaseSync(data.runtime, { readOnly: true });
    try {
      assert.equal(
        db.prepare("SELECT COUNT(*) AS n FROM runtime_runs").get()?.n,
        0,
      );
    } finally {
      db.close();
    }
    assert.equal(
      existsSync(data.ledger),
      false,
      "Migration must not open or fund the accounting wallet",
    );
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
});
