// Production entrypoint acceptance: no live accounts, funds, or paid tools.
import { spawn, spawnSync } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createServer } from "node:net";
import { randomBytes } from "node:crypto";
import { setTimeout as delay } from "node:timers/promises";
import assert from "node:assert/strict";
import { hashPassword } from "../dist/auth/password.js";
const directory = await mkdtemp(join(tmpdir(), "widemind-production-"));
const probe = createServer();
await new Promise((resolve) => probe.listen(0, "127.0.0.1", resolve));
const port = probe.address().port;
await new Promise((resolve) => probe.close(resolve));
const origin = `http://127.0.0.1:${port}`,
  password = randomBytes(32).toString("hex");
const env = {
  ...process.env,
  WIDEMIND_DATABASE: join(directory, "state.sqlite"),
  WIDEMIND_PROFILE: "demo",
  WIDEMIND_HOST: "127.0.0.1",
  WIDEMIND_PORT: String(port),
  WIDEMIND_PUBLIC_ORIGIN: origin,
  WIDEMIND_SECURE_COOKIES: "false",
  WIDEMIND_OPERATOR_PASSWORD_HASH: await hashPassword(password),
};
const children = [];
let logs = "";
const launch = (file) => {
  const child = spawn(process.execPath, [file], {
    env,
    stdio: ["ignore", "pipe", "pipe"],
  });
  child.stdout.on("data", (data) => {
    logs += data;
  });
  child.stderr.on("data", (data) => {
    logs += data;
  });
  children.push(child);
  return child;
};
async function waitFor(fn) {
  const until = Date.now() + 10000;
  while (Date.now() < until) {
    if (await fn()) return;
    await delay(40);
  }
  throw new Error("Production launch acceptance timed out");
}
async function stop(child) {
  if (child.exitCode !== null || child.signalCode !== null) return;
  const done = new Promise((resolve) => child.once("exit", resolve));
  child.kill("SIGTERM");
  const timer = setTimeout(() => child.kill("SIGKILL"), 2000);
  try {
    await done;
  } finally {
    clearTimeout(timer);
  }
}
try {
  const migration = spawnSync("npm", ["run", "migrate"], {
    env,
    encoding: "utf8",
  });
  assert.equal(migration.status, 0, migration.stderr);
  launch("dist/web/main.js");
  launch("dist/runtime/main.js");
  await waitFor(async () => {
    try {
      return (await fetch(origin + "/healthz")).ok;
    } catch {
      return false;
    }
  });
  const post = (path, body = {}, headers = {}) =>
    fetch(origin + path, {
      method: "POST",
      headers: {
        Origin: origin,
        "Content-Type": "application/json",
        ...headers,
      },
      body: JSON.stringify(body),
    });
  const login = await post("/api/operator/login", { password });
  assert.equal(login.status, 200);
  const cookie = login.headers.get("set-cookie").split(";")[0],
    { csrf } = await login.json();
  const headers = { Cookie: cookie, "x-csrf-token": csrf };
  const created = await post("/api/operator/runs", {}, headers);
  assert.equal(created.status, 201);
  const { runId } = await created.json();
  assert.equal(
    (await post(`/api/operator/runs/${runId}/resume`, {}, headers)).status,
    200,
  );
  let status;
  await waitFor(async () => {
    status = await (
      await fetch(origin + "/api/operator/status", {
        headers: { Cookie: cookie },
      })
    ).json();
    return (
      status.runs[0]?.state === "waiting" &&
      status.runs[0]?.actions[0]?.state === "completed"
    );
  });
  assert.equal(status.runs[0].actions[0].attempts, 1);
  const data = await (await fetch(origin + "/api/public")).json();
  assert.equal(data.financial, null);
  assert.equal(data.runs[0].profile, "demo");
  assert.equal("actions" in data.runs[0], false);
  assert.match(
    await (await fetch(origin + "/")).text(),
    /Unverified \/ unavailable/,
  );
  assert.match(
    await (
      await fetch(origin + "/operator", { headers: { Cookie: cookie } })
    ).text(),
    /Run control/,
  );
  console.log(
    JSON.stringify({
      passed: true,
      mode: "zero-cost local demo",
      migration: true,
      compiledWeb: true,
      compiledWorker: true,
      authenticatedControls: true,
      durableAction: true,
      financialSummary: null,
    }),
  );
} catch (error) {
  console.error(String(error));
  console.error(logs);
  process.exitCode = 1;
} finally {
  await Promise.all(children.map(stop));
  await rm(directory, { recursive: true, force: true });
}
