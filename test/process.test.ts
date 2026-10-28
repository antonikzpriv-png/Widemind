import test from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import type { ChildProcess } from "node:child_process";
import { setTimeout as delay } from "node:timers/promises";
import { webServer } from "../src/web/server.ts";
import { fixture } from "./helpers.ts";
async function waitFor(predicate: () => boolean, diagnostic: () => string) {
  const until = Date.now() + 15000;
  while (Date.now() < until) {
    if (predicate()) return;
    await delay(25);
  }
  assert.fail(`Timed out: ${diagnostic()}`);
}
async function stop(child: ChildProcess) {
  if (child.exitCode !== null || child.signalCode !== null) return;
  const done = new Promise<void>((resolve) =>
    child.once("exit", () => resolve()),
  );
  child.kill("SIGTERM");
  const timeout = setTimeout(() => child.kill("SIGKILL"), 3000);
  try {
    await done;
  } finally {
    clearTimeout(timeout);
  }
}
test("worker outlives the website, survives process restart, and competing workers do not duplicate work", async () => {
  const f = fixture();
  const children: ChildProcess[] = [];
  let logs = "";
  let webOpen = true;
  const web = webServer({ ...f, secrets: { operatorPasswordHash: null } });
  try {
    await new Promise<void>((resolve) => web.listen(0, "127.0.0.1", resolve));
    const address = web.address();
    assert.ok(address && typeof address !== "string");
    assert.equal(
      (await fetch(`http://127.0.0.1:${address.port}/healthz`)).status,
      200,
    );
    const launch = () => {
      const child = spawn(process.execPath, ["src/runtime/main.ts"], {
        cwd: process.cwd(),
        env: {
          ...process.env,
          WIDEMIND_DATABASE: f.config.database,
          WIDEMIND_PROFILE: "demo",
          WIDEMIND_LEASE_MS: "3000",
          WIDEMIND_IDLE_MS: "1000",
        },
        stdio: ["ignore", "pipe", "pipe"],
      });
      child.stdout?.on("data", (chunk) => {
        logs += String(chunk);
      });
      child.stderr?.on("data", (chunk) => {
        logs += String(chunk);
      });
      children.push(child);
      return child;
    };
    launch();
    launch();
    await waitFor(
      () => logs.split("worker started").length === 3,
      () => logs,
    );
    await new Promise<void>((resolve, reject) =>
      web.close((error) => (error ? reject(error) : resolve())),
    );
    webOpen = false;
    const id = f.store.create("demo");
    f.store.control(id, "resume");
    await waitFor(
      () => f.store.run(id)?.state === "waiting",
      () => JSON.stringify(f.store.run(id)) + " " + logs,
    );
    assert.equal(f.store.actions(id).length, 1);
    assert.equal(f.store.actions(id)[0]?.attempts, 1);
    assert.equal(f.store.actions(id)[0]?.state, "completed");
    await Promise.all(children.map(stop));
    const before = f.store.actions(id)[0]?.id;
    logs = "";
    launch();
    await waitFor(
      () => logs.includes("worker started"),
      () => logs,
    );
    await delay(600);
    assert.equal(f.store.run(id)?.state, "waiting");
    assert.equal(f.store.actions(id)[0]?.id, before);
    assert.equal(f.store.actions(id)[0]?.attempts, 1);
  } finally {
    await Promise.all(children.map(stop));
    if (webOpen)
      await new Promise<void>((resolve) => web.close(() => resolve()));
    f.close();
  }
});
