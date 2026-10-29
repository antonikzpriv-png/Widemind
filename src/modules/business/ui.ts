/** Trusted SSR business pages. Task/customer/provider data is always escaped. */
import { randomUUID } from "node:crypto";
import type { PublicWebsiteData } from "../../contracts/index.ts";
import { displayUsd, escape, layout } from "../../web/pages.ts";

const consolePath = "/operator/modules/business/console";
const commandPath = "/api/modules/business/command";
const views = [
  "overview",
  "opportunities",
  "orders",
  "finances",
  "recovery",
  "settings",
] as const;
type View = (typeof views)[number];
type Row = Record<string, unknown>;
const asRow = (value: unknown): Row =>
  value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Row)
    : {};
const rows = (value: unknown): Row[] =>
  Array.isArray(value) ? value.map(asRow) : [];
const string = (value: unknown, fallback = "Unavailable"): string =>
  typeof value === "string" && value.length ? value : fallback;
const number = (value: unknown): number =>
  typeof value === "number" && Number.isSafeInteger(value) && value >= 0
    ? value
    : 0;
const pretty = (value: unknown) =>
  escape(JSON.stringify(value ?? null, null, 2));
const badge = (value: unknown) =>
  `<span class="pill">${escape(
    string(value)
      .replace(/([a-z])([A-Z])/g, "$1 $2")
      .replace(/[_-]/g, " "),
  )}</span>`;
function amount(value: unknown) {
  try {
    return displayUsd(string(value, ""));
  } catch {
    return "Unavailable";
  }
}
function date(value: unknown) {
  return typeof value === "string" && Number.isFinite(Date.parse(value))
    ? value
    : "Not set";
}
function scopeTitle(value: unknown) {
  const scope = asRow(value);
  return string(scope.topic, string(scope.description, "Agreed scope"));
}
function link(runId: string, view: string, extra: Record<string, string> = {}) {
  return `${consolePath}?${new URLSearchParams({ runId, view, ...extra })}`;
}
function field(name: string, value: string) {
  return `<input type="hidden" name="${escape(name)}" value="${escape(value)}">`;
}
function fields(runId: string, view: View, csrf: string, command: string) {
  return (
    field("csrf", csrf) +
    field("runId", runId) +
    field("view", view) +
    field("requestId", randomUUID()) +
    field("command", command)
  );
}
function form(
  runId: string,
  view: View,
  csrf: string,
  command: string,
  body: string,
) {
  return `<form method="post" action="${commandPath}">${fields(runId, view, csrf, command)}${body}</form>`;
}
function empty(text: string) {
  return `<p class="empty">${escape(text)}</p>`;
}
function stat(label: string, value: string, note: string) {
  return `<section><p class="stat-label">${escape(label)}</p><p class="stat-value">${escape(value)}</p><small>${escape(note)}</small></section>`;
}
function countsOf(data: Row) {
  return asRow(data.counts);
}
function pageControls(
  data: Row,
  kind: string,
  runId: string,
  view: View,
  query: Record<string, string>,
) {
  const specific = asRow(asRow(data.pagination)[kind]);
  const offset = number(specific.offset);
  const limit = number(specific.limit) || 500;
  const total = number(specific.total);
  const shown = rows(data[kind]).length;
  if (!total && !shown) return "";
  const countText = total
    ? `Showing ${Math.min(total, offset + 1)}–${Math.min(total, offset + shown)} of ${total}`
    : `Showing ${shown} stored records`;
  const extra: Record<string, string> = { collection: kind };
  if (query.orderId) extra.orderId = query.orderId;
  const previous =
    specific.hasPrevious === true || offset > 0
      ? `<a class="button secondary small-button" href="${escape(link(runId, view, { ...extra, offset: String(Math.max(0, offset - limit)) }))}">Previous page</a>`
      : "";
  const next =
    specific.hasMore === true
      ? `<a class="button secondary small-button" href="${escape(link(runId, view, { ...extra, offset: String(offset + limit) }))}">Next page</a>`
      : "";
  return `<div class="pagination"><span>${escape(countText)} · Counts cover the whole run.</span><div class="controls">${previous}${next}</div></div>`;
}

