/** Owner pages for finding first customers, and the public samples gallery. */
import { OFFERINGS, type StoredProfile } from "../runtime/agent.ts";
import {
  MAX_SAMPLE,
  mailtoLink,
  orderLink,
  type Channel,
  type Lead,
  type Prototype,
  type Sample,
  type ShareItem,
} from "../runtime/growth.ts";
import { escape, layout } from "./pages.ts";

const time = (iso: string | null) =>
  iso ? iso.slice(0, 16).replace("T", " ") : "";
const offeringLabel = (id: string) =>
  OFFERINGS.find((item) => item.id === id)?.label ?? id;
const csrfField = (csrf: string) =>
  `<input type="hidden" name="csrf" value="${escape(csrf)}">`;
const hidden = (name: string, value: string | number) =>
  `<input type="hidden" name="${escape(name)}" value="${escape(value)}">`;

function head(
  current: "leads" | "share" | "samples",
  title: string,
  lead: string,
) {
  const link = (id: string, path: string, label: string) =>
    `<a href="${path}"${id === current ? ' aria-current="page"' : ""}>${label}</a>`;
  return `<p class="eyebrow">FIND YOUR FIRST CUSTOMER</p><h1>${escape(title)}</h1><p class="lead">${escape(lead)}</p><nav class="business-nav" aria-label="First customers"><a href="/operator/start">← Getting started</a>${link("share", "/operator/share", "Share kit")}${link("leads", "/operator/leads", "Lead inbox")}${link("samples", "/operator/samples", "Samples")}</nav>`;
}
const noAgent = (current: "leads" | "share" | "samples", title: string) =>
  layout(
    title,
    `${head(current, title, "")}<section class="notice"><h2>No agent yet</h2><p>Create the agent on the getting-started page first.</p></section>`,
  );

