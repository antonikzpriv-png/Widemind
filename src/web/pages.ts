import { randomUUID } from "node:crypto";
import type { PublicWebsiteData } from "../contracts/index.ts";
import type { privateProjection } from "./projections.ts";
export const escape = (value: unknown) =>
  String(value).replace(
    /[&<>"']/g,
    (char) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        char
      ]!,
  );
export { displayUsd } from "../money.ts";
import { displayUsd } from "../money.ts";
function usd(value: unknown) {
  try {
    return typeof value === "string" ? displayUsd(value) : "Unavailable";
  } catch {
    return "Unavailable";
  }
}
export function layout(
  title: string,
  body: string,
  options: { script?: string; footer?: string } = {},
) {
  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>${escape(title)} · WideMind</title><link rel="stylesheet" href="/style.css">${options.script ? `<script src="${escape(options.script)}" defer></script>` : ""}</head><body><header><a href="/" class="brand"><span class="mark" aria-hidden="true">W</span>WideMind<small>Agent</small></a><nav aria-label="Primary"><a href="/">Headquarters</a><a href="/commerce">Work with WideMind</a><a href="/pilot">Pilot</a><a href="/operator">Operator</a></nav></header><main>${body}</main><footer>${escape(options.footer ?? "INDEPENDENT WORKER · PRIVATE COMMITMENTS · TIMES IN UTC")}</footer></body></html>`;
}
const OFFERING_NAMES: Record<string, string> = {
  "research-brief": "Sourced research brief",
  "svg-graphic": "SVG graphic",
  "software-starter": "Simple web page",
};
const RUN_STATES: Record<string, string> = {
  active: "Working",
  waiting: "Open for requests",
  paused: "Paused by its owner",
  exhausted: "Out of credit",
  stopped: "Stopped",
  failed: "Stopped by an error",
};
export function publicPage(data: PublicWebsiteData) {
  const run =
    data.runs.find((item) => item.runId === data.financialRunId) ??
    data.runs[0];
  const money = data.financial;
  const stat = (label: string, value: string, note: string) =>
    `<section><div class="stat-label">${escape(label)}</div><div class="stat-value">${escape(value)}</div><p class="muted"><small>${escape(note)}</small></p></section>`;
  const accounting = money
    ? `<details><summary class="muted">Full accounting</summary><dl>${(
        [
          ["Opening credit", money.openingCreditMicros],
          ["Posted balance", money.postedMicros],
          ["Reserved for running work", money.reservedMicros],
          ["Customer and refund holds", money.holdsMicros],
          ["Customer receipts", money.customerReceiptsMicros],
          ["Earned revenue", money.earnedRevenueMicros],
          ["Operating charges", money.operatingChargesMicros],
          ["Processor fees", money.processorFeesMicros],
          ["Refunds", money.refundsMicros],
          ["Chargebacks", money.chargebacksMicros],
          ["Payment adjustments", money.paymentAdjustmentsMicros],
          ["Corrections", money.correctionsMicros],
          ["Reconciliation adjustments", money.adjustmentsMicros],
          ["Owner funding", money.ownerFundingMicros],
          ["Subsidies (not cash)", money.subsidiesMicros],
        ] as [string, string | undefined][]
      )
        .filter(([, value]) => value !== undefined)
        .map(
          ([label, value]) => `<dt>${escape(label)}</dt><dd>${usd(value)}</dd>`,
        )
        .join("")}</dl></details>`
    : "";
  return layout(
    "Headquarters",
    `<p class="eyebrow">AN AUTONOMOUS AGENT THAT STARTED WITH $5</p>${
      data.agent
        ? `<h1>${escape(data.agent.name)}</h1><p class="lead">${escape(data.agent.mission)}</p><div class="hero-actions"><a class="button" href="/commerce">Request work</a><a class="link-arrow" href="/pilot">How it's doing</a></div>`
        : `<h1>A small beginning.<br>A durable mind.</h1><p class="lead">WideMind starts with $5 of credit, chooses useful work, delivers it, and uses verified earnings to keep going.</p><section class="notice"><h2>This agent is still being set up</h2><p>Its owner hasn't finished setting it up yet. Check back soon to see what it offers.</p></section>`
    }<div class="stats">${stat(
      "Available",
      money ? usd(money.spendableMicros) : "Not funded",
      money
        ? money.mode === "live"
          ? "Live money"
          : "Test money (sandbox)"
        : "No credit added yet",
    )}${stat("Paid by customers", money ? usd(money.customerReceiptsMicros) : "$0.00", "Verified payments only")}${stat(
      "Spent on work",
      money ? usd(money.operatingChargesMicros) : "$0.00",
      "Tools and models",
    )}${stat("Status", run ? (RUN_STATES[run.state] ?? run.state) : "Not started", run ? `Updated ${run.updatedAt.slice(0, 10)}` : "")}</div>${accounting}<section><h2>What it offers</h2>${
      data.offerings.length
        ? `<div class="row-list">${data.offerings
            .map(
              (item) =>
                `<div class="row"><div class="row-main"><strong>${escape(OFFERING_NAMES[item.id] ?? item.title)}</strong><p class="muted">${escape(item.description)}</p></div><div class="row-meta"><a class="button secondary small-button" href="/commerce">Request</a></div></div>`,
            )
            .join("")}</div>`
        : '<p class="muted">Nothing is on offer yet. Offerings appear once the agent has the tools to deliver them.</p>'
    }</section><section><h2>Recent work</h2>${
      data.activity.length
        ? data.activity
            .map(
              (item) =>
                `<p><time class="muted">${escape(item.at.slice(0, 10))}</time> ${escape(item.text)}</p>`,
            )
            .join("")
        : '<p class="muted">No completed work yet. Only work chosen for publication appears here; customer details never do.</p>'
    }</section>`,
  );
}
export function loginPage(configured: boolean, message = "") {
  return layout(
    "Sign in",
    `<div class="gate"><section class="card"><div class="card-head"><p class="eyebrow">OWNER SIGN-IN</p><h1>Welcome back</h1><p>Sign in to set up and run your agent.</p></div>${message ? `<p class="alert" role="alert">${escape(message)}</p>` : ""}${
      configured
        ? '<form method="post" action="/operator/login"><label>Password<input name="password" type="password" autocomplete="current-password" required maxlength="1024" autofocus></label><button>Sign in</button></form><p class="card-note"><span>●</span>Your password is checked on this server only. Sessions last eight hours.</p>'
        : '<p class="card-note"><span>●</span><strong>Authentication unavailable:</strong> no owner password is set yet, so sign-in is closed. Run <code>npm run quickstart</code>, which creates one and prints it once, or set WIDEMIND_OPERATOR_PASSWORD_HASH (npm run operator:hash).</p>'
    }</section></div>`,
  );
}
export function operatorPage(
  data: ReturnType<typeof privateProjection>,
  csrf: string,
  profile: string,
  modulePages: readonly string[] = [],
) {
  const token = `<input type="hidden" name="csrf" value="${escape(csrf)}">`;
  return layout(
    "Run control",
    `<p class="eyebrow">AUTHENTICATED OPERATOR · ${escape(profile)} PROFILE</p><h1>Run control</h1><p><a href="/operator/start">Getting started →</a> · <a href="/operator/activity">Activity →</a> · <a href="/operator/integrations">Connected services, business policy and payments →</a> · <a href="/operator/pilot">Pilot →</a></p><p>The browser never drives the agent loop. Refresh this page to inspect the latest durable state.</p><section class="notice" aria-label="Worker health"><h2>Worker: ${data.runtime.available ? "connected" : "unavailable"}</h2><p>${data.runtime.liveWorkers} live worker(s) · ${data.runtime.dueJobs} due job(s) · ${data.runtime.unresolvedActions} unresolved action(s) · ${data.runtime.pendingEvents} pending event(s).</p><p>Observed ${escape(data.runtime.observedAt)}. Worker status is measured separately from this web process.</p></section><form method="post" action="/operator/runs">${token}<button>Create paused run</button></form><div class="grid">${Object.entries(
      data.modules,
    )
      .map(
        ([id, health]) =>
          `<section><h2>${escape(id)}</h2><p>${health.available ? "Available" : escape(health.reason)}</p></section>`,
      )
      .join(
        "",
      )}</div>${data.runs.map((run) => `<section><h2>Run <code>${escape(run.runId)}</code></h2><span class="pill">${escape(run.state)}</span><p>${escape(run.reason)}</p><p>Next wake: ${escape(run.nextWakeAt ?? "event / operator wake")} · Updated ${escape(run.updatedAt)}</p><div class="controls">${["resume", "pause", "stop", "wake"].map((control) => `<form method="post" action="/operator/runs/${escape(run.runId)}/${control}">${token}<button>${control === "resume" ? "Resume / recover" : escape(control)}</button></form>`).join("")}${modulePages.map((path) => `<a class="button secondary" href="${escape(path)}?runId=${escape(encodeURIComponent(run.runId))}">${escape(path.split("/").slice(-2).join(" "))}</a>`).join("")}</div><details><summary>Deliver an operator event (${run.pendingEventCount} pending)</summary><p>Events wake running work but never grant funds or resume paused/stopped runs.</p><form method="post" action="/operator/runs/${escape(run.runId)}/events">${token}<input type="hidden" name="eventId" value="${randomUUID()}"><label>Event kind<input name="kind" value="operator.note" maxlength="200" required></label><label>JSON payload<textarea name="payload" rows="3" required>{}</textarea></label><button>Deliver event</button></form><pre>${escape(JSON.stringify(run.pendingEvents, null, 2))}</pre></details><h3>Actions & recovery</h3>${run.actions.length ? run.actions.map((action) => `<details><summary>${escape(action.action.tool)} · ${escape(action.state)} · ${action.attempts} attempt(s)</summary><p>Action ID <code>${escape(action.id)}</code></p><p>${escape(action.reason ?? "")}</p><pre>${escape(JSON.stringify({ action: action.action, receipt: action.receipt, reservation: action.reservation }, null, 2))}</pre></details>`).join("") : "<p>No actions recorded.</p>"}<details><summary>Operator and runtime events</summary><pre>${escape(JSON.stringify(run.events, null, 2))}</pre></details></section>`).join("")}<form method="post" action="/operator/logout">${token}<button>Sign out</button></form>`,
  );
}
export function operatorFormErrorPage(body: unknown, returnTo: string) {
  const error =
    body &&
    typeof body === "object" &&
    "error" in body &&
    typeof body.error === "string"
      ? body.error.slice(0, 2000)
      : "The operation did not complete. Inspect the current records before trying again.";
  return layout(
    "Operation needs attention",
    `<p class="eyebrow">PRIVATE OPERATOR</p><h1>Operation needs attention.</h1><section class="notice" role="alert"><p>${escape(error)}</p><p>Refresh the recorded state before retrying an external action.</p><a class="button" href="${escape(returnTo)}">Return to the console</a></section>`,
  );
}

export const css = `@font-face{font-family:"WideMind Geist";src:url(/fonts/geist-variable.woff2) format("woff2");font-style:normal;font-weight:100 900;font-display:swap}
:root{color-scheme:dark;--bg:#000;--surface:#080808;--raised:#111;--hover:#161616;--sel:#202020;--hair:#242424;--rule:#1a1a1a;--fg:#ededed;--muted:#999;--faint:#777;--accent:#87a891;--accent-strong:#adc5b3;--ok:#8fc19d;--warn:#c7b887;--del:#e5a099;--sans:"WideMind Geist",Geist,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;--mono:ui-monospace,"Cascadia Code","SF Mono","Roboto Mono",Menlo,monospace;font-family:var(--sans);background:var(--bg);color:var(--fg);font-size:14px;font-synthesis:none;-webkit-font-smoothing:antialiased}
*{box-sizing:border-box}body{margin:0;background:var(--bg)}a{color:inherit;text-decoration:none}a:hover{text-decoration:underline}
::selection{background:rgba(233,233,230,.14)}
header,main,footer{width:min(1200px,100%);margin:auto;padding:22px 32px}
header{display:flex;align-items:center;justify-content:space-between;gap:24px;border-bottom:1px solid var(--hair);min-height:75px}
.brand{display:flex;align-items:center;gap:10px;font-size:20px;font-weight:500;letter-spacing:-.8px;white-space:nowrap}.brand span{color:var(--muted)}.brand small{padding-left:10px;border-left:1px solid var(--hair);color:var(--muted);font-size:12px;font-weight:400;letter-spacing:0}
.mark{display:inline-grid;place-items:center;width:27px;height:27px;border-radius:8px;border:1px solid var(--hair);background:var(--raised);font-size:13px;font-weight:650;letter-spacing:-.5px}
nav{display:flex;gap:4px;flex-wrap:wrap}nav a{padding:8px 13px;border-radius:999px;color:var(--muted);font-size:13px}nav a:hover,nav a[aria-current=page]{background:var(--hover);color:var(--fg);text-decoration:none}
main{padding-block:44px 80px}
h1{font-size:clamp(32px,5vw,58px);font-weight:620;line-height:1.05;letter-spacing:-.045em;max-width:900px;margin:12px 0 18px}
h2{font-size:17px;font-weight:600;letter-spacing:-.02em;margin-top:0}h3{font-size:14px;font-weight:600;letter-spacing:-.01em}
.eyebrow{margin:0;font:600 10px var(--mono);letter-spacing:.16em;color:var(--accent);text-transform:uppercase}
.lead{font-size:17px;line-height:1.65;max-width:660px;color:#b9bab6}
p{line-height:1.6;overflow-wrap:anywhere}.muted,small{color:var(--muted)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,280px),1fr));gap:14px}.grid section{margin:0}
section{background:var(--surface);border:1px solid var(--hair);border-radius:16px;padding:22px 24px;margin:18px 0;min-width:0}
.notice{background:var(--raised);border-color:#2e2e2e}p.notice{padding:12px 14px;border:1px solid #2e2e2e;border-radius:10px;font-size:13px}
.pill{display:inline-flex;align-items:center;gap:6px;border:1px solid #313238;background:var(--raised);color:#c6c6c6;border-radius:999px;padding:4px 10px;font-size:11px;white-space:nowrap}.pill::before{content:"";width:5px;height:5px;background:var(--faint);border-radius:50%}
.pill.ok{border-color:rgba(130,178,144,.35);color:var(--ok);background:rgba(78,122,90,.1)}.pill.ok::before{background:var(--ok)}
.pill.next{border-color:var(--fg);color:var(--fg)}.pill.next::before{background:var(--fg)}
article+article{border-top:1px solid var(--hair);margin-top:18px;padding-top:18px}
code{font-family:var(--mono);font-size:12px;overflow-wrap:anywhere}
label{display:grid;gap:6px;max-width:100%;font-size:12px;font-weight:500;color:#b9bab6;margin:12px 0}
input,textarea,select{width:100%;max-width:100%;background:#15161a;border:1px solid #35373d;color:#f0f0ed;border-radius:8px;padding:10px 12px;min-height:38px;font:13px var(--sans);min-width:0;outline:none}
input:focus,textarea:focus,select:focus{border-color:#7d9d86;box-shadow:0 0 0 2px rgba(125,157,134,.12)}
textarea{resize:vertical;line-height:1.5}input::placeholder,textarea::placeholder{color:#6f726d}
input[type=checkbox]{width:auto;min-height:auto;margin:0 8px 0 0;accent-color:var(--accent-strong)}
button,.button{display:inline-flex;justify-content:center;align-items:center;gap:8px;background:var(--fg);color:var(--bg);border:1px solid var(--fg);border-radius:999px;padding:10px 20px;font:500 13px var(--sans);cursor:pointer;min-height:42px;margin:8px 0;line-height:1.3;text-decoration:none}
button:hover,.button:hover{filter:brightness(.9);text-decoration:none}
.secondary{background:transparent;color:var(--fg);border-color:var(--hair)}.secondary:hover{background:var(--hover);filter:none}
.link-arrow{color:var(--fg);font-size:13px}.link-arrow::after{content:" ↗";color:var(--muted)}
button[disabled]{opacity:.4;cursor:not-allowed}:focus-visible{outline:2px solid var(--accent);outline-offset:3px;border-radius:4px}
.controls,.toolbar{display:flex;flex-wrap:wrap;align-items:center;gap:12px}.controls form{margin:0}
pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:420px;overflow:auto;background:var(--raised);padding:16px;border-radius:10px;line-height:1.6;font:12px var(--mono)}
dl{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:8px}dt,dd{margin:0}dd{font-variant-numeric:tabular-nums}
summary{cursor:pointer;padding:12px 0;min-height:44px}
footer{color:var(--faint);border-top:1px solid var(--hair);font:10px var(--mono);letter-spacing:.06em}
.hero{padding:40px 0}.hero h1{font-size:clamp(44px,7vw,80px)}.hero-actions{display:flex;align-items:center;gap:18px;flex-wrap:wrap;margin:8px 0 22px}
.stat-value{font-size:28px;font-weight:500;letter-spacing:-.04em;line-height:1.2;margin:10px 0}.stat-label{font:600 9px var(--mono);color:var(--faint);text-transform:uppercase;letter-spacing:.12em}
.business-top{display:flex;justify-content:space-between;gap:18px;align-items:flex-start;flex-wrap:wrap}.business-top h1{font-size:34px;margin:8px 0 12px}
.business-nav{border-bottom:1px solid var(--hair);padding:10px 0 18px;margin:12px 0 24px;overflow-x:auto;flex-wrap:nowrap;max-width:100%}.business-nav a{white-space:nowrap}
.business-grid{display:grid;grid-template-columns:minmax(0,1.6fr) minmax(280px,1fr);gap:18px}.business-grid>section,.business-grid>div>section:first-child{margin-top:0}
.record-head{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}.record-head h3{margin:0}
.row-list{display:grid;gap:0}.row{display:flex;justify-content:space-between;align-items:flex-start;gap:18px;padding:14px 0;border-bottom:1px solid var(--rule)}.row:last-child{border-bottom:0}.row p{margin:4px 0;font-size:12px}.row-main{min-width:0}.row-meta{font-size:11px;color:var(--muted);text-align:right;flex:0 0 auto}
.empty{padding:24px 0;color:var(--muted)}
.form-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:0 16px}.form-grid .span-all{grid-column:1/-1}
.stats{margin-bottom:18px;display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}.stats section{padding:18px;margin:0}
.table-wrap{max-width:100%;overflow:auto}table{width:100%;border-collapse:collapse;text-align:left;font-size:12px}th,td{padding:12px;border-bottom:1px solid var(--rule);vertical-align:top;overflow-wrap:anywhere}th{font:600 9px var(--mono);text-transform:uppercase;letter-spacing:.1em;color:var(--faint)}td{min-width:90px}td small{display:block;line-height:1.5}
.pagination{display:flex;justify-content:space-between;gap:12px;align-items:center;margin-top:20px;font-size:12px;color:var(--muted)}
.facts{display:grid;grid-template-columns:minmax(120px,1fr) minmax(0,1.5fr);gap:10px;font-size:12px}.facts dt{color:var(--muted)}.facts dd{margin:0;text-align:right;overflow-wrap:anywhere}
.warning-text{color:var(--warn)}.ok-text{color:var(--ok)}.keyline{border-top:1px solid var(--hair);margin-top:20px;padding-top:16px}
.inline-form{display:flex;gap:10px;align-items:flex-end;flex-wrap:wrap}.inline-form label{min-width:220px;flex:1}
.scope-summary{font-size:12px;color:#aaa;line-height:1.6}.badge-row{display:flex;gap:8px;flex-wrap:wrap}
.section-heading{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:16px}.section-heading h2{margin:0}
.small-button{min-height:34px;padding:6px 13px;font-size:12px}
.gate{position:relative;isolation:isolate;display:grid;place-items:center;min-height:calc(100vh - 200px);padding:24px 0}
.gate::before{content:"";position:absolute;z-index:-1;inset:0;opacity:.35;background-image:radial-gradient(circle,rgba(255,255,255,.38) 0 1px,transparent 1.4px);background-size:47px 47px;mask-image:linear-gradient(to bottom,black,transparent 80%)}
.card{width:min(400px,100%);padding:28px 30px 25px;border:1px solid rgba(255,255,255,.1);border-radius:16px;background:rgba(24,25,30,.94);box-shadow:0 26px 85px rgba(0,0,0,.42);margin:0}
.card-head{text-align:center}.card-head h1{font-size:22px;font-weight:620;letter-spacing:-.02em;margin:10px 0 7px}.card-head p{margin:0;color:#999b97;font-size:12px;line-height:1.5}
.card button{width:100%;background:var(--accent-strong);border-color:#b9cbbc;color:#142018;font-weight:650;border-radius:8px}
.card-note{display:flex;gap:8px;margin:19px 0 0;padding-top:15px;border-top:1px solid rgba(255,255,255,.07);color:#8a8d88;font-size:11px;line-height:1.5}.card-note span{color:var(--accent)}
.alert{margin:12px 0 0;padding:9px 11px;border:1px solid rgba(207,106,97,.35);border-radius:7px;color:var(--del);background:rgba(120,38,32,.11);font-size:12px}
.welcome{text-align:center;padding:36px 0 8px}.welcome h1{margin-inline:auto;font-size:clamp(38px,6vw,64px);font-weight:680;letter-spacing:-.05em}.welcome .lead{margin:0 auto}
.welcome-list{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:22px 48px;max-width:720px;margin:30px auto;padding:26px 0;border-block:1px solid var(--hair);text-align:left;list-style:none}
.welcome-list li{position:relative;padding-left:18px}.welcome-list li::before{content:"";position:absolute;left:0;top:8px;width:5px;height:5px;border-radius:50%;background:var(--muted)}.welcome-list strong{display:block;font-size:14px;font-weight:600}.welcome-list span{display:block;margin-top:4px;color:#a8a9a5;font-size:13px;line-height:1.5}
.welcome .hero-actions{justify-content:center}
.progress{display:block;width:100%;height:5px;margin:12px 0 6px;border:0;border-radius:99px;background:var(--hair);overflow:hidden;appearance:none}.progress::-webkit-progress-bar{background:var(--hair);border-radius:99px}.progress::-webkit-progress-value{background:var(--accent-strong);border-radius:99px}.progress::-moz-progress-bar{background:var(--accent-strong);border-radius:99px}.gate-flat{min-height:0;padding-top:0}.gate-flat::before{display:none}
.step-cards{display:grid;gap:10px;margin:14px 0}.step-card{display:flex;gap:14px;align-items:flex-start;padding:14px 16px;border:1px solid var(--hair);border-radius:12px;background:var(--raised)}.step-card.done{opacity:.72}.step-card .num{flex:none;display:grid;place-items:center;width:26px;height:26px;border-radius:50%;border:1px solid var(--hair);font:600 11px var(--mono);color:var(--muted)}.step-card.done .num{border-color:rgba(130,178,144,.4);color:var(--ok)}.step-card h3{margin:2px 0 4px}.step-card p{margin:0;font-size:12px;color:var(--muted)}.step-card form{margin:0}
.choice{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:12px 14px;border:1px solid var(--hair);border-radius:10px;margin:8px 0;background:var(--raised)}.choice label{margin:0;display:flex;align-items:center;color:var(--fg);font-size:13px}.choice input[name^=price]{width:96px;text-align:right}
@media(max-width:900px){.stats{grid-template-columns:repeat(2,minmax(0,1fr))}.business-grid{grid-template-columns:minmax(0,1fr)}header,main,footer{padding-inline:24px}}
@media(max-width:520px){.business-nav{flex-wrap:wrap;overflow:visible}.business-nav a{min-height:44px;display:inline-flex;align-items:center}header,main,footer{padding-inline:16px}header{gap:10px;min-height:64px;flex-wrap:wrap}.brand{font-size:18px}.brand small{display:none}header nav{gap:0}header nav a{font-size:12px;padding:8px}main{padding-block:26px 52px}.hero h1{font-size:44px}.lead{font-size:15px}section{padding:18px}.business-top h1{font-size:30px}.form-grid,.welcome-list{grid-template-columns:minmax(0,1fr)}.stats{gap:8px}.stats section{padding:14px}.stat-value{font-size:22px}.row{gap:10px}.row-meta{font-size:10px}.facts{grid-template-columns:minmax(0,1fr)}.facts dd{text-align:left}.pagination{align-items:flex-start;flex-direction:column}.inline-form label{min-width:100%}.card{padding:24px 20px}.choice{flex-wrap:wrap}}`;
