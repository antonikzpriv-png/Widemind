/** Private discovery evidence and bounded experiment controls. No provider calls. */
import { randomUUID } from "node:crypto";
import { displayUsd, escape, layout } from "../../web/pages.ts";

export const discoveryPagePath = "/operator/modules/business/discovery";
export const discoveryCommandPath = "/api/modules/business/discovery-command";
type Row = Record<string, unknown>;
const row = (value: unknown): Row =>
  value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Row)
    : {};
const rows = (value: unknown): Row[] =>
  Array.isArray(value) ? value.map(row) : [];
const string = (value: unknown, fallback = "Unavailable") =>
  typeof value === "string" && value.length ? value : fallback;
const pretty = (value: unknown) =>
  escape(JSON.stringify(value ?? null, null, 2));
const badge = (value: unknown) =>
  `<span class="pill">${escape(string(value).replaceAll("_", " "))}</span>`;
const field = (name: string, value: string) =>
  `<input type="hidden" name="${escape(name)}" value="${escape(value)}">`;
function money(value: unknown) {
  try {
    if (typeof value === "number" && Number.isSafeInteger(value))
      return displayUsd(String(value));
    return displayUsd(string(value, ""));
  } catch {
    return "Unverified";
  }
}
function date(value: unknown) {
  const millis =
    typeof value === "number" && Number.isFinite(value)
      ? value * 1000
      : typeof value === "string"
        ? Date.parse(value)
        : NaN;
  return Number.isFinite(millis) && Math.abs(millis) < 8.64e15
    ? new Date(millis).toISOString()
    : "Not scheduled";
}
function sourceLink(value: unknown) {
  if (typeof value !== "string" || value.length > 4096) return "";
  try {
    const url = new URL(value);
    if (
      url.protocol !== "https:" ||
      url.username ||
      url.password ||
      /[\u0000-\u0020\u007f]/.test(value)
    )
      return "";
    return `<a href="${escape(url.href)}" rel="noreferrer noopener" target="_blank">${escape(value)}</a>`;
  } catch {
    return "";
  }
}
function form(runId: string, csrf: string, command: string, body: string) {
  return `<form method="post" action="${discoveryCommandPath}">${field("csrf", csrf)}${field("runId", runId)}${field("requestId", randomUUID())}${field("command", command)}${body}</form>`;
}
function recordDetails(item: unknown, label = "Stored evidence and state") {
  return `<details><summary>${escape(label)}</summary><pre>${pretty(item)}</pre></details>`;
}
function evidence(value: unknown) {
  const entries = Array.isArray(value) ? value.map(row) : [row(value)];
  return entries
    .map(
      (item) =>
        `<blockquote><p>${escape(string(item.excerpt, string(item.text, string(item.observed_need, "Evidence is stored below."))))}</p><p>${sourceLink(item.url ?? item.source_url)}<br><small>Retrieved ${escape(date(item.retrieved_at ?? item.observed_at))}</small></p></blockquote>`,
    )
    .join("");
}
function outcomeFacts(item: Row) {
  const document = row(item.document);
  const count = (value: unknown) =>
    typeof value === "number" && Number.isSafeInteger(value) && value >= 0
      ? String(value)
      : "Unverified";
  if (item.kind === "research_cost" || item.kind === "prototype_cost")
    return `<p>Recorded cost ${money(document.actual_micro_usd)} · ${document.costs_final === true ? "Final accounting observation" : "Costs remain uncertain"}</p>`;
  return `<dl class="facts"><dt>Responses</dt><dd>${count(document.responses)}</dd><dt>Conversions</dt><dd>${count(document.conversions ?? (typeof document.conversion === "boolean" ? Number(document.conversion) : undefined))}</dd><dt>Delivery costs</dt><dd>${money(document.delivery_cost_micro_usd)}</dd><dt>Refunds</dt><dd>${money(document.refunds_micro_usd)}</dd><dt>Revisions</dt><dd>${count(document.revisions)}</dd><dt>Measured contribution</dt><dd>${money(document.measured_contribution_micro_usd)}</dd><dt>Cost finality</dt><dd>${document.costs_final === true ? "Final" : "Uncertain / unavailable"}</dd></dl>`;
}
function recoveryForms(item: Row, runId: string, csrf: string) {
  return rows(item.effects)
    .filter((effect) =>
      ["running", "unknown"].includes(string(effect.state, "")),
    )
    .map(
      (effect) =>
        `<div class="keyline"><p><code>${escape(string(effect.id))}</code> · ${escape(string(effect.action))} · ${badge(effect.state)}</p><p class="muted">Inspect the original provider outcome before resuming. This schedules observational reconciliation and preserves its reservation.</p>${form(runId, csrf, "reconcile", field("actionId", string(effect.id, "")) + '<button class="secondary">Reconcile original effect</button>')}</div>`,
    )
    .join("");
}

