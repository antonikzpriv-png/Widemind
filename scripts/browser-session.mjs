/** Minimal native-CDP harness for the integrated customer/operator acceptance gate. */
import { spawn } from "node:child_process";
import { mkdir, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { setTimeout as delay } from "node:timers/promises";
export async function browserSession(directory, output) {
  await mkdir(output, { recursive: true });
  const pending = new Map(),
    errors = [];
  let sequence = 0,
    sessionId,
    socket,
    endpoint = "",
    stderr = "";
  const chrome = spawn(
    process.env.WIDEMIND_CHROMIUM ?? "/usr/bin/chromium",
    [
      "--headless",
      "--no-sandbox",
      "--disable-dev-shm-usage",
      "--remote-debugging-port=0",
      "--user-data-dir=" + join(directory, "chrome"),
    ],
    { stdio: ["ignore", "ignore", "pipe"] },
  );
  chrome.on("error", (error) =>
    errors.push(error.code ?? "browser-launch-failed"),
  );
  chrome.stderr.on("data", (chunk) => {
    stderr = (stderr + chunk).slice(-8192);
    endpoint =
      stderr.match(/DevTools listening on (ws:\/\/[^\s]+)/)?.[1] ?? endpoint;
  });
  async function waitFor(fn) {
    const until = Date.now() + 15000;
    while (Date.now() < until) {
      if (await fn()) return;
      await delay(50);
    }
    throw new Error("Integrated browser condition timed out");
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
  async function close() {
    for (const task of pending.values()) {
      clearTimeout(task.timer);
      task.reject(new Error("Browser closed"));
    }
    pending.clear();
    socket?.close();
    if (chrome.exitCode === null && chrome.signalCode === null) {
      const done = new Promise((resolve) => chrome.once("exit", resolve));
      chrome.kill("SIGTERM");
      const timer = setTimeout(() => chrome.kill("SIGKILL"), 2000);
      try {
        await done;
      } finally {
        clearTimeout(timer);
      }
    }
  }
  try {
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
    await send(
      "Browser.setDownloadBehavior",
      { behavior: "allow", downloadPath: join(directory, "downloads") },
      null,
    );
    async function evaluate(expression) {
      const result = await send("Runtime.evaluate", {
        expression,
        returnByValue: true,
        awaitPromise: true,
      });
      if (result.exceptionDetails)
        throw new Error("Integrated browser evaluation failed");
      return result.result.value;
    }
    async function click(selector) {
      const point = await evaluate(
        `(()=>{const el=document.querySelector(${JSON.stringify(selector)});if(!el)throw new Error('Missing element');el.scrollIntoView({block:'center'});const r=el.getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2};})()`,
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
    return {
      errors,
      waitFor,
      evaluate,
      click,
      close,
      // CDP can inspect the test frame without granting the application origin
      // access to it. This helper never changes the iframe sandbox or its CSP.
      async evaluateFrame(name, expression) {
        const { root } = await send("DOM.getDocument");
        const { nodeId } = await send("DOM.querySelector", {
          nodeId: root.nodeId,
          selector: "iframe[name=" + JSON.stringify(name) + "]",
        });
        if (!nodeId) throw new Error("Missing preview element");
        const { node } = await send("DOM.describeNode", { nodeId });
        const frameId = node.frameId;
        if (!frameId) throw new Error("Missing preview frame ID");
        let frameSession = sessionId;
        const { targetInfos } = await send("Target.getTargets", {}, null);
        const remote = targetInfos.find(
          (target) => target.type === "iframe" && target.targetId === frameId,
        );
        if (remote) {
          // Opaque frames can have a separate renderer process. Attach CDP to
          // that test target; do not weaken site isolation or sandbox flags.
          ({ sessionId: frameSession } = await send(
            "Target.attachToTarget",
            {
              targetId: remote.targetId,
              flatten: true,
            },
            null,
          ));
        }
        const { executionContextId } = await send(
          "Page.createIsolatedWorld",
          {
            frameId,
            worldName: "widemind-ui-audit",
          },
          frameSession,
        );
        const result = await send(
          "Runtime.evaluate",
          {
            expression,
            contextId: executionContextId,
            returnByValue: true,
            awaitPromise: true,
          },
          frameSession,
        );
        if (result.exceptionDetails)
          throw new Error("Preview evaluation failed");
        return result.result.value;
      },
      async fill(selector, text) {
        await click(selector);
        await send("Input.insertText", { text });
      },
      async navigate(url) {
        await send("Page.navigate", { url });
        await waitFor(() =>
          evaluate(
            `location.href===${JSON.stringify(url)}&&document.readyState==='complete'`,
          ),
        );
      },
      async viewport(width, height, mobile = false) {
        await send("Emulation.setDeviceMetricsOverride", {
          width,
          height,
          deviceScaleFactor: 1,
          mobile,
        });
      },
      async screenshot(name) {
        const { data } = await send("Page.captureScreenshot", {
          captureBeyondViewport: true,
        });
        await writeFile(
          join(output, name + ".png"),
          Buffer.from(data, "base64"),
        );
      },
    };
  } catch (error) {
    await close();
    throw error;
  }
}
