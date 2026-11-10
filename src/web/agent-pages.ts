import type { FirstHourPlan, StoredProfile } from "../runtime/agent.ts";
import { OFFERINGS, formatUsd } from "../runtime/agent.ts";
import type { SetupGuide } from "../runtime/setup.ts";
import type { Timeline } from "../runtime/timeline.ts";
import { displayUsd, escape, layout } from "./pages.ts";
import { feeEstimate, type FeeModel } from "../runtime/growth.ts";
import { firstCustomerCard } from "./growth-pages.ts";

export interface StartData {
  runId: string | null;
  guide: SetupGuide;
  profile: StoredProfile | null;
  applied: { ok: boolean; detail: string; at: string } | null;
  plan: FirstHourPlan | null;
  decision: {
    decidedAt: string;
    results: { id: string; ok: boolean; detail: string }[];
  } | null;
  /** The run is working or waiting for work. */
  running: boolean;
  /** Estimated payment fee, for the "what you keep" note beside each price. */
  fees: FeeModel;
  /** Counts for the first-customer card, once the agent exists. */
  firstCustomer: {
    newLeads: number;
    publishedSamples: number;
    requests: number;
  } | null;
}

const usdInput = (micros: string) => formatUsd(micros).slice(1);
const time = (iso: string) => iso.slice(0, 16).replace("T", " ");
const SUGGESTED_MISSION =
  "Make small, useful graphics and pages for small businesses, deliver them privately, and pay my own way.";

function agentForm(
  token: string,
  profile: StoredProfile | null,
  compact: boolean,
  fees: FeeModel,
) {
  const offerings = OFFERINGS.map((offering) => {
    const price = profile?.offerings[offering.id];
    const checked = profile ? Boolean(price) : offering.id !== "research-brief";
    const current = price ?? offering.defaultPriceMicros;
    const estimate = feeEstimate(current, fees);
    const presets = offering.presets
      .map(
        (preset) =>
          `<option value="${preset.id}">${escape(preset.label)} · ${escape(formatUsd(preset.priceMicros))}</option>`,
      )
      .join("");
    return `<div class="choice"><label><input type="checkbox" name="offer_${offering.id}" value="true"${checked ? " checked" : ""}>${escape(offering.label)}</label><label class="muted">$<input name="price_${offering.id}" inputmode="decimal" aria-label="${escape(offering.label)} price in USD" value="${escape(usdInput(current))}"></label></div><div class="choice-more"><label>Or use a suggested price<select name="preset_${offering.id}" aria-label="${escape(offering.label)} suggested price"><option value="">Keep the price above</option>${presets}</select></label><p class="${estimate.heavy ? "warning-text" : "muted"}">At ${escape(formatUsd(current))} you keep about ${escape(formatUsd(estimate.keepMicros))} after an estimated ${escape(formatUsd(estimate.feeMicros))} payment fee.${estimate.heavy ? " The fee takes more than a fifth of this price." : ""}</p></div>`;
  }).join("");
  // "Leave as it is" keeps an allowance set directly in the business policy.
  const revisions =
    `<option value=""${profile?.includedRevisions === undefined ? " selected" : ""}>Leave as it is</option>` +
    [0, 1, 2, 3]
      .map(
        (count) =>
          `<option value="${count}"${profile?.includedRevisions === count ? " selected" : ""}>${count === 0 ? "None" : count}</option>`,
      )
      .join("");
  const advanced = `<details${compact ? "" : " open"}><summary class="muted">Spending limits and outreach</summary><div class="form-grid"><label>Experiment budget (USD)<input name="experimentBudget" inputmode="decimal" value="${escape(usdInput(profile?.experimentBudgetMicros ?? "1000000"))}"></label><label>Most it may spend on one job (USD)<input name="maxJob" inputmode="decimal" value="${escape(usdInput(profile?.maxJobMicros ?? "250000"))}"></label><label class="span-all">Revisions included in every quote<select name="revisions">${revisions}</select></label></div><label><span><input type="checkbox" name="allowOutbound" value="true"${profile?.allowOutbound ? " checked" : ""}>Let it contact people first. Off means it only answers people who come to it.</span></label></details>`;
  return `<form method="post" action="/operator/agent">${token}<label>Name<input name="name" required maxlength="60" value="${escape(profile?.name ?? "")}" placeholder="For example: Pixel &amp; Proof" autofocus></label><label>What is it for?<textarea name="mission" required maxlength="500" rows="3">${escape(profile?.mission ?? SUGGESTED_MISSION)}</textarea></label><p class="eyebrow">WHAT IT SELLS</p>${offerings}<p class="muted"><small>Customers pay before any work starts. Research briefs need OpenAI connected. Suggested prices are starting points, and the fee is an estimate; check your payment provider's rates.</small></p>${advanced}<button>${profile ? "Save changes" : "Create my agent"}</button></form>`;
}