function customerMap(data: Row) {
  return new Map(
    rows(data.customers).map((customer) => [
      string(customer.id, ""),
      string(customer.email, "Unlisted customer"),
    ]),
  );
}
function overview(data: Row, runId: string) {
  const counts = countsOf(data),
    financial = asRow(data.summary);
  const items = rows(data.orders)
    .filter((order) => order.deliveryObligationOpen === true)
    .slice(0, 8);
  const obligations = items.length
    ? items
        .map(
          (order) =>
            `<div class="row"><div class="row-main"><strong>${escape(scopeTitle(order.scope))}</strong><p>${escape(string(order.service))} · ${escape(string(order.status))}</p></div><div class="row-meta">Due ${escape(date(order.deliveryDueAt))}<br>${escape(amount(order.priceMicros))}</div></div>`,
        )
        .join("")
    : empty(
        "No delivery obligations are visible in this page of records. Global counts appear above.",
      );
  const integrations = rows(data.integrations)
    .map(
      (item) =>
        `<div class="row"><div class="row-main"><strong>${escape(string(item.name ?? item.id, "Integration"))}</strong><p>${escape(string(item.detail ?? item.reason, "Readiness is reported by the configured module."))}</p></div>${badge(item.status ?? (item.available === true ? "available" : "unavailable"))}</div>`,
    )
    .join("");
  return `<div class="stats">${stat("Opportunities", String(number(counts.opportunities)), "Durable requests and experiments")}${stat("Open commitments", String(number(counts.openCommitments)), "Work still owed to customers")}${stat("Customer receipts", amount(financial.customerReceiptsMicros), "Verified cash, distinct from earned revenue")}${stat("Spendable", amount(financial.spendableMicros), "After reservations and customer holds")}</div><div class="business-grid"><section><div class="section-heading"><h2>Delivery commitments</h2><a href="${escape(link(runId, "orders"))}">View all orders →</a></div>${obligations}</section><section><h2>Integration readiness</h2>${integrations || empty("No configured integration readiness is available.")}</section></div><section><h2>One pipeline, many kinds of work.</h2><p class="muted">Receive an opportunity, assess its cost, agree a scope, obtain authorization, create and check the work, then deliver and reconcile the result. Research is one supported service; enabled tools and policy determine the next opportunity.</p><div class="badge-row"><span class="pill">Discover</span><span class="pill">Assess</span><span class="pill">Offer</span><span class="pill">Fulfill</span><span class="pill">Evaluate</span></div></section>`;
}
function opportunities(
  data: Row,
  runId: string,
  csrf: string,
  query: Record<string, string>,
) {
  const customers = customerMap(data);
  const records = rows(data.opportunities)
    .map(
      (item) =>
        `<article><div class="record-head"><h3>${escape(scopeTitle(item.scope))}</h3>${badge(item.status)}</div><p class="scope-summary">${escape(string(item.service))} · ${escape(string(item.origin))} · Priority ${escape(String(item.priority ?? 0))}</p><p class="muted">${escape(customers.get(string(item.customerId, "")) ?? "No identified customer")}</p><details><summary>Scope and feasibility</summary><pre>${pretty({ scope: item.scope, assessment: item.assessment, sourceReference: item.sourceReference })}</pre></details>${["needsReview", "budgetWait"].includes(string(item.status)) ? form(runId, "opportunities", csrf, "wake", field("opportunityId", string(item.id, "")) + field("evidenceReference", "operator reviewed opportunity and requested a wake") + '<button class="secondary small-button">Queue reassessment</button>') : ""}</article>`,
    )
    .join("");
  const quotes = rows(data.quotes)
    .map((quote) => {
      const opportunity = rows(data.opportunities).find(
        (item) => item.id === quote.opportunityId,
      );
      const email = opportunity
        ? (customers.get(string(opportunity.customerId, "")) ?? "")
        : "";
      return `<article><div class="record-head"><h3>${escape(scopeTitle(quote.scope))}</h3>${badge(quote.status)}</div><p>${escape(amount(quote.priceMicros))} quote · ${escape(amount(quote.costMicros))} cost ceiling</p><p class="muted">Expires ${escape(date(quote.expiresAt))}</p>${quote.status === "offered" ? form(runId, "opportunities", csrf, "accept", field("quoteId", string(quote.id, "")) + `<label>Customer email<input name="customerEmail" type="email" value="${escape(email)}" maxlength="254" required></label><label>Actual acceptance evidence reference<input name="evidenceReference" required maxlength="1000" placeholder="Verified customer message reference"></label><button>Queue customer acceptance</button>`) : ""}</article>`;
    })
    .join("");
  const intake = form(
    runId,
    "opportunities",
    csrf,
    "intake",
    `<div class="form-grid"><label>Service<input name="service" value="research-brief" maxlength="100" required></label><label>Customer email<input name="customerEmail" type="email" maxlength="254" placeholder="Actual requester, if known"></label><label>Origin<select name="origin"><option value="inbound">Incoming customer request</option><option value="discovered">Discovered opportunity</option><option value="outbound">Targeted outbound</option></select></label><label>Priority<input name="priority" type="number" min="0" max="100" value="1" required></label><label class="span-all">Source or relevance reference<input name="sourceReference" maxlength="2000" placeholder="Actual message reference or relevant HTTPS URL"></label><label class="span-all">Proposed scope, as JSON<textarea name="scopeJson" rows="7" required placeholder='{"topic":"Actual customer research topic","sections":["Overview","Risks"],"minSources":2}'></textarea></label></div><p class="muted">Record a real request or lead. A proposal cannot create a customer payment or delivery commitment.</p><button>Queue opportunity assessment</button>`,
  );
  return `<div class="business-grid"><section><h2>Opportunity pipeline</h2>${records || empty("No opportunities recorded yet.")}${pageControls(data, "opportunities", runId, "opportunities", query)}</section><section><h2>Record an opportunity</h2>${intake}</section></div><section><h2>Scoped quotes</h2>${quotes || empty("No quotes have been proposed.")}</section>`;
}
function orders(
  data: Row,
  runId: string,
  csrf: string,
  query: Record<string, string>,
) {
  const customers = customerMap(data);
  const records = rows(data.orders)
    .map(
      (order) =>
        `<article><div class="record-head"><h3>${escape(scopeTitle(order.scope))}</h3>${badge(order.status)}</div><p class="scope-summary">${escape(customers.get(string(order.customerId, "")) ?? "Customer stored privately")} · ${escape(string(order.service))}</p><dl class="facts"><dt>Agreed price</dt><dd>${escape(amount(order.priceMicros))}</dd><dt>Cost ceiling</dt><dd>${escape(amount(order.costMicros))}</dd><dt>Delivery due</dt><dd>${escape(date(order.deliveryDueAt))}</dd><dt>Delivery obligation</dt><dd>${order.deliveryObligationOpen === true ? "Open" : "No open obligation reported"}</dd><dt>Payment observation</dt><dd>${escape(date(order.paymentObservedAt))}</dd></dl><details><summary>Agreed scope and payment evidence</summary><p class="muted">This is the last stored order snapshot, not a fresh processor confirmation.</p><pre>${pretty({ scope: order.scope, payment: order.payment, paymentSource: order.paymentSource })}</pre></details>${form(runId, "orders", csrf, "wake", field("orderId", string(order.id, "")) + '<label>Evidence for rechecking this order<input name="evidenceReference" required maxlength="1000" placeholder="Relevant provider or operator reference"></label><button class="secondary">Recheck recorded state</button>')}${order.status === "paymentHold" ? form(runId, "orders", csrf, "resolve-hold", field("orderId", string(order.id, "")) + '<label>Resolution<select name="decision"><option value="resume">Resume after verified payment</option><option value="cancel">Cancel commitment; retain refund exposure</option></select></label><label>Resolution evidence<input name="evidenceReference" required maxlength="1000"></label><button>Queue hold resolution</button>') : ""}${order.hasArtifact === true ? `<a class="button secondary small-button" href="${escape(link(runId, "orders", { orderId: string(order.id, "") }))}">Open private deliverable</a>` : '<p class="muted">No completed deliverable is reported for this order.</p>'}</article>`,
    )
    .join("");
  const selected = asRow(data.selectedOrder);
  const artifact = asRow(selected.artifact);
  const selectedMarkup = Object.keys(selected).length
    ? `<section><div class="section-heading"><h2>Private deliverable</h2><a href="${escape(link(runId, "orders"))}">Close detail</a></div><p>${escape(scopeTitle(selected.scope))}</p>${Object.keys(artifact).length ? `<p class="muted">${escape(string(artifact.filename))} · ${escape(string(artifact.mediaType ?? artifact.media_type))}</p><details open><summary>Validated content</summary><pre>${escape(string(artifact.content, "No textual artifact content is available."))}</pre></details><details><summary>Scope checks and source evidence</summary><pre>${pretty({ checks: artifact.checks, sources: artifact.sources })}</pre></details>` : empty("No validated deliverable is stored for this order.")}</section>`
    : "";
  return `${selectedMarkup}<section><div class="section-heading"><h2>Orders and fulfillment</h2><span class="muted">${number(countsOf(data).openCommitments)} open commitments across the run</span></div><p class="muted">Receipts do not discharge a delivery obligation. Unknown outcomes need reconciliation before another external action.</p>${records || empty("No orders have been accepted.")}${pageControls(data, "orders", runId, "orders", query)}</section>`;
}
function finances(data: Row) {
  const summary = asRow(data.summary);
  if (!Object.keys(summary).length)
    return `<section class="notice"><h2>Accounting unavailable</h2><p>No verified financial summary is configured for this run. No opening grant, receipts, available funds, or profitable outcome is assumed.</p><a class="button secondary" href="/operator">Open runtime and funding controls</a></section>`;
  const metrics = [
    ["Spendable", "spendableMicros", "Available after reservations and holds"],
    ["Customer receipts", "customerReceiptsMicros", "Verified cash receipts"],
    [
      "Earned revenue",
      "earnedRevenueMicros",
      "Performance recognized by economy",
    ],
    [
      "Operating charges",
      "operatingChargesMicros",
      "Includes billable failures and retries",
    ],
  ];
  const fields = [
    ["Opening credit", "openingCreditMicros"],
    ["Posted balance", "postedMicros"],
    ["Active reservations", "reservedMicros"],
    ["Customer / refund holds", "holdsMicros"],
    ["Owner funding", "ownerFundingMicros"],
    ["Subsidies", "subsidiesMicros"],
    ["Processor fees", "processorFeesMicros"],
    ["Refunds", "refundsMicros"],
    ["Chargebacks", "chargebacksMicros"],
    ["Test funding", "testFundingMicros"],
  ];
  return `<div class="badge-row">${badge(summary.mode)}<span class="muted">Authoritative economy snapshot · ${escape(date(summary.asOf))}</span></div><div class="stats">${metrics.map(([label, key, note]) => stat(label!, amount(summary[key!]), note!)).join("")}</div><section><h2>Where the money stands</h2><div class="table-wrap"><table><thead><tr><th>Category</th><th>Verified amount · USD</th></tr></thead><tbody>${fields.map(([label, key]) => `<tr><td>${escape(label)}</td><td>${escape(amount(summary[key!]))}</td></tr>`).join("")}</tbody></table></div><p class="muted">Spendable = posted − active reservations − holds. Cash receipts and earned revenue are separate. Sandbox totals represent test funds. Only the economy can credit or reconcile money.</p></section>`;
}
function recovery(
  data: Row,
  runId: string,
  csrf: string,
  query: Record<string, string>,
) {
  const effects = rows(data.effects)
    .map(
      (effect) =>
        `<tr><td><code>${escape(string(effect.id))}</code><small>${escape(string(effect.provider ?? effect.tool))}</small></td><td>${escape(string(effect.action ?? effect.kind))}</td><td>${badge(effect.state ?? effect.status)}</td><td>${escape(string(effect.reason, "No additional reason"))}</td></tr>`,
    )
    .join("");
  const events = rows(data.events)
    .map(
      (event) =>
        `<article><div class="record-head"><h3>${escape(string(event.kind))}</h3>${badge(event.state ?? event.status)}</div><p class="muted">${escape(string(event.error ?? event.reason, "No recorded error"))} · ${escape(date(event.dueAt ?? event.receivedAt))}</p><details><summary>Private event reference</summary><code>${escape(string(event.id ?? event.eventId))}</code></details></article>`,
    )
    .join("");
  return `<div class="stats">${stat("Unresolved effects", String(number(countsOf(data).unresolvedEffects)), "Reconcile external evidence before retrying")}${stat("Blocked events", String(number(countsOf(data).blockedEvents)), "Needs dependency or operator attention")}${stat("In fulfillment", String(number(countsOf(data).inFulfillment)), "Includes work not yet delivered")}${stat("Awaiting payment", String(number(countsOf(data).awaitingPayment)), "Customer promises are not settlement")}</div><section><h2>Actions and reconciliation</h2><p class="muted">The persistent runtime owns authorization, leases, retries, cancellation, and uncertain action reconciliation. Use Run control to inspect durable receipts and request recovery; this page never resends an unknown effect.</p><a class="button secondary" href="/operator">Open run control and recovery</a>${effects ? `<div class="table-wrap"><table><thead><tr><th>Action reference</th><th>Operation</th><th>State</th><th>Recorded reason</th></tr></thead><tbody>${effects}</tbody></table></div>` : empty("No action records in this page.")}${pageControls(data, "effects", runId, "recovery", query)}</section><section><h2>Recorded events</h2>${events || empty("No pending or blocked events are reported.")}${pageControls(data, "events", runId, "recovery", query)}</section><section><h2>Inspect the mailbox</h2><p class="muted">A configured Gmail adapter can receive verified customer requests. Refreshing this page does not invoke a model or inspect a remote mailbox.</p>${form(runId, "recovery", csrf, "sync-inbox", '<button class="secondary">Queue one mailbox sync</button>')}</section>`;
}
function settings(data: Row, runId: string, csrf: string) {
  const integrations = rows(data.integrations)
    .map(
      (item) =>
        `<div class="row"><div class="row-main"><strong>${escape(string(item.name ?? item.id))}</strong><p>${escape(string(item.detail ?? item.reason, "Configuration and provider qualification are separate."))}</p></div>${badge(item.status ?? (item.available === true ? "available" : "unavailable"))}</div>`,
    )
    .join("");
  return `<div class="business-grid"><section><h2>Goals, capability policy, and budgets</h2><p class="muted">Business policy cannot grant credentials, increase the operating wallet, or bypass gateway capability checks. Changing a policy does not rewrite an agreed quote.</p>${form(runId, "settings", csrf, "policy", `<label>Operator-owned business policy, JSON<textarea name="policyJson" rows="18" required>${pretty(data.policy)}</textarea></label><button>Queue policy update</button>`)}</section><section><h2>Provider readiness</h2>${integrations || empty("No readiness diagnostics are configured.")}<div class="keyline"><h3>Run state</h3><p>${badge(data.runtimeState)}</p><p class="muted">Pause, resume, stop, funding, and worker health use the existing authenticated runtime controls.</p><a class="button secondary" href="/operator">Open run controls</a></div></section></div><section><h2>Customer preferences</h2><p class="muted">An opt-out prevents new automatic contact. Existing delivery and refund obligations remain recorded for operator handling.</p>${form(runId, "settings", csrf, "opt-out", '<div class="form-grid"><label>Customer email<input name="email" type="email" maxlength="254" required></label><label>Actual opt-out evidence reference<input name="evidenceReference" maxlength="1000" required></label></div><button class="secondary">Queue customer opt-out</button>')}</section>`;
}