export interface LeadsData {
  profile: StoredProfile | null;
  leads: Lead[];
  sync: { ok: boolean; added: number; detail: string };
  /** Lowercase addresses that asked not to be contacted; null when unknown. */
  optedOut: string[] | null;
}
export function leadsPage(data: LeadsData, csrf: string) {
  const title = "Lead inbox";
  if (!data.profile) return noAgent("leads", title);
  const profile = data.profile;
  const open = data.leads.filter((lead) => lead.state === "new");
  const sent = data.leads.filter((lead) => lead.state === "contacted");
  const dismissed = data.leads.filter((lead) => lead.state === "dismissed");
  const evidence = (lead: Lead) =>
    `<blockquote><p>${escape(lead.excerpt || "No excerpt was stored.")}</p>${lead.sourceUrl ? `<p><a href="${escape(lead.sourceUrl)}" rel="noreferrer noopener" target="_blank">${escape(lead.sourceUrl)}</a></p>` : ""}</blockquote>`;
  const pills = (lead: Lead) =>
    `<span class="pill">${escape(offeringLabel(lead.service))}</span> <span class="pill">${lead.contact ? "Names a contact address" : "Reply where it was posted"}</span>`;
  const openCards = open
    .map((lead) => {
      // A lead that names an address can only be sent once opt-outs were read.
      const stopped =
        lead.contact !== null &&
        data.optedOut !== null &&
        data.optedOut.includes(lead.contact);
      const unchecked = lead.contact !== null && data.optedOut === null;
      const held = stopped || unchecked;
      const mail = held ? null : mailtoLink(lead, profile);
      return `<article><div class="record-head"><h3>${escape(lead.need || "Matching request")}</h3><span>${pills(lead)}</span></div>${evidence(lead)}<form method="post" action="/operator/leads">${csrfField(csrf)}${hidden("id", lead.id)}<label>Your reply (edit it before you send it)<textarea name="draft" rows="11" maxlength="4000" required>${escape(lead.draft)}</textarea></label>${lead.contact ? `<p class="muted">Contact found in the source: <code>${escape(lead.contact)}</code></p>` : `<p class="muted">No contact address was found. Paste this reply where the request was posted, if that site's rules allow it.</p>`}${stopped ? '<p class="warning-text">This person asked not to be contacted. Dismiss this lead.</p>' : unchecked ? '<p class="warning-text">Opt-outs could not be checked right now, so this lead cannot be sent yet.</p>' : ""}<div class="controls"><button class="secondary small-button" name="action" value="save">Save draft</button>${mail ? `<a class="button secondary small-button" href="${escape(mail)}">Open in my mail app</a>` : ""}${held ? "" : '<button class="small-button" name="action" value="contacted">I sent it</button>'}<button class="secondary small-button" name="action" value="dismiss">Dismiss</button></div></form></article>`;
    })
    .join("");
  const sentRows = sent
    .map(
      (lead) =>
        `<div class="row"><div class="row-main"><strong>${escape(lead.need || "Matching request")}</strong><p class="muted">${escape(offeringLabel(lead.service))}${lead.contact ? ` · ${escape(lead.contact)}` : ""}</p><details><summary class="muted">What was sent</summary><pre>${escape(lead.draft)}</pre></details></div><div class="row-meta"><time>${escape(time(lead.decidedAt))}</time><p><span class="pill ok">Sent</span></p></div></div>`,
    )
    .join("");
  const dismissedRows = dismissed
    .map(
      (lead) =>
        `<div class="row"><div class="row-main"><strong>${escape(lead.need || "Matching request")}</strong><p class="muted">${escape(offeringLabel(lead.service))}</p></div><div class="row-meta"><form method="post" action="/operator/leads">${csrfField(csrf)}${hidden("id", lead.id)}<button class="secondary small-button" name="action" value="reopen">Reopen</button></form></div></div>`,
    )
    .join("");
  return layout(
    title,
    `${head("leads", title, "Requests your agent found on the pages you asked it to scan, each with a reply ready for you to review and send.")}<section class="notice"><h2>You send every message</h2><p>WideMind drafts; you decide. A match shows that a page mentioned a need, not that anyone wants to buy. Follow the rules of the site where a request was posted and the anti-spam law where you and the recipient live. Each address can be marked as sent once.</p>${data.sync.ok ? (data.sync.added ? `<p class="ok-text">${data.sync.added} new lead(s) added.</p>` : "") : `<p class="warning-text">${escape(data.sync.detail)}</p>`}</section><section><h2>To review (${open.length})</h2>${openCards || '<p class="muted">Nothing to review. Leads come from the optional scan on Getting started: give it one to five pages where people ask for this kind of work.</p>'}</section>${sent.length ? `<section><h2>Sent (${sent.length})</h2><div class="row-list">${sentRows}</div></section>` : ""}${dismissed.length ? `<section><details><summary><strong>Dismissed (${dismissed.length})</strong></summary><div class="row-list">${dismissedRows}</div></details></section>` : ""}`,
  );
}

