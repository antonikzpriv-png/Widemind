/** Local synthetic UI fixtures; no provider calls, customer data or funding. */
import assert from "node:assert/strict";
import { mkdtemp, rm, mkdir, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { randomUUID } from "node:crypto";
import { bootstrap } from "../dist/bootstrap.js";
import { Store } from "../dist/runtime/store.js";
import { webServer } from "../dist/web/server.js";
import { browserSession } from "./browser-session.mjs";

const directory = await mkdtemp(join(tmpdir(), "widemind-network-"));
const output = resolve("var/network-browser-smoke");
await mkdir(output, { recursive: true });
const app = await bootstrap({
  WIDEMIND_DATABASE: join(directory, "fixture.sqlite"),
  WIDEMIND_PROFILE: "demo",
});
const store = new Store(app.db);
const server = webServer(app);
let browser;
function action(runId, i, pending = false) {
  const id = randomUUID();
  app.db
    .prepare(
      "INSERT INTO runtime_actions(id,run_id,work_key,state,action_json,receipt_json,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
    )
    .run(
      id,
      runId,
      id,
      pending ? "running" : "completed",
      JSON.stringify({
        tool: i % 3 === 0 ? "ai.text" : "research.collect",
        input: "private-fixture-canary",
      }),
      pending
        ? null
        : JSON.stringify({
            version: 1,
            actionId: id,
            status: "succeeded",
            usage: {
              actualCharge: {
                currency: "USD",
                micros: i === 1 ? "1" : String(i * 1234),
              },
              provisional: i === 2,
            },
          }),
      Date.now() + i,
      Date.now() + i,
    );
  return id;
}
try {
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  browser = await browserSession(directory, output);
  await browser.viewport(1440, 1000);
  await browser.navigate(origin + "/");
  await browser.waitFor(() =>
    browser.evaluate("document.querySelectorAll('.ambient-dot').length>0"),
  );
  assert.equal(
    await browser.evaluate(
      "document.querySelectorAll('[data-connection]').length",
    ),
    0,
  );
  assert.match(
    await browser.evaluate(
      "document.getElementById('network-status').textContent",
    ),
    /Awaiting/,
  );
  await browser.screenshot("empty-desktop", false);

  const runId = store.create("demo");
  store.control(runId, "resume");
  store.workerSeen("synthetic-worker", "demo", 600000);
  for (let i = 0; i < 12; i++) action(runId, i);
  await browser.waitFor(() =>
    browser.evaluate(
      "document.querySelectorAll('[data-connection]').length===12",
    ),
  );
  assert.equal(
    await browser.evaluate("document.querySelectorAll('.network-cost').length"),
    12,
  );
  assert.equal(
    await browser.evaluate(
      "document.querySelectorAll('.network-edge.is-new').length",
    ),
    12,
  );
  const ids = await browser.evaluate(
    "Array.from(document.querySelectorAll('[data-node]'),n=>n.dataset.node)",
  );
  assert.equal(new Set(ids).size, 12);
  assert.match(
    await browser.evaluate(
      "document.getElementById('network-cost-list').textContent",
    ),
    /\$0\.000001/,
  );
  assert.doesNotMatch(
    await browser.evaluate("document.body.textContent"),
    /private-fixture-canary/,
  );
  await browser.evaluate(
    "document.querySelector('[data-node]').focus();document.querySelector('[data-node]').dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}))",
  );
  assert.match(
    await browser.evaluate(
      "document.getElementById('network-detail').textContent",
    ),
    /\$0\.00/,
  );
  assert.equal(
    await browser.evaluate(
      "document.querySelector('[data-node]').getAttribute('aria-pressed')",
    ),
    "true",
  );
  await browser.click("#network-motion");
  assert.equal(
    await browser.evaluate(
      "document.querySelector('[data-thinking-network]').classList.contains('is-still')",
    ),
    true,
  );
  await browser.click("#network-motion");
  await browser.evaluate("scrollTo(0,0)");
  await new Promise((resolve) => setTimeout(resolve, 1600));
  await browser.screenshot("constellation-desktop", false);

  const pendingId = action(runId, 15, true);
  await browser.waitFor(() =>
    browser.evaluate(
      "document.querySelectorAll('[data-connection]').length===13",
    ),
  );
  assert.match(
    await browser.evaluate(
      "document.getElementById('network-cost-list').textContent",
    ),
    /Pending/,
  );
  app.db
    .prepare(
      "UPDATE runtime_actions SET state='completed',receipt_json=? WHERE id=?",
    )
    .run(
      JSON.stringify({
        version: 1,
        actionId: pendingId,
        status: "succeeded",
        usage: {
          actualCharge: { currency: "USD", micros: "42" },
          provisional: false,
        },
      }),
      pendingId,
    );
  await browser.waitFor(() =>
    browser.evaluate(
      "document.getElementById('network-cost-list').textContent.includes('$0.000042')",
    ),
  );
  assert.equal(
    await browser.evaluate(
      "document.querySelectorAll('[data-connection]').length",
    ),
    13,
  );
  await browser.navigate(origin + "/");
  await browser.waitFor(() =>
    browser.evaluate("document.querySelectorAll('[data-node]').length===13"),
  );
  assert.deepEqual(
    (
      await browser.evaluate(
        "Array.from(document.querySelectorAll('[data-node]'),n=>n.dataset.node)",
      )
    ).slice(0, 12),
    ids,
  );
  store.control(runId, "pause");
  await browser.waitFor(() =>
    browser.evaluate(
      "document.getElementById('network-status').textContent==='Paused'",
    ),
  );
  assert.equal(
    await browser.evaluate(
      "document.querySelector('[data-thinking-network]').classList.contains('is-working')",
    ),
    false,
  );

  for (const [width, height, name] of [
    [390, 844, "constellation-phone"],
    [320, 740, "constellation-narrow"],
  ]) {
    await browser.viewport(width, height, true);
    await browser.waitFor(() =>
      browser.evaluate(
        "Math.abs(document.querySelector('#thinking-svg').viewBox.baseVal.width-document.querySelector('#thinking-svg').getBoundingClientRect().width)<1",
      ),
    );
    assert.equal(
      await browser.evaluate(
        "document.documentElement.scrollWidth<=innerWidth",
      ),
      true,
    );
    await browser.screenshot(name, false);
  }
  await browser.reducedMotion(true);
  await browser.waitFor(() =>
    browser.evaluate(
      "document.getElementById('network-motion').getAttribute('aria-pressed')==='true'",
    ),
  );
  assert.equal(
    await browser.evaluate(
      "getComputedStyle(document.querySelector('.core-dot')).animationName",
    ),
    "none",
  );

  await browser.evaluate(
    "window.originalFetch=window.fetch;window.fetch=(url,options)=>String(url)==='/api/public/graph'?Promise.reject(new Error('fixture offline')):window.originalFetch(url,options)",
  );
  await browser.waitFor(() =>
    browser.evaluate(
      "document.getElementById('network-status').textContent==='Activity connection lost'",
    ),
  );
  assert.equal(
    await browser.evaluate("document.querySelectorAll('[data-node]').length"),
    13,
  );
  await browser.evaluate("window.fetch=window.originalFetch");
  await browser.waitFor(() =>
    browser.evaluate(
      "document.getElementById('network-status').textContent==='Paused'",
    ),
  );
  await browser.click(".network-explore");
  await browser.waitFor(() =>
    browser.evaluate("location.hash==='#headquarters-details'"),
  );
  assert.deepEqual(browser.errors, []);
  const report = {
    result: "passed",
    synthetic: true,
    providerCalls: 0,
    checks: [
      "empty state",
      "new action adds exactly one line and dot",
      "exact sub-cent labels",
      "provisional and pending costs",
      "receipt update without duplicate node",
      "stable reload identities",
      "pause state",
      "keyboard inspection",
      "motion controls",
      "reduced motion",
      "desktop/390/320 layouts",
      "connection failure/recovery",
      "public/private separation",
      "headquarters navigation",
    ],
    screenshots: [
      "empty-desktop.png",
      "constellation-desktop.png",
      "constellation-phone.png",
      "constellation-narrow.png",
    ],
    pageErrors: browser.errors,
  };
  await writeFile(
    join(output, "verification.json"),
    JSON.stringify(report, null, 2) + "\n",
  );
  console.log(JSON.stringify(report));
} finally {
  await browser?.close();
  await new Promise((resolve) => server.close(resolve));
  app.db.close();
  await rm(directory, {
    recursive: true,
    force: true,
    maxRetries: 5,
    retryDelay: 100,
  });
}
