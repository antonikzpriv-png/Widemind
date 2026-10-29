/** Synthetic portal fixture. Generated-script isolation is exercised in a real browser. */
import assert from "node:assert/strict";
import { createServer } from "node:http";
import {
  mkdtemp,
  rm,
  mkdir,
  writeFile,
  readdir,
  readFile,
} from "node:fs/promises";
import { join, resolve } from "node:path";
import { tmpdir } from "node:os";
import { bootstrap } from "../dist/bootstrap.js";
import { Store } from "../dist/runtime/store.js";
import { webServer } from "../dist/web/server.js";
import { browserSession } from "./browser-session.mjs";

const dir = await mkdtemp(join(tmpdir(), "widemind-creative-portal-"));
const output = resolve("var/creative-portal-browser");
await mkdir(output, { recursive: true });
const token = "a".repeat(43),
  runId = "00000000-0000-4000-8000-000000000001";
const received = [];
const source =
  '<!doctype html><meta charset="utf-8"><style>body{font:20px system-ui;background:#f4f1e9;color:#182b3b;padding:24px}input{font:inherit;padding:10px}output{display:block;padding-top:20px}</style><h1>Custom price calculator</h1><label>Quantity <input type="number" id="quantity" value="2"></label><output id="result">50</output><script>quantity.oninput=()=>result.textContent=Number(quantity.value)*25;const evidence={parentBlocked:false,cookieBlocked:false,fetchBlocked:false};try{parent.document.body.dataset.compromised="yes"}catch{evidence.parentBlocked=true}try{document.cookie}catch{evidence.cookieBlocked=true}fetch("/api/operator/status").catch(()=>{evidence.fetchBlocked=true;parent.postMessage({type:"sandbox-evidence",...evidence},"*")});quantity.value="3";quantity.dispatchEvent(new Event("input"));parent.postMessage({type:"calculator-result",result:result.textContent},"*");</script>';