const CHANNEL_LABELS: [Channel, string][] = [
  ["share", "Your share links"],
  ["lead", "Lead replies"],
  ["sample", "Samples gallery"],
  ["repeat", "Returning customers"],
  ["friend", "Customer referrals"],
  ["other", "Other links"],
];
export interface ShareData {
  profile: StoredProfile | null;
  items: ShareItem[];
  local: boolean;
  counts: Record<Channel | "total", number>;
  /** Requests per customer referral code. */
  referrals: { ref: string; requests: number }[];
}
export function sharePage(data: ShareData) {
  const title = "Share kit";
  if (!data.profile) return noAgent("share", title);
  const items = data.items
    .map(
      (item) =>
        `<section><div class="record-head"><h2>${escape(item.offering.label)}</h2><span class="pill">${escape(item.price)}</span></div><label>Order link (opens the request form with this service chosen)<input readonly value="${escape(item.link)}"></label><div class="share-layout"><div>${item.posts.map((post) => `<label>${escape(post.label)}<textarea readonly rows="${post.text.includes("\n") ? 7 : 3}">${escape(post.text)}</textarea></label>`).join("")}</div><div><img class="share-card" src="${escape(item.card)}" alt="${escape(`Card for ${item.offering.label}`)}" width="1200" height="630"><p><a class="button secondary small-button" href="${escape(item.card)}" download="${escape(`${item.offering.id}-card.svg`)}">Download the card</a></p><p class="muted"><small>The card is an SVG image. Convert it to PNG if a site does not accept SVG.</small></p></div></div></section>`,
    )
    .join("");
  const counts = CHANNEL_LABELS.map(
    ([channel, label]) =>
      `<dt>${escape(label)}</dt><dd>${data.counts[channel]}</dd>`,
  ).join("");
  return layout(
    title,
    `${head("share", title, "The quickest first sale usually comes from people who already know you. Post these where you already are.")}${data.local ? '<section class="notice"><h2>These links only work on this computer</h2><p class="warning-text">Your site is still at a local address. Deploy it on a domain with HTTPS before sharing; the links below will then use that address.</p></section>' : ""}${items || '<section><p class="muted">Choose at least one offering in the agent settings.</p></section>'}<section><h2>Where requests came from</h2><p class="muted">Counts requests that arrived through a tracked link (${data.counts.total} so far). A request is not a sale; verified payments are on the Pilot page.</p><dl class="facts">${counts}</dl>${data.referrals.length ? `<h3>Customer referral codes</h3><p class="muted">A customer's referral link ends in their code. Compare it with this list to confirm a referral.</p><dl class="facts">${data.referrals.map((item) => `<dt><code>${escape(item.ref)}</code></dt><dd>${item.requests}</dd>`).join("")}</dl>` : ""}</section>`,
  );
}

const KIND_LABELS: Record<string, string> = {
  svg: "SVG graphic",
  html: "Web page (HTML)",
  text: "Text or Markdown",
};
function preview(sample: Sample, base: string) {
  if (sample.kind === "svg")
    return `<img class="sample-art" src="${base}/${sample.id}.svg" alt="${escape(sample.title)}" loading="lazy">`;
  if (sample.kind === "html")
    return `<iframe class="sample-frame" sandbox src="${base}/${sample.id}.html" title="${escape(sample.title)}" loading="lazy"></iframe>`;
  return `<pre class="sample-text">${escape(sample.content.slice(0, 1500))}${sample.content.length > 1500 ? "\n…" : ""}</pre>`;
}
export interface SamplesData {
  profile: StoredProfile | null;
  samples: Sample[];
  prototypes: Prototype[];
  /** Why finished experiment prototypes could not be listed, if so. */
  unavailable: string | null;
}
export function samplesOperatorPage(data: SamplesData, csrf: string) {
  const title = "Samples";
  if (!data.profile) return noAgent("samples", title);
  const profile = data.profile;
  const options = OFFERINGS.filter((item) => profile.offerings[item.id])
    .map(
      (item) =>
        `<option value="${escape(item.id)}">${escape(item.label)}</option>`,
    )
    .join("");
  const imported = new Set(data.samples.map((sample) => sample.origin));
  const cards = data.samples
    .map(
      (sample) =>
        `<article><div class="record-head"><h3>${escape(sample.title)}</h3><span><span class="pill">${escape(offeringLabel(sample.service))}</span> <span class="pill${sample.state === "published" ? " ok" : ""}">${sample.state === "published" ? "Public" : "Draft"}</span></span></div>${preview(sample, "/operator/sample-preview")}<div class="controls"><form method="post" action="/operator/samples">${csrfField(csrf)}${hidden("id", sample.id)}${sample.state === "draft" ? '<button class="small-button" name="action" value="publish">Publish to the gallery</button>' : ""}<button class="secondary small-button" name="action" value="retract">${sample.state === "published" ? "Take down" : "Delete draft"}</button></form></div></article>`,
    )
    .join("");
  const prototypes = data.prototypes
    .filter((item) => !imported.has(`experiment:${item.experimentId}`))
    .map(
      (item) =>
        `<div class="row"><div class="row-main"><strong>${escape(item.filename)}</strong><p class="muted">${escape(item.title)}${item.service ? ` · ${escape(offeringLabel(item.service))}` : ""}</p></div><div class="row-meta"><form method="post" action="/operator/samples">${csrfField(csrf)}${hidden("experimentId", item.experimentId)}<button class="secondary small-button" name="action" value="import">Add as a draft</button></form></div></div>`,
    )
    .join("");
  return layout(
    title,
    `${head("samples", title, "Show what your agent makes. An empty storefront sells little; a few real examples help a visitor decide.")}<section class="notice"><h2>Only what you publish is public</h2><p>Samples start as drafts. Publish only work you may show: your own examples or prototypes made as experiments, never a customer's delivery without their consent.</p><p><a href="/samples" target="_blank" rel="noopener">Open the public gallery ↗</a></p></section><section><h2>Your samples</h2>${cards || '<p class="muted">No samples yet. Add one below.</p>'}</section><section><h2>From finished experiments</h2>${data.unavailable ? `<p class="warning-text">${escape(data.unavailable)}</p>` : prototypes || '<p class="muted">No finished prototypes to add. Experiments are started on the Discovery page.</p>'}</section><section><h2>Add a sample</h2><form method="post" action="/operator/samples">${csrfField(csrf)}<div class="form-grid"><label>Title<input name="title" required maxlength="120" placeholder="For example: Badge for a bakery"></label><label>Shows this offering<select name="service" required>${options}</select></label></div><label>Type<select name="kind">${Object.entries(
      KIND_LABELS,
    )
      .map(([id, label]) => `<option value="${id}">${escape(label)}</option>`)
      .join(
        "",
      )}</select></label><label>Content (paste the SVG, HTML or text)<textarea name="content" rows="10" maxlength="${MAX_SAMPLE}" required></textarea></label><p class="muted"><small>SVG with scripts or event handlers is refused. HTML is shown in a locked-down frame where scripts do not run.</small></p><button name="action" value="add">Save as a draft</button></form></section>`,
  );
}