/** All content originates in private business storage, not a public activity feed. */
export function renderDiscoveryPage(
  value: unknown,
  csrf: string,
  selectedRun: string,
  artifact?: unknown,
) {
  const data = row(value),
    config = row(data.config),
    totals = row(data.totals),
    discoveries = rows(data.discoveries),
    attempts = rows(data.attempts),
    experiments = rows(data.experiments),
    outcomes = rows(data.outcomes),
    effects = rows(data.effects),
    markets = rows(config.markets),
    marketStates = rows(data.markets),
    observations = rows(data.observations),
    upcoming = rows(data.upcoming);
  const mode = string(data.mode, "Unconfigured"),
    fixture = mode === "test" || mode === "demo";
  const empty = (text: string) => `<p class="muted">${escape(text)}</p>`;
  const heading = `<div class="business-top"><div><p class="eyebrow">PRIVATE OPERATOR · INTEGRATED PAYPAL BUSINESS</p><h1>Discover worthwhile work.</h1><p class="muted">Evidence, bounded experiments and measured outcomes. The independent worker owns scheduled execution.</p></div><a class="button secondary" href="/operator">Run control</a></div><form class="inline-form" method="get" action="${discoveryPagePath}"><label>Runtime run ID<input name="runId" maxlength="200" value="${escape(selectedRun)}" required></label><button class="secondary">Open run</button></form>`;
  if (!selectedRun)
    return layout(
      "Discovery and experiments",
      heading +
        "<section><h2>Select a configured run</h2><p>Create or select a foundation run in Run control. No provider work runs when this page opens.</p></section>",
    );
  const summary = `<section class="notice"><h2>${fixture ? "Fixture / test mode" : mode === "live" ? "Live configuration" : "Configuration unavailable"}</h2><p>${fixture ? "These records belong to an explicitly identified test or demo run. They are not verified real opportunities, customers, payments or earnings." : "Source observations are evidence of page content. Proposed demand, customer intent and likely sales remain uncertain until independently verified."}</p><p>Run ${escape(selectedRun)} · ${badge(data.runtime_state)} · Mode ${escape(mode)}</p><p>Discovery ${config.enabled === true ? "enabled" : "disabled"} · Lifetime allocated ceiling ${money(totals.allocated_micro_usd)} of ${money(totals.allowance_micro_usd ?? config.allowance_micro_usd)}. Allocation is a spending limit, not an actual charge.</p><p class="muted">Waiting and status reads use no model calls. Discovery does not expand outreach permissions or override opt-outs.</p></section>`;
  const marketSection = `<section id="markets"><h2>Configured markets</h2>${markets.length ? markets.map((item) => `<article><div class="record-head"><h3>${escape(string(item.label, string(item.id)))}</h3>${badge(item.kind)}</div><p>${escape(string(item.proposed_offering))}</p><p>${badge(marketStates.find((saved) => saved.market_id === item.id)?.state ?? "configured")} · Next research ${escape(date(marketStates.find((saved) => saved.market_id === item.id)?.next_due_at))}</p><p>Research ceiling ${money(item.research_allowance_micro_usd)} · Interval ${escape(String(item.interval_seconds ?? "Unconfigured"))} seconds · Cooldown ${escape(String(item.cooldown_seconds ?? "Unconfigured"))} seconds</p><p>${(Array.isArray(item.source_urls) ? item.source_urls : []).map(sourceLink).filter(Boolean).join("<br>")}</p>${recordDetails(item, "Need terms, capabilities and market limits")}${form(selectedRun, csrf, "wake", field("marketId", string(item.id, "")) + '<button class="secondary">Queue bounded research</button>')}</article>`).join("") : empty("No markets configured. Add actual source URLs and need terms in the owner configuration below.")}</section>`;
  const discoverySection = `<section id="discoveries"><h2>Evidence-backed discoveries</h2>${discoveries.length ? discoveries.map((item) => `<article><div class="record-head"><h3>${escape(string(item.observed_need, "Observed need"))}</h3>${badge(item.kind)}</div><p>${escape(string(item.proposed_offering))}</p><p class="muted">Market ${escape(string(item.market_id))} · Opportunity <code>${escape(string(item.opportunity_id))}</code></p>${evidence(item.evidence)}<dl class="facts"><dt>Service capability</dt><dd>${row(item.capabilities).service_available === true ? "Available" : "Unavailable / unverified"}</dd><dt>Evidence fresh until</dt><dd>${escape(date(item.expires_at))}</dd><dt>Estimated cost ceiling</dt><dd>${money(item.estimated_cost_micro_usd ?? row(item.cost).maximum_micro_usd)}</dd><dt>Uncertainty</dt><dd>${escape(typeof item.uncertainty === "string" ? item.uncertainty : JSON.stringify(item.uncertainty ?? "Unassessed"))}</dd><dt>Next action</dt><dd>${escape(string(item.next_action))}</dd></dl>${recordDetails(item, "Source hashes, available capabilities and assessment")}${item.kind === "experiment" && !item.experiment_id && !experiments.some((experiment) => experiment.opportunity_id === item.opportunity_id) ? form(selectedRun, csrf, "start", field("opportunityId", string(item.opportunity_id, "")) + `<label>Bounded experiment specification, JSON<textarea name="specJson" rows="9" maxlength="12000" required>${pretty({ hypothesis: "Describe a falsifiable hypothesis supported by this evidence", allowance_micro_usd: 0, deadline: Math.floor(Date.now() / 1000) + 86400, success_criteria: { artifact_valid: true }, stop_conditions: ["deadline", "allowance_exhausted", "single_attempt", "scope_failure", "operator_stop"] })}</textarea></label><p class="muted">Explicitly authorize a hypothesis and ceiling. A prototype alone does not establish a sale or profit.</p><button>Start one bounded experiment</button>`) : item.kind === "experiment" ? '<p class="muted">This opportunity already has its single bounded experiment. <a href="#experiments">Inspect experiments and measured outcomes.</a></p>' : '<p class="muted">Lead assessment uses the existing intake, scope and outreach policy. Discovery alone authorizes no message.</p>'}</article>`).join("") : empty("No discoveries yet. Source content must support a configured need before opportunity intake.")}</section>`;
  const experimentSection = `<section id="experiments"><h2>Experiments</h2>${experiments.length ? experiments.map((item) => `<article><div class="record-head"><h3>${escape(string(item.hypothesis))}</h3>${badge(item.state)}</div><p>Allowance ${money(item.allowance_micro_usd ?? item.allowance)} · Deadline ${escape(date(item.deadline))}</p>${recordDetails(item, "Success criteria, stop conditions, artifact and measured result")}${recoveryForms(item, selectedRun, csrf)}${item.artifact ? `<a class="button secondary" href="${escape(discoveryPagePath + "?" + new URLSearchParams({ runId: selectedRun, experimentId: string(item.id, "") }))}#prototype">Open private prototype</a>` : ""}${form(selectedRun, csrf, "link-order", field("experimentId", string(item.id, "")) + '<label>Existing attributable order ID<input name="orderId" maxlength="200" required></label><label>Actual attribution evidence reference<input name="evidenceReference" maxlength="1000" required placeholder="Verified customer source or order reference"></label><p class="muted">Links an existing order of the same service. It creates no customer, order, payment or revenue.</p><button class="secondary">Attribute an existing order</button>')}${["completed", "stopped", "failed", "expired", "ended", "budget_exhausted"].includes(string(item.state, "")) ? "" : form(selectedRun, csrf, "stop", field("experimentId", string(item.id, "")) + '<label>Stop reason<input name="reason" maxlength="1000" required placeholder="Actual operator reason"></label><button class="secondary">Stop further experiment work</button>')}</article>`).join("") : empty("No experiments authorized. A customer is not required for a self-initiated prototype.")}</section>`;
  const outcomeSection = `<section id="outcomes"><h2>Outcomes and adaptation</h2><p class="muted">Only verified final costs and delivered, unrefunded receipts can support positive measured contribution. Unpaid promises and unknown costs cannot improve priority. Fees, refunds, costs and gross receipts remain separate.</p>${outcomes.length ? outcomes.map((item) => `<article><h3>${escape(string(item.entity_id, string(item.opportunity_id, string(item.experiment_id, string(item.id, "Recorded outcome")))))}</h3>${outcomeFacts(item)}${recordDetails(item, "Responses, conversions, delivery costs, refunds, revisions and contribution")}</article>`).join("") : empty("No measured outcomes recorded.")}${recordDetails(data.priorities, "Priority adaptations within owner limits")}</section>`;
  const queueSection = `<section id="upcoming"><h2>Upcoming work</h2>${upcoming.length ? upcoming.map((item) => `<article><h3>${escape(string(item.kind, string(item.market_id, "Scheduled work")))}</h3><p>${badge(item.state)} · ${escape(date(item.due_at ?? item.deadline))}</p>${recordDetails(item, "Scheduled event reference")}</article>`).join("") : empty("No upcoming work. Waiting spends no research or planning tokens.")}</section>`;
  const historySection = `<section id="attempts"><h2>Durable attempt history</h2><p class="muted">Attempts remain stored across restarts and configuration changes. An uncertain external result must be reconciled before another action.</p>${attempts.length ? attempts.map((item) => `<article><h3>${escape(string(item.market_id, string(item.id)))}</h3><p>${badge(item.state)} · ${escape(date(item.created_at))}</p>${recordDetails(item, "Attempt, reservation and cost evidence")}${recoveryForms(item, selectedRun, csrf)}</article>`).join("") : empty("No research attempts yet.")}</section>`;
  const observationsSection = `<section id="observations"><h2>Source observation history</h2>${observations.length ? observations.map((item) => `<article><p>Observed ${escape(date(item.observed_at))} · Discovery <code>${escape(string(item.discovery_id))}</code></p>${evidence(item.evidence)}${recordDetails(item, "Original source and attempt references")}</article>`).join("") : empty("No source observations recorded.")}</section>`;
  const recoverySection = `<section id="recovery"><h2>Original effects and reconciliation</h2><p class="muted">Observational recovery retains the original action and reservation. It does not authorize a new paid attempt. Expired or stopped experiments keep unresolved effects for operator review.</p>${effects.length ? effects.map((item) => `<article><p><code>${escape(string(item.id))}</code> · ${escape(string(item.action))} · ${badge(item.state)}</p>${recordDetails(item, "Original effect and accounting reference")}</article>`).join("") : empty("No discovery effects recorded.")}${recoveryForms({ effects }, selectedRun, csrf)}</section>`;
  const ownerConfig = Object.keys(config).length
    ? config
    : {
        enabled: false,
        allowance_micro_usd: 0,
        fixture_mode: false,
        adaptation_limit: 5,
        adaptation_min_samples: 3,
        markets: [],
      };
  const settingsSection = `<section id="configuration"><h2>Owner discovery configuration</h2><p class="muted">Use real market sources, need terms, supported services and explicit ceilings. The allowance is a lifetime allocation cap for this run; changing configuration does not erase previous attempts. Credentials and network permissions belong in the existing tools setup.</p>${form(selectedRun, csrf, "configure", `<label>Discovery configuration, JSON<textarea name="configJson" rows="18" maxlength="12000" required>${pretty(ownerConfig)}</textarea></label><button>Save discovery configuration</button>`)}<p><a href="/operator/integrations">Connected services and business policy</a></p></section>`;
  const offset =
    typeof data.history_offset === "number" ? data.history_offset : 0;
  const limit =
    typeof data.history_limit === "number" && data.history_limit > 0
      ? data.history_limit
      : 100;
  const historyLink = (next: number) =>
    discoveryPagePath +
    "?" +
    new URLSearchParams({ runId: selectedRun, offset: String(next) });
  const hasMore = [
    "discoveries",
    "attempts",
    "experiments",
    "outcomes",
    "observations",
    "effects",
    "upcoming",
  ].some(
    (key) =>
      typeof totals[key] === "number" &&
      (totals[key] as number) > offset + limit,
  );
  const historyNote = `<div class="pagination"><p class="muted">History offset ${escape(String(offset))}. Showing at most ${escape(String(limit))} records per collection; durable history remains accessible across pages.</p><div class="controls">${offset > 0 ? `<a class="button secondary" href="${escape(historyLink(Math.max(0, offset - limit)))}">Previous history</a>` : ""}${hasMore ? `<a class="button secondary" href="${escape(historyLink(offset + limit))}">Next history</a>` : ""}</div></div>`;
  const prototypeSection = artifact
    ? `<section id="prototype"><h2>Private prototype: ${escape(string(row(artifact).filename))}</h2><p class="muted">${escape(string(row(artifact).media_type))}. Generated content is shown as source text. This prototype is not a customer delivery or sale.</p><pre>${escape(string(row(artifact).content, "No content returned"))}</pre>${recordDetails(row(artifact).checks, "Scope validation checks")}</section>`
    : "";
  const nav =
    '<nav class="business-nav" aria-label="Discovery sections"><a href="#markets">Markets</a><a href="#discoveries">Discoveries</a><a href="#experiments">Experiments</a><a href="#outcomes">Outcomes</a><a href="#upcoming">Upcoming</a><a href="#attempts">History</a><a href="#configuration">Configuration</a></nav>';
  return layout(
    "Discovery and experiments",
    heading +
      summary +
      nav +
      historyNote +
      prototypeSection +
      marketSection +
      discoverySection +
      experimentSection +
      outcomeSection +
      queueSection +
      historySection +
      observationsSection +
      recoverySection +
      settingsSection,
  );
}
