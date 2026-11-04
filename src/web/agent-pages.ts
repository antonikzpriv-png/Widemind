import type { FirstHourPlan, StoredProfile } from "../runtime/agent.ts";
import { OFFERINGS, formatUsd } from "../runtime/agent.ts";
import type { SetupGuide } from "../runtime/setup.ts";
import type { Timeline } from "../runtime/timeline.ts";
import { displayUsd, escape, layout } from "./pages.ts";

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
}

const usdInput = (micros: string) => formatUsd(micros).slice(1);

export function startPage(data: StartData, csrf: string) {
  const token = `<input type="hidden" name="csrf" value="${escape(csrf)}">`;
  const { guide, profile } = data;
  const stages = [...new Set(guide.steps.map((step) => step.stage))];
  const steps = stages
    .map((stage) => {
      const items = guide.steps.filter((step) => step.stage === stage);
      const complete = items.every((step) => step.done);
      return `<details${complete ? "" : " open"}><summary><strong>${escape(stage)}</strong> · ${items.filter((step) => step.done).length} of ${items.length} done</summary><div class="row-list">${items
        .map(
          (step) =>
            `<div class="row"><div class="row-main"><strong>${step.done ? "✓" : "○"} ${escape(step.title)}</strong><p class="muted">${escape(step.done ? step.detail : step.todo)}</p>${step.done ? "" : `<p><small>${escape(step.detail)}</small></p>`}</div><div class="row-meta"><span class="pill">${step.done ? "Done" : guide.next?.id === step.id ? "Next" : "To do"}</span></div></div>`,
        )
        .join("")}</div></details>`;
    })
    .join("");
  const offerings = OFFERINGS.map((offering) => {
    const price = profile?.offerings[offering.id];
    return `<div class="row"><div class="row-main"><label><span><input type="checkbox" name="offer_${offering.id}" value="true"${!profile || price ? " checked" : ""}> ${escape(offering.label)}</span></label></div><div class="row-meta"><label>Price (USD)<input name="price_${offering.id}" inputmode="decimal" value="${escape(usdInput(price ?? offering.defaultPriceMicros))}"></label></div></div>`;
  }).join("");
  const agentForm = data.runId
    ? `<form method="post" action="/operator/agent">${token}<label>Name<input name="name" required maxlength="60" value="${escape(profile?.name ?? "")}" placeholder="WideMind"></label><label>Mission<textarea name="mission" required maxlength="500" rows="3" placeholder="Make small, useful graphics and research for small businesses, and pay my own way.">${escape(profile?.mission ?? "")}</textarea></label><h3>What it sells</h3><div class="row-list">${offerings}</div><h3>Spending limits</h3><div class="form-grid"><label>Experiment budget (USD)<input name="experimentBudget" inputmode="decimal" value="${escape(usdInput(profile?.experimentBudgetMicros ?? "1000000"))}"></label><label>Most it may spend on one job (USD)<input name="maxJob" inputmode="decimal" value="${escape(usdInput(profile?.maxJobMicros ?? "250000"))}"></label></div><label><span><input type="checkbox" name="allowOutbound" value="true"${profile?.allowOutbound ? " checked" : ""}> Allow it to contact people first (outreach). Off means it only answers people who come to it.</span></label><button>${profile ? "Save changes" : "Create agent"}</button></form>${data.applied ? `<p class="${data.applied.ok ? "muted" : "warning-text"}">Business settings: ${escape(data.applied.detail)} (${escape(data.applied.at.slice(0, 16).replace("T", " "))} UTC)</p>` : ""}`
    : `<p class="warning-text">${escape(guide.steps[0]!.todo)}</p>`;
  const plan = data.plan
    ? `<p class="muted">Proposed from your settings and the tools that are connected right now; no model was used and nothing runs until you approve.</p><form method="post" action="/operator/agent/plan">${token}<div class="row-list">${data.plan.items
        .map(
          (item) =>
            `<div class="row"><div class="row-main"><label><span><input type="checkbox" name="item:${escape(item.id)}" value="true"${item.available ? " checked" : " disabled"}> <strong>${escape(item.title)}</strong></span></label><p class="muted">${escape(item.detail)}</p>${item.available ? "" : `<p class="warning-text">${escape(item.blocker ?? "Not available yet")}</p>`}</div></div>`,
        )
        .join("")}</div>${
        data.plan.items.find((item) => item.id === "discovery")?.available
          ? `<label>Where should it look? One to five public HTTPS pages where people ask for this kind of work (forums, job boards, request threads)<textarea name="sources" rows="3" placeholder="https://example.com/requests"></textarea></label>`
          : ""
      }<button>Approve and start</button></form>${
        data.decision
          ? `<h3>Last approval · ${escape(data.decision.decidedAt.slice(0, 16).replace("T", " "))} UTC</h3><div class="row-list">${data.decision.results.map((result) => `<div class="row"><div class="row-main"><strong>${escape(result.id)}</strong><p class="muted">${escape(result.detail)}</p></div><div class="row-meta"><span class="pill">${result.ok ? "Done" : "Not done"}</span></div></div>`).join("")}</div>`
          : ""
      }`
    : '<p class="muted">Create the agent first; its first-hour plan appears here.</p>';
  return layout(
    "Start",
    `<p class="eyebrow">AUTHENTICATED OPERATOR · GETTING STARTED</p><h1>${escape(profile?.name ?? "Set up your agent")}</h1><p><a href="/operator">← Run control</a> · <a href="/operator/activity">Activity →</a> · <a href="/operator/pilot">Pilot →</a></p><section class="notice"><h2>${guide.next ? `Next: ${escape(guide.next.title)}` : "Setup complete"}</h2><p>${guide.next ? escape(guide.next.todo) : "Every step passes. The agent is ready for live work."}</p><p class="muted">${guide.done} of ${guide.steps.length} steps done${guide.readyToRehearse && !guide.readyForLive ? " · ready to rehearse in sandbox" : ""}.</p></section><div class="business-grid"><div><section><h2>1. Create your agent</h2>${agentForm}</section><section><h2>2. First hour</h2>${plan}</section></div><section><h2>Setup steps</h2>${steps}</section></div>`,
  );
}