const upstream = createServer(async (req, res) => {
  let raw = "";
  for await (const part of req) raw += part;
  const body = JSON.parse(raw);
  received.push({ path: req.url, body });
  res.setHeader("Content-Type", "application/json");
  if (req.url === "/v1/offerings") {
    res.end(
      JSON.stringify({
        offerings: [
          {
            id: "custom-graphic",
            title: "Custom graphic",
            description: "Customer-specific layout",
            available: true,
            priceMicros: "3000000",
          },
          {
            id: "interactive-calculator",
            title: "Interactive calculator",
            description: "Verified calculator",
            available: true,
            priceMicros: "4000000",
          },
          {
            id: "software-starter",
            title: "Static software template",
            description: "Static template",
            available: true,
            priceMicros: "1000000",
          },
        ],
      }),
    );
    return;
  }
  if (req.url === "/v1/request") {
    res.end(
      JSON.stringify({
        opportunityId: "new-opportunity",
        customerToken: body.customerToken,
      }),
    );
    return;
  }
  if (body.token !== token) {
    res.writeHead(403);
    res.end(JSON.stringify({ error: "Private access required" }));
    return;
  }
  if (req.url === "/v1/customer") {
    res.end(
      JSON.stringify({
        status: "completed",
        mode: "test",
        quotes: [],
        notifications: [],
        orders: [
          {
            orderId: "private-order",
            status: "completed",
            deliveryAvailable: true,
            revision: {
              used: 0,
              limit: 1,
              remaining: 1,
              available: true,
              deadline: Date.now() / 1000 + 3600,
            },
            fulfillment: { stage: "delivery", status: "completed" },
            artifactVersions: [
              {
                version: 1,
                createdAt: new Date().toISOString(),
                checks: { acceptance_cases: 2 },
                files: [
                  {
                    filename: "index.html",
                    mediaType: "text/html",
                    size: source.length,
                    sha256: "f".repeat(64),
                    role: "original",
                  },
                  {
                    filename: "README.md",
                    mediaType: "text/markdown",
                    size: 10,
                    sha256: "e".repeat(64),
                    role: "instructions",
                  },
                ],
              },
            ],
          },
        ],
      }),
    );
    return;
  }
  if (req.url === "/v1/delivery") {
    res.end(
      JSON.stringify({
        filename: body.filename || "index.html",
        mediaType:
          body.filename === "README.md" ? "text/markdown" : "text/html",
        encoding: "utf-8",
        content: body.filename === "README.md" ? "Usage fixture" : source,
      }),
    );
    return;
  }
  if (req.url === "/v1/manifest") {
    res.end(
      JSON.stringify({
        version: 1,
        private: true,
        scope: { title: "Synthetic fixture" },
        files: [],
      }),
    );
    return;
  }
  if (req.url === "/v1/change-request") {
    res.end(JSON.stringify({ recorded: true }));
    return;
  }
  res.end("{}");
});
let app, server, browser;
const listen = async (server) => {
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  return "http://127.0.0.1:" + server.address().port;
};
try {
  const upstreamOrigin = await listen(upstream);
  app = await bootstrap({
    WIDEMIND_DATABASE: join(dir, "runtime.sqlite"),
    WIDEMIND_PROFILE: "foundation",
    WIDEMIND_BUSINESS_URL: upstreamOrigin,
    WIDEMIND_BUSINESS_TOKEN: "b".repeat(48),
    WIDEMIND_COMMERCE_RUN_ID: runId,
  });
  new Store(app.db).create("foundation", Date.now(), runId);
  server = webServer(app);
  const origin = await listen(server);
  app.config.publicOrigin = origin;
  browser = await browserSession(dir, output);
  await browser.viewport(1280, 900);
  await browser.navigate(origin + "/commerce#customer=" + token);
  await browser.waitFor(() =>
    browser.evaluate(
      "document.querySelector('#customer-content')?.innerText.includes('Delivery version 1')",
    ),
  );
  await browser.evaluate(
    "window.previewEvents=[];addEventListener('message',event=>window.previewEvents.push({origin:event.origin,data:event.data}));document.cookie='operator-secret-canary=private;path=/';",
  );
  assert.equal(
    await browser.evaluate(
      "document.querySelector('iframe').getAttribute('sandbox')",
    ),
    "allow-scripts",
  );
  const before = received.length;
  await browser.evaluate(
    "Array.from(document.querySelectorAll('#customer-content button')).find(item=>item.textContent==='Open isolated preview').click()",
  );
  await browser.waitFor(() =>
    browser.evaluate(
      "window.previewEvents.some(event=>event.data.type==='sandbox-evidence')",
    ),
  );
  const evidence = await browser.evaluate(
    "window.previewEvents.find(event=>event.data.type==='sandbox-evidence')",
  );
  assert.deepEqual(evidence, {
    origin: "null",
    data: {
      type: "sandbox-evidence",
      parentBlocked: true,
      cookieBlocked: true,
      fetchBlocked: true,
    },
  });
  assert.equal(
    await browser.evaluate(
      "window.previewEvents.find(event=>event.data.type==='calculator-result').data.result",
    ),
    "75",
  );
  assert.equal(
    await browser.evaluate("document.body.dataset.compromised===undefined"),
    true,
  );
  assert.equal(
    received.slice(before).filter((item) => item.path === "/v1/delivery")
      .length,
    1,
  );
  async function previewScreenshot(name) {
    // Cross-origin frames outside the current viewport may be omitted by a
    // full-page CDP capture. Paint the real preview before collecting evidence.
    await browser.evaluate(`(async()=>{
      const frame=document.querySelector('#customer-content iframe:not([hidden])');
      if(!frame)throw new Error('The private preview must be open');
      frame.scrollIntoView({block:'center'});
      await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
    })()`);
    await browser.screenshot(name);
  }
  await previewScreenshot("private-preview-desktop");
  await browser.evaluate(
    "Array.from(document.querySelectorAll('#customer-content button')).find(item=>item.textContent==='Download README.md').click()",
  );
  await browser.waitFor(async () => {
    try {
      return (await readdir(join(dir, "downloads"))).includes("README.md");
    } catch {
      return false;
    }
  });
  assert.equal(
    await readFile(join(dir, "downloads", "README.md"), "utf8"),
    "Usage fixture",
  );
  await browser.evaluate(
    "const box=Array.from(document.querySelectorAll('#customer-content details')).find(item=>item.querySelector('summary')?.textContent==='Request a change');box.open=true;box.querySelector('textarea[name=feedback]').value='Improve spacing while retaining scope';box.querySelector('form').requestSubmit();",
  );
  await browser.waitFor(() =>
    received.some((item) => item.path === "/v1/change-request"),
  );
  assert.equal(
    received.find((item) => item.path === "/v1/change-request").body.kind,
    "revision",
  );
  for (const width of [390, 320]) {
    await browser.viewport(width, 844, true);
    assert.equal(
      await browser.evaluate(
        "document.documentElement.scrollWidth<=innerWidth",
      ),
      true,
    );
    await previewScreenshot("private-preview-" + width);
  }
  await browser.evaluate(
    "document.getElementById('new-request').click();const select=document.querySelector('#commerce-request select[name=service]');select.value='interactive-calculator';select.dispatchEvent(new Event('change'));document.querySelector('#commerce-request input[name=email]').value='provided@example.invalid';document.querySelector('#commerce-request input[name=title]').value='Custom pricing';document.querySelector('#commerce-request textarea[name=description]').value='Calculate quantity times unit price';document.querySelector('#commerce-request').requestSubmit();",
  );
  await browser.waitFor(() =>
    received.some((item) => item.path === "/v1/request"),
  );
  const request = received.find((item) => item.path === "/v1/request").body;
  assert.equal(request.scope.category, "calculator");
  assert.equal(request.scope.acceptance_cases[0].expected.total, 50);
  assert.equal(request.scope.inputs.length, 2);
  assert.deepEqual(request.scope.requirements, [
    "Calculate the agreed results",
    "Update results when an input changes",
    "Allow resetting inputs to their starting values",
    "Reject invalid or out-of-range inputs",
    "Work on narrow screens",
    "Support keyboard entry",
  ]);
  await browser.evaluate(
    "document.getElementById('new-request').click();const graphicSelect=document.querySelector('#commerce-request select[name=service]');graphicSelect.value='custom-graphic';graphicSelect.dispatchEvent(new Event('change'));document.querySelector('#commerce-request input[name=title]').value='Summer opening poster';document.querySelector('#commerce-request textarea[name=description]').value='Promote our new storefront';document.querySelector('#commerce-request textarea[name=graphic_text]').value=['Summer','Opening'].join(String.fromCharCode(10));document.querySelector('#commerce-request').requestSubmit();",
  );
  await browser.waitFor(
    () => received.filter((item) => item.path === "/v1/request").length === 2,
  );
  const graphic = received.filter((item) => item.path === "/v1/request")[1]
    .body;
  assert.deepEqual(graphic.scope.required_text, ["Summer", "Opening"]);
  assert.equal(graphic.scope.visual_requirements.length, 2);

  assert.deepEqual(browser.errors, []);
  const report = {
    passed: true,
    synthetic: true,
    providerCalls: false,
    previewOpaqueOrigin: true,
    parentAccessBlocked: true,
    cookiesBlocked: true,
    networkBlocked: true,
    calculatorResult: "75",
    sourceDownload: true,
    revisionRequest: true,
    typedCalculatorScope: true,
    graphicScope: true,
    mobileWidths: [390, 320],
    pageErrors: browser.errors,
  };
  await writeFile(
    join(output, "verification.json"),
    JSON.stringify(report, null, 2) + "\n",
  );
  console.log(JSON.stringify(report));
} finally {
  await browser?.close();
  if (server) await new Promise((resolve) => server.close(resolve));
  await new Promise((resolve) => upstream.close(resolve));
  app?.db.close();
  await rm(dir, {
    recursive: true,
    force: true,
    maxRetries: 5,
    retryDelay: 100,
  });
}