function welcome(data: StartData, token: string) {
  return layout(
    "Welcome",
    `<div class="welcome"><p class="eyebrow">WELCOME</p><h1>Let's set up your agent</h1><p class="lead">WideMind is an agent that starts with $5, sells small pieces of useful work, and uses what it earns to keep going. Setting it up takes about a minute.</p><ul class="welcome-list"><li><strong>1 · Name it</strong><span>Give it a name and say what it's for. You can change both later.</span></li><li><strong>2 · Choose what it sells</strong><span>Graphics, simple web pages or research, at prices you set.</span></li><li><strong>3 · Try it with test money</strong><span>Add $5 of sandbox credit and place a test order yourself. Nothing real is spent.</span></li><li><strong>4 · Go live when ready</strong><span>A checklist walks you through payments, email, backups and HTTPS.</span></li></ul></div>${
      data.runId
        ? `<div class="gate gate-flat"><section class="card"><div class="card-head"><p class="eyebrow">STEP 1 OF 4</p><h1>Create your agent</h1><p>Sensible defaults are filled in.</p></div>${agentForm(token, null, true, data.fees)}</section></div>`
        : `<section class="notice"><h2>One thing first</h2><p>${escape(data.guide.steps[0]!.todo)}</p></section>`
    }`,
  );
}

export function startPage(data: StartData, csrf: string) {
  const token = `<input type="hidden" name="csrf" value="${escape(csrf)}">`;
  const { guide, profile } = data;
  if (!profile) return welcome(data, token);
  const plan = data.plan;
  const item = (id: string) => plan?.items.find((entry) => entry.id === id);
  const action = (id: string, label: string, fields = "") => {
    const entry = item(id);
    if (!entry) return "";
    if (entry.done) return '<span class="pill ok">Done</span>';
    if (!entry.available) return `<span class="pill">Not yet</span>`;
    return `<form method="post" action="/operator/agent/plan">${token}<input type="hidden" name="item:${id}" value="true">${fields}<button class="small-button">${escape(label)}</button></form>`;
  };
  const card = (
    number: number,
    title: string,
    body: string,
    done: boolean,
    side: string,
  ) =>
    `<div class="step-card${done ? " done" : ""}"><span class="num">${done ? "✓" : number}</span><div class="row-main"><h3>${escape(title)}</h3><p>${body}</p></div><div class="row-meta">${side}</div></div>`;
  const fund = item("fund"),
    start = item("start"),
    discovery = item("discovery");
  const firstOffer = OFFERINGS.find(
    (offering) => profile.offerings[offering.id],
  );
  const steps = [
    card(
      1,
      "Add $5.00 of test credit",
      escape(
        fund?.blocker ??
          "Sandbox money, labelled as test funding. Real funding comes when you go live.",
      ),
      Boolean(fund?.done),
      action("fund", "Add test credit"),
    ),
    card(
      2,
      "Start your agent",
      escape(
        start?.blocker ??
          "It answers requests, sends quotes and delivers paid work. Waiting costs nothing.",
      ),
      Boolean(start?.done) || data.running,
      action("start", "Start"),
    ),
    card(
      3,
      "Try it yourself",
      `Open your public site in a new tab and request ${escape(firstOffer?.phrase ?? "something")}. A quote arrives in seconds; paying needs PayPal sandbox connected (see the checklist).`,
      false,
      '<a class="button secondary small-button" href="/commerce" target="_blank" rel="noopener">Open your site ↗</a>',
    ),
    card(
      4,
      "Watch what it does",
      "Every request, quote, payment and delivery shows up in plain language, with what it cost.",
      false,
      '<a class="button secondary small-button" href="/operator/activity">Activity</a>',
    ),
  ].join("");
  const discoveryCard = discovery
    ? `<details><summary class="muted">Optional: let it look for customers</summary><p class="muted">${escape(discovery.detail)}</p>${
        discovery.available
          ? `<form method="post" action="/operator/agent/plan">${token}<input type="hidden" name="item:discovery" value="true"><label>Pages where people ask for this kind of work (one to five HTTPS links)<textarea name="sources" rows="2" placeholder="https://example.com/requests"></textarea></label><button class="small-button">Start looking</button></form>`
          : `<p class="warning-text">${escape(discovery.blocker ?? "Not available yet")}</p>`
      }</details>`
    : "";
  const failed = data.decision?.results.filter((result) => !result.ok) ?? [];
  const decision = failed.length
    ? `<p class="alert" role="alert">${failed.map((result) => escape(result.detail)).join(" · ")}</p>`
    : "";
  const stages = [...new Set(guide.steps.map((step) => step.stage))];
  const checklist = stages
    .map((stage) => {
      const items = guide.steps.filter((step) => step.stage === stage);
      const complete = items.every((step) => step.done);
      const open =
        !complete && items.some((step) => step.id === guide.next?.id);
      return `<details${open ? " open" : ""}><summary><strong>${escape(stage)}</strong> <span class="muted">· ${items.filter((step) => step.done).length} of ${items.length}</span></summary><div class="row-list">${items
        .map(
          (step) =>
            `<div class="row"><div class="row-main"><strong>${escape(step.title)}</strong><p class="muted">${escape(step.done ? step.detail : step.todo)}</p></div><div class="row-meta"><span class="pill${step.done ? " ok" : guide.next?.id === step.id ? " next" : ""}">${step.done ? "Done" : guide.next?.id === step.id ? "Next" : "To do"}</span></div></div>`,
        )
        .join("")}</div></details>`;
    })
    .join("");
  const percent = Math.round((guide.done / guide.steps.length) * 100);
  return layout(
    "Start",
    `<p class="eyebrow">YOUR AGENT</p><h1>${escape(profile.name)}</h1><p class="lead">${escape(profile.mission)}</p><p><a href="/operator/activity">Activity</a> · <a href="/">Public site</a> · <a href="/operator">Run control</a> · <a href="/operator/pilot">Pilot</a></p>${
      data.applied && !data.applied.ok
        ? `<p class="alert" role="alert">Settings saved, but not active yet: ${escape(data.applied.detail)}</p>`
        : ""
    }<div class="business-grid"><div><section><h2>Your first hour</h2>${decision}<div class="step-cards">${steps}</div>${discoveryCard}</section>${data.firstCustomer ? firstCustomerCard(data.firstCustomer) : ""}<section><details><summary><strong>Edit agent settings</strong></summary>${agentForm(token, profile, true, data.fees)}</details></section></div><section><h2>Before going live</h2><progress class="progress" value="${guide.done}" max="${guide.steps.length}" aria-label="Setup progress">${percent}%</progress><p class="muted">${guide.done} of ${guide.steps.length} steps done${guide.next ? ` · next: <strong>${escape(guide.next.title)}</strong>` : " · ready for live work"}</p>${guide.next ? `<p class="notice">${escape(guide.next.todo)}</p>` : ""}${checklist}</section></div>`,
  );
}