export function samplesPublicPage(
  agentName: string | null,
  samples: Sample[],
  origin: string,
) {
  const cards = samples
    .map(
      (sample) =>
        `<article class="sample"><div class="sample-view">${preview(sample, "/samples")}</div><h3>${escape(sample.title)}</h3><p class="muted">${escape(offeringLabel(sample.service))}</p><a class="button secondary small-button" href="${escape(orderLink(origin, { service: sample.service, ref: `sample-${sample.id}` }))}">Order one like this</a></article>`,
    )
    .join("");
  return layout(
    "Samples",
    `<p class="eyebrow">EXAMPLES OF THE WORK</p><h1>${escape(agentName ?? "Samples")}</h1><p class="lead">Examples chosen by the owner. Your own request is quoted exactly before you pay, and delivered privately.</p>${cards ? `<div class="sample-grid">${cards}</div>` : '<section class="notice"><h2>No samples yet</h2><p>Nothing has been published here yet. You can still describe what you need.</p></section>'}<div class="hero-actions"><a class="button" href="/commerce">Request work</a></div>`,
  );
}

/** The card on Getting started that points at the three tools. */
export function firstCustomerCard(input: {
  newLeads: number;
  publishedSamples: number;
  requests: number;
}) {
  const row = (title: string, body: string, path: string, action: string) =>
    `<div class="row"><div class="row-main"><strong>${escape(title)}</strong><p class="muted">${escape(body)}</p></div><div class="row-meta"><a class="button secondary small-button" href="${path}">${escape(action)}</a></div></div>`;
  return `<section><h2>Find your first customer</h2><p class="muted">${input.requests} request(s) have arrived through tracked links so far.</p><div class="row-list">${row("Share kit", "An order link, ready-to-post text and a card image for each offering.", "/operator/share", "Open")}${row("Samples", `${input.publishedSamples} published. Show examples of what it makes.`, "/operator/samples", "Open")}${row("Lead inbox", `${input.newLeads ? `${input.newLeads} to review. ` : ""}Requests it found, with a reply drafted for you to send.`, "/operator/leads", "Open")}</div></section>`;
}
