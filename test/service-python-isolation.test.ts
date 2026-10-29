import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import {
  mkdtempSync,
  mkdirSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import test from "node:test";

test(
  "service test launcher strips inherited package selectors and deployment authority",
  { skip: process.platform === "win32" },
  () => {
    const directory = mkdtempSync(join(tmpdir(), "widemind-python-isolation-"));
    try {
      const output = join(directory, "selectors.jsonl");
      for (const service of ["economy", "tools", "business"]) {
        const bin = join(directory, ".venvs", service, "bin");
        mkdirSync(bin, { recursive: true });
        writeFileSync(
          join(bin, "python"),
          `#!/usr/bin/env node
const fs = require('node:fs');
fs.appendFileSync(process.env.FIXTURE_SELECTOR_OUTPUT, JSON.stringify({pythonPath: Object.hasOwn(process.env, 'PYTHONPATH'), pythonHome: Object.hasOwn(process.env, 'PYTHONHOME'), gateway: Object.hasOwn(process.env, 'WIDEMIND_GATEWAY_TOKEN'), argv: process.argv.slice(2)}) + '\\n');
`,
          { mode: 0o700 },
        );
      }
      const result = spawnSync(
        process.execPath,
        [resolve("scripts/services.mjs"), "test"],
        {
          cwd: directory,
          env: {
            ...process.env,
            PYTHONPATH: "/explicit-fixture-wrong-package",
            PYTHONHOME: "/explicit-fixture-wrong-interpreter",
            WIDEMIND_GATEWAY_TOKEN: "fixture-only-deployment-authority",
            FIXTURE_SELECTOR_OUTPUT: output,
          },
          encoding: "utf8",
          timeout: 10000,
        },
      );
      assert.equal(result.status, 0, result.stderr);
      const observed = readFileSync(output, "utf8")
        .trim()
        .split("\n")
        .map((line) => JSON.parse(line));
      assert.equal(observed.length, 3);
      for (const item of observed) {
        assert.equal(item.pythonPath, false);
        assert.equal(item.pythonHome, false);
        assert.equal(item.gateway, false);
        assert.equal(item.argv[0], "-I");
        assert.equal(item.argv[1], "-m");
      }
    } finally {
      rmSync(directory, { recursive: true, force: true });
    }
  },
);