export function renderBusinessPage(
  value: unknown,
  csrf: string,
  query: Record<string, string> = {},
): string {
  const data = asRow(value),
    runId = string(data.runId, string(query.runId, ""));
  const view: View = views.includes(query.view as View)
    ? (query.view as View)
    : "overview";
  const selected = `<form method="get" action="${consolePath}" class="inline-form"><label>Runtime run ID<input name="runId" value="${escape(runId)}" placeholder="Choose a run from Run control" maxlength="200" required></label>${field("view", view)}<button class="secondary">Open run</button><a class="button secondary" href="/operator">Run control</a></form>`;
  if (!runId)
    return layout(
      "Business",
      `<p class="eyebrow">PRIVATE BUSINESS OPERATIONS</p><h1>Every commitment,<br>in one place.</h1><p class="lead">Open a runtime run to inspect its opportunities, orders, financial records, and recovery state.</p><section><h2>Choose a run</h2>${selected}</section>`,
    );
  const content =
    view === "opportunities"
      ? opportunities(data, runId, csrf, query)
      : view === "orders"
        ? orders(data, runId, csrf, query)
        : view === "finances"
          ? finances(data)
          : view === "recovery"
            ? recovery(data, runId, csrf, query)
            : view === "settings"
              ? settings(data, runId, csrf)
              : overview(data, runId);
  return layout(
    "Business operations",
    `<div class="business-top"><div><p class="eyebrow">PRIVATE BUSINESS OPERATIONS</p><h1>WideMind workspace</h1><p class="muted">Opportunities, commitments, and the evidence behind them.</p></div><div class="badge-row">${badge(data.runtimeState)}${badge(data.usesFixtures === true ? "explicit demonstration" : (data.executionProfile ?? "foundation"))}</div></div>${selected}<nav class="business-nav" aria-label="Business views">${views.map((item) => `<a href="${escape(link(runId, item))}"${item === view ? ' aria-current="page"' : ""}>${escape(item[0]!.toUpperCase() + item.slice(1))}</a>`).join("")}</nav>${data.usesFixtures === true ? '<section class="notice"><p>Explicit fixture demonstration. No real customer, payment, delivery, or earnings are claimed.</p></section>' : ""}<p class="muted">Commands are queued. Resume a paused run in Run control to process its pending work.</p>${content}`,
  );
}

