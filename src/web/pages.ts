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
function layout(title: string, body: string) {
  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>${escape(title)} · WideMind</title><link rel="stylesheet" href="/style.css"></head><body><header><a href="/" class="brand">W<span>•</span> WideMind</a><nav aria-label="Primary"><a href="/">Headquarters</a><a href="/operator">Operator</a></nav></header><main>${body}</main><footer>Persistent agent runtime · Times shown in UTC · Foundation v0.1</footer></body></html>`;
}
export function publicPage(data: PublicWebsiteData) {
  return layout(
    "Headquarters",
    `<p class="eyebrow">AN OPEN-ENDED BUSINESS EXPERIMENT</p><h1>A small beginning.<br>A durable mind.</h1><p class="lead">WideMind’s intended starting operating credit is $5. It will choose useful work, deliver it, and use verified earnings to continue.</p><section class="notice"><h2>Foundation is running</h2><p>The background worker runs separately from this website. Live business, payment and account adapters are not included in this foundation.</p></section><div class="grid"><section><h2>Operating funds</h2><strong>${data.financial ? `${escape(data.financial.spendableMicros)} micro-USD spendable` : "Unverified / unavailable"}</strong><p>${data.financial ? `Accounting mode: ${escape(data.financial.mode)}. As of ${escape(data.financial.asOf)}.` : "No opening grant, customer payment, or balance is assumed."}</p></section><section><h2>Current runs</h2>${data.runs.length ? data.runs.map((run) => `<article><span class="pill">${escape(run.state)}</span> <span>${escape(run.profile)}</span><p><code>${escape(run.runId)}</code></p><p>Updated ${escape(run.updatedAt)}</p>${run.nextWakeAt ? `<p>Scheduled wake ${escape(run.nextWakeAt)}</p>` : ""}</article>`).join("") : "<p>No runs have been created.</p>"}</section></div><section><h2>Available offerings</h2>${data.offerings.length ? data.offerings.map((item) => `<article><h3>${escape(item.title)}</h3><p>${escape(item.description)}</p></article>`).join("") : "<p>Unavailable until a business module and delivery/payment capabilities are configured.</p>"}</section><section><h2>Selected public activity</h2>${data.activity.length ? data.activity.map((item) => `<p><time>${escape(item.at)}</time> ${escape(item.text)}</p>`).join("") : "<p>No curated public activity yet. Private action logs are never used as a public feed.</p>"}</section>`,
  );
}
export function loginPage(configured: boolean, message = "") {
  return layout(
    "Operator sign in",
    `<p class="eyebrow">PRIVATE CONTROL PLANE</p><h1>Operator access</h1><p>Use the server-configured operator password. Sessions expire after eight hours.</p>${message ? `<p role="alert">${escape(message)}</p>` : ""}${configured ? '<form method="post" action="/operator/login"><label>Password<input name="password" type="password" autocomplete="current-password" required maxlength="1024"></label><button>Sign in</button></form>' : '<section class="notice"><h2>Authentication unavailable</h2><p>Configure WIDEMIND_OPERATOR_PASSWORD_HASH on the server. Operator APIs fail closed until then.</p></section>'}`,
  );
}
export function operatorPage(
  data: ReturnType<typeof privateProjection>,
  csrf: string,
  profile: string,
) {
  const token = `<input type="hidden" name="csrf" value="${escape(csrf)}">`;
  return layout(
    "Run control",
    `<p class="eyebrow">AUTHENTICATED OPERATOR · ${escape(profile)} PROFILE</p><h1>Run control</h1><p>The browser never drives the agent loop. Refresh this page to inspect the latest durable state.</p><section class="notice" aria-label="Worker health"><h2>Worker: ${data.runtime.available ? "connected" : "unavailable"}</h2><p>${data.runtime.liveWorkers} live worker(s) · ${data.runtime.dueJobs} due job(s) · ${data.runtime.unresolvedActions} unresolved action(s) · ${data.runtime.pendingEvents} pending event(s).</p><p>Observed ${escape(data.runtime.observedAt)}. Worker status is measured separately from this web process.</p></section><form method="post" action="/operator/runs">${token}<button>Create paused run</button></form><div class="grid">${Object.entries(
      data.modules,
    )
      .map(
        ([id, health]) =>
          `<section><h2>${escape(id)}</h2><p>${health.available ? "Available" : escape(health.reason)}</p></section>`,
      )
      .join(
        "",
      )}</div>${data.runs.map((run) => `<section><h2>Run <code>${escape(run.runId)}</code></h2><span class="pill">${escape(run.state)}</span><p>${escape(run.reason)}</p><p>Next wake: ${escape(run.nextWakeAt ?? "event / operator wake")} · Updated ${escape(run.updatedAt)}</p><div class="controls">${["resume", "pause", "stop", "wake"].map((control) => `<form method="post" action="/operator/runs/${escape(run.runId)}/${control}">${token}<button>${control === "resume" ? "Resume / recover" : escape(control)}</button></form>`).join("")}</div><details><summary>Deliver an operator event (${run.pendingEventCount} pending)</summary><p>Events wake running work but never grant funds or resume paused/stopped runs.</p><form method="post" action="/operator/runs/${escape(run.runId)}/events">${token}<input type="hidden" name="eventId" value="${randomUUID()}"><label>Event kind<input name="kind" value="operator.note" maxlength="200" required></label><label>JSON payload<textarea name="payload" rows="3" required>{}</textarea></label><button>Deliver event</button></form><pre>${escape(JSON.stringify(run.pendingEvents, null, 2))}</pre></details><h3>Actions & recovery</h3>${run.actions.length ? run.actions.map((action) => `<details><summary>${escape(action.action.tool)} · ${escape(action.state)} · ${action.attempts} attempt(s)</summary><p>Action ID <code>${escape(action.id)}</code></p><p>${escape(action.reason ?? "")}</p><pre>${escape(JSON.stringify({ action: action.action, receipt: action.receipt, reservation: action.reservation }, null, 2))}</pre></details>`).join("") : "<p>No actions recorded.</p>"}<details><summary>Operator and runtime events</summary><pre>${escape(JSON.stringify(run.events, null, 2))}</pre></details></section>`).join("")}<form method="post" action="/operator/logout">${token}<button>Sign out</button></form>`,
  );
}
export const css = `:root{color-scheme:dark;font-family:system-ui,-apple-system,sans-serif;background:#101716;color:#e8eee9;font-size:16px}*{box-sizing:border-box}body{margin:0}header,main,footer{max-width:1100px;margin:auto;padding:24px}header{display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid #34453e}a{color:#d4e6dc;text-decoration:none}a:hover{text-decoration:underline}.brand{font-size:22px;font-weight:700}.brand span{color:#b5ef82}nav{display:flex;gap:24px}main{padding-block:58px}h1{font-size:clamp(34px,6vw,66px);line-height:1.08;letter-spacing:-.04em;max-width:760px;margin:14px 0 24px}h2{font-size:21px}h3{font-size:17px}.eyebrow{font-size:12px;letter-spacing:.18em;color:#b5ef82}.lead{font-size:20px;line-height:1.7;max-width:700px;color:#c4d2cb}p{line-height:1.65;overflow-wrap:anywhere}section{background:#18221f;border:1px solid #34453e;border-radius:16px;padding:26px;margin:20px 0;min-width:0}.notice{border-left:4px solid #b5ef82}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,320px),1fr));gap:20px}.grid section{margin:0}.pill{display:inline-block;background:#304d39;color:#d4f7c0;border-radius:30px;padding:5px 12px}article+article{border-top:1px solid #34453e;margin-top:18px;padding-top:18px}code{font-size:13px;overflow-wrap:anywhere}label{display:grid;gap:10px;max-width:430px}input,textarea{max-width:100%;background:#0e1712;border:1px solid #607567;color:inherit;border-radius:8px;padding:14px;font:inherit;min-width:0}button{background:#b5ef82;color:#14200d;border:0;border-radius:8px;padding:12px 18px;font:600 15px system-ui;cursor:pointer;min-height:44px;margin:12px 0}button:hover{background:#d4ffaa}:focus-visible{outline:3px solid #efce85;outline-offset:4px}.controls{display:flex;flex-wrap:wrap;gap:12px}pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:440px;overflow:auto;background:#101716;padding:16px;border-radius:8px}summary{cursor:pointer;padding:14px 0;min-height:44px}footer{color:#b0c2b6;border-top:1px solid #34453e;font-size:13px}@media(max-width:480px){header,main,footer{padding:20px}header{align-items:flex-start;gap:16px}nav{gap:10px;flex-direction:column}section{padding:18px}main{padding-top:36px}}`;
