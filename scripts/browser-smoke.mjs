// Real browser form regression, using Node's built-in WebSocket/CDP client.
// Requires npm run build and a Chromium executable; no browser SDK dependency.
import { spawn } from "node:child_process";
import { mkdtemp, rm, mkdir, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { randomBytes } from "node:crypto";
import { setTimeout as delay } from "node:timers/promises";
import assert from "node:assert/strict";
import { bootstrap } from "../dist/bootstrap.js";
import { webServer } from "../dist/web/server.js";
import { hashPassword } from "../dist/auth/password.js";
const directory = await mkdtemp(join(tmpdir(), "widemind-browser-"));
const output = resolve("var/browser-smoke");
await mkdir(output, { recursive: true });
const password = randomBytes(24).toString("hex");
const app = await bootstrap({
  WIDEMIND_DATABASE: join(directory, "db.sqlite"),
  WIDEMIND_PROFILE: "demo",
  WIDEMIND_OPERATOR_PASSWORD_HASH: await hashPassword(password),
});
const server = webServer(app);
let chrome, socket;
const pending = new Map();
let sequence = 0,
  sessionId;
const errors = [];
async function waitFor(fn) {
  const until = Date.now() + 15000;
  while (Date.now() < until) {
    if (await fn()) return;
    await delay(40);
  }
  throw new Error("Browser condition timed out");
}
function send(method, params = {}, session = sessionId) {
  return new Promise((resolve, reject) => {
    const id = ++sequence;
    const timer = setTimeout(() => {
      pending.delete(id);
      reject(new Error("CDP timed out: " + method));
    }, 15000);
    pending.set(id, { resolve, reject, timer });
    socket.send(
      JSON.stringify({
        id,
        method,
        params,
        ...(session ? { sessionId: session } : {}),
      }),
    );
  });
}
async function evaluate(expression) {
  const result = await send("Runtime.evaluate", {
    expression,
    returnByValue: true,
    awaitPromise: true,
  });
  if (result.exceptionDetails) throw new Error("Browser evaluation failed");
  return result.result.value;
}
async function click(selector) {
  const point = await evaluate(
    `(()=>{const el=document.querySelector(${JSON.stringify(selector)});el.scrollIntoView({block:'center'});const r=el.getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2};})()`,
  );
  await send("Input.dispatchMouseEvent", {
    type: "mousePressed",
    button: "left",
    clickCount: 1,
    ...point,
  });
  await send("Input.dispatchMouseEvent", {
    type: "mouseReleased",
    button: "left",
    clickCount: 1,
    ...point,
  });
}
async function navigate(url) {
  await send("Page.navigate", { url });
  await waitFor(() =>
    evaluate(
      `location.href===${JSON.stringify(url)}&&document.readyState==='complete'`,
    ),
  );
}
async function shot(name) {
  const { data } = await send("Page.captureScreenshot", {
    captureBeyondViewport: true,
  });
  await writeFile(join(output, name + ".png"), Buffer.from(data, "base64"));
}
try {
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  app.config.publicOrigin = origin;
  chrome = spawn(
    process.env.WIDEMIND_CHROMIUM ?? "/usr/bin/chromium",
    [
      "--headless",
      "--no-sandbox",
      "--disable-dev-shm-usage",
      "--remote-debugging-port=0",
      `--user-data-dir=${join(directory, "chrome")}`,
    ],
    { stdio: ["ignore", "ignore", "pipe"] },
  );
  let endpoint = "";
  let stderr = "";
  chrome.stderr.on("data", (data) => {
    stderr = (stderr + data).slice(-8192);
    endpoint =
      stderr.match(/DevTools listening on (ws:\/\/[^\s]+)/)?.[1] ?? endpoint;
  });
  chrome.on("error", (error) => {
    errors.push(error.code ?? "browser-launch-failed");
  });
  await waitFor(() => endpoint.length > 0);
  socket = new WebSocket(endpoint);
  await new Promise((resolve, reject) => {
    socket.addEventListener("open", resolve, { once: true });
    socket.addEventListener("error", reject, { once: true });
  });
  socket.addEventListener("message", (event) => {
    const message = JSON.parse(event.data);
    if (message.id) {
      const task = pending.get(message.id);
      if (!task) return;
      pending.delete(message.id);
      clearTimeout(task.timer);
      if (message.error) task.reject(new Error(message.error.message));
      else task.resolve(message.result);
    } else if (message.method === "Runtime.exceptionThrown")
      errors.push("page-exception");
  });
  const { targetId } = await send("Target.createTarget", {
    url: "about:blank",
  });
  ({ sessionId } = await send("Target.attachToTarget", {
    targetId,
    flatten: true,
  }));
  await send("Page.enable");
  await send("Runtime.enable");
  await send("Emulation.setDeviceMetricsOverride", {
    width: 1280,
    height: 900,
    deviceScaleFactor: 1,
    mobile: false,
  });
  await navigate(origin + "/");
  await shot("headquarters-desktop");
  await navigate(origin + "/operator");
  await click("input[name=password]");
  await send("Input.insertText", { text: password });
  await click("form button");
  await waitFor(() => evaluate("document.title.startsWith('Run control')"));
  await click('form[action="/operator/runs"] button');
  await waitFor(() =>
    evaluate("document.querySelector('section .pill')?.textContent==='paused'"),
  );
  await send("Emulation.setDeviceMetricsOverride", {
    width: 390,
    height: 844,
    deviceScaleFactor: 1,
    mobile: true,
  });
  assert.equal(
    await evaluate("document.documentElement.scrollWidth<=innerWidth"),
    true,
  );
  assert.equal(
    await evaluate(
      "document.querySelector('[aria-label=\"Worker health\"]').innerText.includes('Worker: unavailable')",
    ),
    true,
  );
  await click('details:has(form[action$="/events"]) > summary');
  await click('form[action$="/events"] button');
  await waitFor(() =>
    evaluate(
      "document.querySelector('details:has(form[action$=\"/events\"]) summary').textContent.includes('(1 pending)')",
    ),
  );
  await shot("operator-phone");
  await click('form[action$="/resume"] button');
  await waitFor(() =>
    evaluate("document.querySelector('section .pill')?.textContent==='active'"),
  );
  await click('form[action$="/pause"] button');
  await waitFor(() =>
    evaluate("document.querySelector('section .pill')?.textContent==='paused'"),
  );
  await navigate(origin + "/");
  assert.equal(
    await evaluate("document.documentElement.scrollWidth<=innerWidth"),
    true,
  );
  await shot("headquarters-phone");
  assert.deepEqual(errors, []);
  const report = {
    passed: true,
    profile: "demo",
    desktopAndPhone: true,
    realFormLoginCreateResumePause: true,
    operatorEventForm: true,
    workerHealth: true,
    pageErrors: errors,
  };
  await writeFile(
    join(output, "report.json"),
    JSON.stringify(report, null, 2) + "\n",
  );
  console.log(JSON.stringify(report));
} finally {
  for (const task of pending.values()) {
    clearTimeout(task.timer);
    task.reject(new Error("Browser closed"));
  }
  pending.clear();
  socket?.close();
  if (chrome && chrome.exitCode === null && chrome.signalCode === null) {
    const done = new Promise((resolve) => chrome.once("exit", resolve));
    chrome.kill("SIGTERM");
    const timer = setTimeout(() => chrome.kill("SIGKILL"), 2000);
    try {
      await done;
    } finally {
      clearTimeout(timer);
    }
  }
  await new Promise((resolve) => server.close(resolve));
  app.db.close();
  await rm(directory, { recursive: true, force: true });
}