export function activityPage(data: Timeline | null, name: string | null) {
  const head = `<p class="eyebrow">ACTIVITY</p><h1>${escape(name ?? "Agent")}</h1><p><a href="/operator/start">← Getting started</a> · <a href="/operator">Run control</a></p>`;
  if (!data)
    return layout(
      "Activity",
      `${head}<section class="notice"><h2>No agent yet</h2><p>Create the agent on the getting-started page.</p></section>`,
    );
  const states: Record<string, string> = {
    active: "Working",
    waiting: "Waiting for something to happen",
    paused: "Paused",
    exhausted: "Out of credit",
    stopped: "Stopped",
    failed: "Stopped by an error",
  };
  const rows = data.entries.length
    ? data.entries
        .map(
          (entry) =>
            `<div class="row"><div class="row-main"><strong>${escape(entry.title)}</strong>${entry.detail ? `<p class="muted">${escape(entry.detail)}</p>` : ""}</div><div class="row-meta"><time>${escape(time(entry.at))}</time>${entry.costMicros && entry.costMicros !== "0" ? `<p>${escape(displayUsd(entry.costMicros))}</p>` : ""}<p><span class="pill">${escape(entry.kind)}</span></p></div></div>`,
        )
        .join("")
    : '<p class="muted">Nothing yet. Add test credit and start the agent on Getting started.</p>';
  return layout(
    "Activity",
    `${head}<section class="notice"><h2>Now: ${escape(states[data.now.state] ?? data.now.state)}</h2><p>${escape(data.now.reason)}</p><p class="muted">${data.now.nextWakeAt ? `Next check ${escape(time(data.now.nextWakeAt))} UTC · ` : ""}${data.now.pendingEvents} event(s) waiting · ${data.now.unresolved} action(s) awaiting confirmation · ${escape(displayUsd(data.spentMicros))} spent on tools so far</p></section><section><h2>What it did</h2><div class="row-list">${rows}</div></section>`,
  );
}