export function activityPage(data: Timeline | null, name: string | null) {
  const head = `<p class="eyebrow">AUTHENTICATED OPERATOR · ACTIVITY</p><h1>${escape(name ?? "Agent")} activity</h1><p><a href="/operator/start">← Getting started</a> · <a href="/operator">Run control</a></p>`;
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
            `<div class="row"><div class="row-main"><strong>${escape(entry.title)}</strong>${entry.detail ? `<p class="muted">${escape(entry.detail)}</p>` : ""}</div><div class="row-meta"><time>${escape(entry.at.slice(0, 16).replace("T", " "))}</time>${entry.costMicros && entry.costMicros !== "0" ? `<p>${escape(displayUsd(entry.costMicros))}</p>` : ""}<p><span class="pill">${escape(entry.kind)}</span></p></div></div>`,
        )
        .join("")
    : '<p class="muted">Nothing yet. Approve the first-hour plan to get it started.</p>';
  return layout(
    "Activity",
    `${head}<section class="notice"><h2>Now: ${escape(states[data.now.state] ?? data.now.state)}</h2><p>${escape(data.now.reason)}</p><p class="muted">${data.now.nextWakeAt ? `Next check ${escape(data.now.nextWakeAt.slice(0, 16).replace("T", " "))} UTC · ` : ""}${data.now.pendingEvents} event(s) waiting · ${data.now.unresolved} action(s) awaiting confirmation · ${escape(displayUsd(data.spentMicros))} spent on tools so far</p></section><section><h2>What it did</h2><div class="row-list">${rows}</div></section>`,
  );
}