export async function renderBusinessPublic(
  data: PublicWebsiteData,
  contactEmail?: string,
): Promise<string> {
  const safeContact =
    typeof contactEmail === "string" &&
    /^[^\s<>@,;\x00-\x1f\x7f]+@[^\s<>@,;\x00-\x1f\x7f]+\.[^\s<>@,;\x00-\x1f\x7f]+$/.test(
      contactEmail,
    )
      ? contactEmail
      : null;
  const contact = safeContact
    ? `<a class="button" href="mailto:${escape(encodeURIComponent(safeContact))}?subject=WideMind%20project%20request">Discuss a project →</a>`
    : '<span class="pill">Customer contact not connected</span>';
  return layout(
    "Headquarters",
    `<div class="hero"><p class="eyebrow">AN OPEN-ENDED BUSINESS EXPERIMENT</p><h1>One mind.<br>Useful work.</h1><p class="lead">WideMind finds opportunities, agrees a clear scope, and builds useful work. Its starting operating-credit goal is $5; verified accounting and fulfilled commitments tell the rest of the story.</p><div class="hero-actions">${contact}<a class="button secondary" href="/operator">Operator workspace</a></div></div><div class="stats">${stat("Spendable", data.financial ? amount(data.financial.spendableMicros) : "Unavailable", "Verified economy, after holds")}${stat("Customer receipts", data.financial ? amount(data.financial.customerReceiptsMicros) : "Unavailable", "Cash received, distinct from earnings")}${stat("Earned revenue", data.financial ? amount(data.financial.earnedRevenueMicros) : "Unavailable", "Recognized against delivered work")}${stat("Accounting mode", data.financial?.mode ?? "Unverified", data.financial ? `Observed ${data.financial.asOf}` : "No opening grant or payment assumed")}</div><section><div class="section-heading"><h2>What WideMind can take on</h2><span class="muted">Scoped around configured capabilities</span></div>${data.offerings.length ? data.offerings.map((item) => `<article><h3>${escape(item.title)}</h3><p class="muted">${escape(item.description)}</p></article>`).join("") : empty("No live offerings are available until the required tools and delivery/payment capabilities are configured.")}<p class="muted">Research, writing, graphics, and software are possible directions. A service is offered only when its configured capabilities can support the agreed scope.</p></section><div class="business-grid"><section><h2>Work with a clear commitment</h2><p class="muted">Start with a real request. WideMind assesses feasibility and cost, proposes an offer, verifies payment, obtains spending authorization, and checks its deliverable before private delivery.</p>${contact}<p class="muted">Contact opens your email client. Nothing is sent without your own explicit send action.</p></section><section><h2>Selected public activity</h2>${data.activity.length ? data.activity.map((item) => `<div class="row"><div class="row-main"><p>${escape(item.text)}</p><small>${escape(item.at)}</small></div></div>`).join("") : empty("No curated public activity yet. Customer conversations and private work stay private.")}</section></div>`,
  );
}
