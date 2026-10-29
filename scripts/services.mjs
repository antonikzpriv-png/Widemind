/** Local process orchestration; credentials stay in environment, never argv. */
import { spawn, spawnSync } from "node:child_process";
import { mkdir, writeFile, access } from "node:fs/promises";
import { resolve, join } from "node:path";
import { randomBytes, randomUUID } from "node:crypto";
import { setTimeout as delay } from "node:timers/promises";
process.umask(0o077);
const root = process.cwd(),
  operation = process.argv[2] ?? "status",
  names = ["economy", "tools", "business"];
const python = (name) =>
  join(
    root,
    ".venvs",
    name,
    process.platform === "win32" ? "Scripts/python.exe" : "bin/python",
  );
const path = (name) => resolve(process.env[name] ?? "");
const runtimeDatabase = resolve(
  process.env.WIDEMIND_DATABASE ?? "var/widemind.sqlite",
);
function environment(name) {
  const env = { ...process.env, WIDEMIND_RUNTIME_DATABASE: runtimeDatabase };
  if (name === "economy")
    Object.assign(env, {
      WIDEMIND_OPERATOR_TOKEN: env.WIDEMIND_ECONOMY_OPERATOR_TOKEN ?? "",
      WIDEMIND_GATEWAY_TOKEN: env.WIDEMIND_ECONOMY_GATEWAY_TOKEN ?? "",
      WIDEMIND_AGENT_TOKEN: env.WIDEMIND_ECONOMY_AGENT_TOKEN ?? "",
      WIDEMIND_AGENT_RUN_ID: env.WIDEMIND_COMMERCE_RUN_ID ?? "unconfigured",
      WIDEMIND_ECONOMY_DB: resolve(
        env.WIDEMIND_ECONOMY_DB ?? "var/economy.sqlite",
      ),
    });
  if (name === "tools")
    Object.assign(env, {
      WIDEMIND_TOOLS_STATE: resolve(
        env.WIDEMIND_TOOLS_STATE ?? "var/tools/control",
      ),
      WIDEMIND_TOOLS_WORKSPACE: resolve(
        env.WIDEMIND_TOOLS_WORKSPACE ?? "var/workspace",
      ),
    });
  if (name === "business")
    Object.assign(env, {
      WIDEMIND_BUSINESS_DB: resolve(
        env.WIDEMIND_BUSINESS_DB ?? "var/business.sqlite",
      ),
    });
  return env;
}
function command(name) {
  const url = new URL(
    process.env["WIDEMIND_" + name.toUpperCase() + "_URL"] ??
      `http://127.0.0.1:${8080 + names.indexOf(name)}`,
  );
  if (!["127.0.0.1", "localhost", "[::1]"].includes(url.hostname))
    throw new Error("Local launcher only starts loopback services");
  const module = {
    economy: "widemind.economy.cli",
    tools: "widemind.integrated_service",
    business: "widemind.business.host_server",
  }[name];
  return {
    binary: python(name),
    args: ["-m", module, "--host", url.hostname, "--port", url.port || "80"],
    env: environment(name),
    url: url.origin,
  };
}
async function configure() {
  await mkdir("var", { recursive: true, mode: 0o700 });
  const file = resolve("var/integration.env");
  const token = () => randomBytes(32).toString("hex");
  const run = randomUUID();
  const values = {
    WIDEMIND_PROFILE: "foundation",
    WIDEMIND_ECONOMY_URL: "http://127.0.0.1:8080",
    WIDEMIND_TOOLS_URL: "http://127.0.0.1:8081",
    WIDEMIND_BUSINESS_URL: "http://127.0.0.1:8082",
    WIDEMIND_ECONOMY_OPERATOR_TOKEN: token(),
    WIDEMIND_ECONOMY_GATEWAY_TOKEN: token(),
    WIDEMIND_ECONOMY_AGENT_TOKEN: token(),
    WIDEMIND_TOOLS_TOKEN: token(),
    WIDEMIND_BUSINESS_TOKEN: token(),
    WIDEMIND_COMMERCE_RUN_ID: run,
    WIDEMIND_PAYMENT_PROVIDER: "paypal",
    WIDEMIND_BUSINESS_MODE: "test",
  };
  await writeFile(
    file,
    Object.entries(values)
      .map(([key, value]) => key + "=" + value)
      .join("\n") + "\n",
    { flag: "wx", mode: 0o600 },
  );
  console.log(
    "Private integration configuration created at var/integration.env. No provider credentials or funded run were created. Existing files are never overwritten.",
  );
}
async function setup() {
  await mkdir(".venvs", { recursive: true });
  for (const name of names) {
    const result = spawnSync(
      process.env.WIDEMIND_PYTHON ?? "python3",
      ["-m", "venv", join(".venvs", name)],
      { stdio: "inherit" },
    );
    if (result.status !== 0)
      throw new Error("Virtual environment creation failed: " + name);
    const pip = spawnSync(
      python(name),
      [
        "-m",
        "pip",
        "install",
        "--cache-dir",
        resolve("var/pip-cache"),
        "-e",
        resolve("services/" + name),
      ],
      { stdio: "inherit" },
    );
    if (pip.status !== 0) throw new Error("Service install failed: " + name);
  }
  console.log(
    "Isolated service environments installed. Provider accounts remain unconfigured.",
  );
}
async function status() {
  let failed = false;
  for (const name of names) {
    const { url } = command(name);
    try {
      const response = await fetch(url + "/health", {
        signal: AbortSignal.timeout(3000),
      });
      console.log(
        JSON.stringify({
          service: name,
          reachable: response.ok,
          status: response.status,
        }),
      );
      if (!response.ok) failed = true;
    } catch {
      failed = true;
      console.log(JSON.stringify({ service: name, reachable: false }));
    }
  }
  if (failed) process.exitCode = 2;
}
async function start(selected, includeRuntime = false) {
  const children = [];
  let stopping = false;
  const stop = () => {
    if (stopping) return;
    stopping = true;
    for (const child of children) child.kill("SIGTERM");
    const timer = setTimeout(() => {
      for (const child of children)
        if (child.exitCode === null) child.kill("SIGKILL");
    }, 10000);
    timer.unref();
  };
  for (const signal of ["SIGINT", "SIGTERM"]) process.once(signal, stop);
  try {
    for (const name of selected) {
      const spec = command(name);
      await access(spec.binary);
      const child = spawn(spec.binary, spec.args, {
        env: spec.env,
        stdio: "inherit",
      });
      children.push(child);
      child.on("error", stop);
      child.on("exit", () => {
        if (!stopping) {
          process.exitCode = 1;
          stop();
        }
      });
      const until = Date.now() + 15000;
      let ready = false;
      while (Date.now() < until && !stopping) {
        try {
          const response = await fetch(spec.url + "/health", {
            signal: AbortSignal.timeout(1000),
          });
          if (response.ok) {
            ready = true;
            break;
          }
        } catch {}
        await delay(100);
      }
      if (!ready) throw new Error("Service did not become ready: " + name);
    }
    if (includeRuntime) {
      for (const file of ["src/web/main.ts", "src/runtime/main.ts"]) {
        const child = spawn(process.execPath, [file], {
          env: process.env,
          stdio: "inherit",
        });
        children.push(child);
        child.on("error", stop);
        child.on("exit", () => {
          if (!stopping) {
            process.exitCode = 1;
            stop();
          }
        });
      }
    }
    await Promise.all(
      children.map(
        (child) =>
          new Promise((resolve) =>
            child.exitCode !== null || child.signalCode !== null
              ? resolve()
              : child.once("exit", resolve),
          ),
      ),
    );
  } finally {
    stop();
  }
}
try {
  if (operation === "configure") await configure();
  else if (operation === "setup") await setup();
  else if (operation === "status") await status();
  else if (operation === "start")
    await start(process.argv[3] ? [process.argv[3]] : names);
  else if (operation === "all") await start(names, true);
  else throw new Error("Use configure, setup, status, start [service], or all");
} catch (error) {
  console.error(error.message);
  process.exitCode = 1;
}
