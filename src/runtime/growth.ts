/**
 * First-customer tooling: a lead inbox the owner sends from, a share kit,
 * a samples gallery, request attribution and a payment-fee estimate.
 * Nothing here calls a model, spends credit or sends a message. Drafts are
 * built from fixed templates; a person reviews and sends every one.
 */
import type { DatabaseSync } from "node:sqlite";
import { transaction } from "../db/database.ts";
import { displayUsd } from "../money.ts";
import { OFFERINGS, type AgentProfile, type OfferingInfo } from "./agent.ts";
import { Store } from "./store.ts";

type Row = Record<string, unknown>;
const row = (value: unknown): Row =>
  value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Row)
    : {};
const rows = (value: unknown): Row[] =>
  Array.isArray(value) ? value.map(row) : [];
/** Bounded single-purpose text from an untrusted record. */
const clip = (value: unknown, max: number) =>
  typeof value === "string"
    ? value
        .replace(/[\x00-\x08\x0b-\x1f\x7f]/g, " ")
        .trim()
        .slice(0, max)
    : "";
const iso = (value: unknown) =>
  value === null || value === undefined
    ? null
    : new Date(Number(value)).toISOString();
function httpsUrl(value: unknown): string {
  if (typeof value !== "string" || value.length > 2048) return "";
  try {
    const url = new URL(value);
    return url.protocol === "https:" && !url.username && !url.password
      ? url.href
      : "";
  } catch {
    return "";
  }
}
function emailAddress(value: unknown): string | null {
  return typeof value === "string" &&
    value.length <= 254 &&
    /^[^\s<>@,;:?&#%]+@[^\s<>@,;:?&#%]+\.[^\s<>@,;:?&#%]+$/.test(value)
    ? value.toLowerCase()
    : null;
}
const sold = (profile: AgentProfile) =>
  OFFERINGS.filter((item) => profile.offerings[item.id]);
const price = (profile: AgentProfile, offering: OfferingInfo) =>
  displayUsd(profile.offerings[offering.id] ?? offering.defaultPriceMicros);

/* ---------- order links and attribution ---------- */

/** A short channel tag carried on order links, such as share or lead-12. */
export const REF_PATTERN = /^[a-z0-9][a-z0-9-]{0,39}$/;
const CHANNELS = ["share", "lead", "sample", "repeat", "friend"] as const;
export type Channel = (typeof CHANNELS)[number] | "other";

export function orderLink(
  origin: string,
  options: { service?: string; ref?: string } = {},
): string {
  const url = new URL("/commerce", origin);
  if (options.service) url.searchParams.set("service", options.service);
  if (options.ref) url.searchParams.set("ref", options.ref);
  return url.href;
}
/** Links to a loopback origin only work on the owner's own machine. */
export function localOnly(origin: string): boolean {
  return ["127.0.0.1", "localhost", "[::1]"].includes(new URL(origin).hostname);
}
export function channelOf(ref: string): Channel {
  const head = ref.split("-")[0]!;
  return (CHANNELS as readonly string[]).includes(head)
    ? (head as Channel)
    : "other";
}
/**
 * Remember which link a request came from. This counts requests only; it
 * never creates a customer, an order or revenue. Returns whether it was new.
 */
export function recordAttribution(
  db: DatabaseSync,
  runId: string,
  ref: unknown,
  opportunityId: unknown,
  now = Date.now(),
): boolean {
  if (
    typeof ref !== "string" ||
    !REF_PATTERN.test(ref) ||
    typeof opportunityId !== "string" ||
    !/^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$/.test(opportunityId)
  )
    return false;
  const result = db
    .prepare(
      "INSERT OR IGNORE INTO growth_attributions(run_id,ref,opportunity_id,created_at) VALUES (?,?,?,?)",
    )
    .run(runId, ref, opportunityId, now);
  return Number(result.changes) > 0;
}
export function attributionCounts(
  db: DatabaseSync,
  runId: string,
): Record<Channel | "total", number> {
  const counts: Record<Channel | "total", number> = {
    share: 0,
    lead: 0,
    sample: 0,
    repeat: 0,
    friend: 0,
    other: 0,
    total: 0,
  };
  for (const item of db
    .prepare(
      "SELECT ref,COUNT(*) AS n FROM growth_attributions WHERE run_id=? GROUP BY ref",
    )
    .all(runId) as { ref: string; n: number }[]) {
    counts[channelOf(item.ref)] += Number(item.n);
    counts.total += Number(item.n);
  }
  return counts;
}

/**
 * Requests per customer referral code, so the owner can check a customer's
 * claim against the code in that customer's own link.
 */
export function referralCounts(
  db: DatabaseSync,
  runId: string,
): { ref: string; requests: number }[] {
  return (
    db
      .prepare(
        "SELECT ref,COUNT(*) AS n FROM growth_attributions WHERE run_id=? AND ref LIKE 'friend-%' GROUP BY ref ORDER BY n DESC,ref LIMIT 50",
      )
      .all(runId) as { ref: string; n: number }[]
  ).map((item) => ({ ref: item.ref, requests: Number(item.n) }));
}

/* ---------- lead inbox ---------- */

export type LeadState = "new" | "contacted" | "dismissed";
export interface Lead {
  id: number;
  service: string;
  kind: string;
  need: string;
  excerpt: string;
  sourceUrl: string;
  contact: string | null;
  draft: string;
  state: LeadState;
  createdAt: string;
  decidedAt: string | null;
}
const MAX_DRAFT = 4000;

/** The reply the owner starts from. Fixed wording; no model is involved. */
export function draftReply(input: {
  profile: AgentProfile;
  offering: OfferingInfo;
  need: string;
  sourceUrl: string;
  link: string;
}): string {
  const { profile, offering } = input;
  const about = input.need ? ` about "${input.need}"` : "";
  const where = input.sourceUrl ? ` (${input.sourceUrl})` : "";
  return [
    "Hi,",
    "",
    `I came across your request${about}${where} and think I can help.`,
    "",
    `I run ${profile.name}, a small service that can make you ${offering.phrase}. The standard price is ${price(profile, offering)}. You get an exact quote first and pay only if you accept it; delivery is private.`,
    "",
    "You can describe what you need here, with no obligation:",
    input.link,
    "",
    `How this works: ${profile.name} is an automated agent, and a person reviewed this message before sending it. If this isn't relevant, ignore it and you won't hear from me again.`,
  ].join("\n");
}

/**
 * Copy discoveries the business service already recorded into the inbox,
 * for offerings the agent sells. Existing leads are left untouched, so an
 * edited, sent or dismissed lead is never re-drafted. Returns how many were
 * added.
 */
export function syncLeads(input: {
  db: DatabaseSync;
  runId: string;
  status: unknown;
  profile: AgentProfile;
  origin: string;
  now?: number;
}): number {
  const { db, runId, profile } = input;
  const now = input.now ?? Date.now();
  const data = row(input.status);
  const markets = rows(row(data.config).markets);
  let added = 0;
  for (const item of rows(data.discoveries)) {
    const key = clip(item.opportunity_id, 200);
    if (!key) continue;
    const market = markets.find((entry) => entry.id === item.market_id);
    const offering = sold(profile).find(
      (entry) => entry.id === market?.service,
    );
    if (!offering) continue;
    const evidence = row(
      Array.isArray(item.evidence) ? item.evidence[0] : item.evidence,
    );
    const need =
      clip(evidence.matched_term, 200) || clip(item.observed_need, 200);
    const sourceUrl = httpsUrl(evidence.url);
    const kind = item.kind === "lead" ? "lead" : "experiment";
    transaction(db, () => {
      const inserted = db
        .prepare(
          "INSERT OR IGNORE INTO growth_leads(run_id,source_key,service,kind,need,excerpt,source_url,contact,draft,state,created_at) VALUES (?,?,?,?,?,?,?,?,'','new',?)",
        )
        .run(
          runId,
          key,
          offering.id,
          kind,
          need,
          clip(evidence.excerpt, 1000),
          sourceUrl,
          kind === "lead" ? emailAddress(market?.lead_email) : null,
          now,
        );
      if (!Number(inserted.changes)) return;
      const id = Number(inserted.lastInsertRowid);
      db.prepare("UPDATE growth_leads SET draft=? WHERE id=?").run(
        draftReply({
          profile,
          offering,
          need,
          sourceUrl,
          link: orderLink(input.origin, {
            service: offering.id,
            ref: `lead-${id}`,
          }),
        }),
        id,
      );
      added += 1;
    });
  }
  return added;
}

const leadFrom = (item: Row): Lead => ({
  id: Number(item.id),
  service: String(item.service),
  kind: String(item.kind),
  need: String(item.need),
  excerpt: String(item.excerpt),
  sourceUrl: String(item.source_url),
  contact: typeof item.contact === "string" ? item.contact : null,
  draft: String(item.draft),
  state: item.state as LeadState,
  createdAt: iso(item.created_at)!,
  decidedAt: iso(item.decided_at),
});

export function listLeads(db: DatabaseSync, runId: string): Lead[] {
  return (
    db
      .prepare(
        "SELECT * FROM growth_leads WHERE run_id=? ORDER BY CASE state WHEN 'new' THEN 0 WHEN 'contacted' THEN 1 ELSE 2 END,id DESC LIMIT 200",
      )
      .all(runId) as Row[]
  ).map(leadFrom);
}

export function countNewLeads(db: DatabaseSync, runId: string) {
  return Number(
    (
      db
        .prepare(
          "SELECT COUNT(*) AS n FROM growth_leads WHERE run_id=? AND state='new'",
        )
        .get(runId) as { n: number }
    ).n,
  );
}

export type LeadAction = "save" | "contacted" | "dismiss" | "reopen";
/**
 * Owner decisions on a lead. Marking one as contacted is final, and a
 * contact address can be marked once per run, so nobody is written twice.
 * A lead that names an address is only marked as contacted when the list of
 * people who asked not to be contacted was read and does not include it.
 */
export function updateLead(input: {
  db: DatabaseSync;
  runId: string;
  id: number;
  action: LeadAction;
  draft?: unknown;
  /** Lowercase opted-out addresses; null or absent when they could not be read. */
  optedOut?: ReadonlySet<string> | null;
  now?: number;
}): Lead {
  const { db, runId, id } = input;
  const now = input.now ?? Date.now();
  return transaction(db, () => {
    const current = db
      .prepare("SELECT * FROM growth_leads WHERE id=? AND run_id=?")
      .get(id, runId) as Row | undefined;
    if (!current) throw new Error("Unknown lead");
    const state = current.state as LeadState;
    if (input.action === "reopen") {
      if (state !== "dismissed")
        throw new Error("Only a dismissed lead can be reopened");
      db.prepare(
        "UPDATE growth_leads SET state='new',decided_at=NULL WHERE id=?",
      ).run(id);
    } else {
      if (state !== "new") throw new Error("This lead is already decided");
      if (input.draft !== undefined) {
        // Browsers submit CRLF line endings; count the text as it is stored.
        const draft =
          typeof input.draft === "string"
            ? input.draft.replace(/\r\n?/g, "\n").trim()
            : "";
        if (
          !draft ||
          draft.length > MAX_DRAFT ||
          /[\x00-\x08\x0b-\x1f\x7f]/.test(draft)
        )
          throw new Error(
            `The reply is required (at most ${MAX_DRAFT} characters)`,
          );
        db.prepare("UPDATE growth_leads SET draft=? WHERE id=?").run(draft, id);
      }
      if (input.action === "contacted") {
        if (typeof current.contact === "string") {
          if (!input.optedOut)
            throw new Error(
              "Opt-outs could not be checked, so this lead was not marked as sent. Start the business service and try again",
            );
          if (input.optedOut.has(current.contact))
            throw new Error(
              "This person asked not to be contacted. Dismiss the lead instead",
            );
        }
        if (
          typeof current.contact === "string" &&
          db
            .prepare(
              "SELECT 1 FROM growth_leads WHERE run_id=? AND contact=? AND state='contacted' AND id<>?",
            )
            .get(runId, current.contact, id)
        )
          throw new Error(
            "This address was already contacted; it is not written twice",
          );
        db.prepare(
          "UPDATE growth_leads SET state='contacted',decided_at=? WHERE id=?",
        ).run(now, id);
        new Store(db).event(
          runId,
          "growth.lead.contacted",
          `Operator sent a reviewed reply to lead ${id}`,
          now,
        );
      } else if (input.action === "dismiss")
        db.prepare(
          "UPDATE growth_leads SET state='dismissed',decided_at=? WHERE id=?",
        ).run(now, id);
    }
    return leadFrom(
      db.prepare("SELECT * FROM growth_leads WHERE id=?").get(id) as Row,
    );
  });
}

/** A mail-app link for a lead that names a contact address. */
export function mailtoLink(lead: Lead, profile: AgentProfile): string | null {
  if (!lead.contact) return null;
  const query = new URLSearchParams({
    subject: `About your request${lead.need ? `: ${lead.need}` : ""} (${profile.name})`,
    body: lead.draft,
  })
    .toString()
    .replace(/\+/g, "%20");
  return `mailto:${encodeURIComponent(lead.contact)}?${query}`;
}

/* ---------- share kit ---------- */

export interface ShareItem {
  offering: OfferingInfo;
  price: string;
  link: string;
  card: string;
  posts: { label: string; text: string }[];
}
export function shareKit(profile: AgentProfile, origin: string): ShareItem[] {
  return sold(profile).map((offering) => {
    const amount = price(profile, offering);
    const link = orderLink(origin, { service: offering.id, ref: "share" });
    return {
      offering,
      price: amount,
      link,
      card: `/share/${offering.id}.svg`,
      posts: [
        {
          label: "Short post",
          text: `${profile.name} will make you ${offering.phrase} for ${amount}. Describe what you need, get an exact quote, and pay only if you accept it: ${link}`,
        },
        {
          label: "Longer post",
          text: [
            `I've set up ${profile.name}, an automated agent that will make you ${offering.phrase}.`,
            "",
            `How it works: you describe what you need, it replies with an exact quote (${amount} as standard), and work starts only after you accept and pay. Delivery is private, through your own link.`,
            "",
            `Try it here: ${link}`,
          ].join("\n"),
        },
        {
          label: "Direct message",
          text: `Hi! I'm running a small service called ${profile.name} that will make you ${offering.phrase} for ${amount}. If you or someone you know needs one, this link goes straight to the request form: ${link}`,
        },
      ],
    };
  });
}

const xml = (value: string) =>
  value.replace(
    /[&<>"']/g,
    (char) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        char
      ]!,
  );
/** A 1200×630 card for one offering; every value is escaped. */
export function shareCardSvg(
  profile: AgentProfile,
  offeringId: string,
  origin: string,
): string | null {
  const offering = sold(profile).find((item) => item.id === offeringId);
  if (!offering) return null;
  const fit = (text: string, largest: number) =>
    Math.max(
      24,
      Math.min(largest, Math.floor(1000 / (Math.max(text.length, 1) * 0.58))),
    );
  const host = new URL(origin).host;
  return `<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="630" viewBox="0 0 1200 630" role="img" aria-label="${xml(`${profile.name}: ${offering.label}, ${price(profile, offering)}`)}"><rect width="1200" height="630" fill="#000"/><rect x="40" y="40" width="1120" height="550" rx="28" fill="#080808" stroke="#242424" stroke-width="2"/><text x="96" y="150" fill="#87a891" font-family="monospace" font-size="22" letter-spacing="4">AN AUTONOMOUS AGENT THAT STARTED WITH $5</text><text x="96" y="270" fill="#ededed" font-family="sans-serif" font-weight="600" font-size="${fit(profile.name, 76)}">${xml(profile.name)}</text><text x="96" y="350" fill="#b9bab6" font-family="sans-serif" font-size="${fit(offering.label, 40)}">${xml(offering.label)}</text><text x="96" y="492" fill="#ededed" font-family="sans-serif" font-weight="500" font-size="64">${xml(price(profile, offering))}</text><text x="96" y="544" fill="#999" font-family="sans-serif" font-size="26">${xml(host)}/commerce</text></svg>`;
}

/* ---------- samples gallery ---------- */

export type SampleKind = "svg" | "html" | "text";
export type SampleState = "draft" | "published" | "retracted";
export interface Sample {
  id: number;
  service: string;
  title: string;
  kind: SampleKind;
  content: string;
  origin: string;
  state: SampleState;
  createdAt: string;
  publishedAt: string | null;
}
/** Also bounds the form body the web server accepts for a sample. */
export const MAX_SAMPLE = 100_000;
const MAX_SAMPLES = 60;

/**
 * Validate sample content. SVG must be a plain drawing: no scripts, event
 * handlers, embedded documents or entity tricks. It is also served with a
 * sandboxing policy, so this is a second barrier, not the only one.
 */
export function checkSample(kind: unknown, content: unknown): string {
  if (kind !== "svg" && kind !== "html" && kind !== "text")
    throw new Error("Sample type must be SVG, HTML or text");
  if (
    typeof content !== "string" ||
    !content.trim() ||
    content.length > MAX_SAMPLE ||
    content.includes("\x00")
  )
    throw new Error(
      `Sample content is required (at most ${MAX_SAMPLE} characters)`,
    );
  if (kind === "svg") {
    // An XML declaration, comments and the plain DOCTYPE that design tools
    // export may come first. A DOCTYPE with an internal subset may not.
    if (
      !/^\s*(<\?xml[^>]*\?>\s*)?(<!--[\s\S]*?-->\s*)*(<!DOCTYPE\s+svg\b[^>[]*>\s*)?(<!--[\s\S]*?-->\s*)*<svg[\s>]/i.test(
        content,
      )
    )
      throw new Error("An SVG sample must start with an <svg> element");
    if (
      /<\s*(script|foreignObject|iframe|object|embed|handler|listener)\b/i.test(
        content,
      ) ||
      // Deliberately broad: text such as "one=" is refused too, because a
      // narrower pattern can be slipped past with a ">" inside an attribute.
      /[\s"'/]on[a-z]+\s*=/i.test(content) ||
      /(javascript|vbscript)\s*:|data\s*:\s*text\/html/i.test(content) ||
      /<!ENTITY|<!DOCTYPE[^>]*\[/i.test(content)
    )
      throw new Error(
        'This SVG contains scripts, event handlers (any "on…=" text), entities or embedded documents and cannot be used',
      );
  }
  return content;
}

/** Map a private prototype from the business service onto a sample. */
export function sampleFromArtifact(artifact: unknown): {
  kind: SampleKind;
  content: string;
  title: string;
} {
  const data = row(artifact);
  const media = clip(data.media_type, 100).toLowerCase().split(";")[0]!;
  const kind: SampleKind | null =
    media === "image/svg+xml"
      ? "svg"
      : media === "text/html"
        ? "html"
        : media.startsWith("text/")
          ? "text"
          : null;
  if (!kind) throw new Error("This prototype cannot be shown as a sample");
  return {
    kind,
    content: checkSample(kind, data.content),
    title: clip(data.filename, 120) || "Sample",
  };
}

export function addSample(input: {
  db: DatabaseSync;
  runId: string;
  service: unknown;
  title: unknown;
  kind: unknown;
  content: unknown;
  origin: string;
  now?: number;
}): number {
  const { db, runId } = input;
  if (!OFFERINGS.some((item) => item.id === input.service))
    throw new Error("Choose which offering this sample shows");
  const title = clip(input.title, 120);
  if (!title) throw new Error("A sample needs a title");
  const content = checkSample(input.kind, input.content);
  if (
    Number(
      (
        db
          .prepare(
            "SELECT COUNT(*) AS n FROM growth_samples WHERE run_id=? AND state<>'retracted'",
          )
          .get(runId) as { n: number }
      ).n,
    ) >= MAX_SAMPLES
  )
    throw new Error(
      `At most ${MAX_SAMPLES} samples; retract one before adding another`,
    );
  return Number(
    db
      .prepare(
        "INSERT INTO growth_samples(run_id,service,title,kind,content,origin,state,created_at) VALUES (?,?,?,?,?,?,'draft',?)",
      )
      .run(
        runId,
        input.service as string,
        title,
        input.kind as string,
        content,
        clip(input.origin, 200) || "owner",
        input.now ?? Date.now(),
      ).lastInsertRowid,
  );
}

const sampleFrom = (item: Row): Sample => ({
  id: Number(item.id),
  service: String(item.service),
  title: String(item.title),
  kind: item.kind as SampleKind,
  content: String(item.content),
  origin: String(item.origin),
  state: item.state as SampleState,
  createdAt: iso(item.created_at)!,
  publishedAt: iso(item.published_at),
});

export function listSamples(
  db: DatabaseSync,
  runId: string,
  publishedOnly = false,
): Sample[] {
  return (
    db
      .prepare(
        `SELECT * FROM growth_samples WHERE run_id=? AND ${publishedOnly ? "state='published'" : "state<>'retracted'"} ORDER BY id DESC LIMIT ${MAX_SAMPLES}`,
      )
      .all(runId) as Row[]
  ).map(sampleFrom);
}
export function countPublishedSamples(db: DatabaseSync, runId: string) {
  return Number(
    (
      db
        .prepare(
          "SELECT COUNT(*) AS n FROM growth_samples WHERE run_id=? AND state='published'",
        )
        .get(runId) as { n: number }
    ).n,
  );
}
/** One draft or published sample, for the owner's preview. */
export function ownerSample(
  db: DatabaseSync,
  runId: string,
  id: number,
): Sample | null {
  const item = db
    .prepare(
      "SELECT * FROM growth_samples WHERE id=? AND run_id=? AND state<>'retracted'",
    )
    .get(id, runId) as Row | undefined;
  return item ? sampleFrom(item) : null;
}
/** One published sample, for the public image and frame routes. */
export function publishedSample(
  db: DatabaseSync,
  runId: string,
  id: number,
): Sample | null {
  const item = db
    .prepare(
      "SELECT * FROM growth_samples WHERE id=? AND run_id=? AND state='published'",
    )
    .get(id, runId) as Row | undefined;
  return item ? sampleFrom(item) : null;
}
/** Publishing and retracting are the only state changes; both are recorded. */
export function setSampleState(input: {
  db: DatabaseSync;
  runId: string;
  id: number;
  action: "publish" | "retract";
  now?: number;
}): void {
  const { db, runId, id } = input;
  const now = input.now ?? Date.now();
  transaction(db, () => {
    const current = db
      .prepare("SELECT state FROM growth_samples WHERE id=? AND run_id=?")
      .get(id, runId) as { state: SampleState } | undefined;
    if (!current || current.state === "retracted")
      throw new Error("Unknown sample");
    if (input.action === "publish") {
      if (current.state !== "draft")
        throw new Error("This sample is already published");
      db.prepare(
        "UPDATE growth_samples SET state='published',published_at=? WHERE id=?",
      ).run(now, id);
    } else
      // Taking a sample down also erases its content, here and in later backups.
      db.prepare(
        "UPDATE growth_samples SET state='retracted',content='' WHERE id=?",
      ).run(id);
    new Store(db).event(
      runId,
      input.action === "publish"
        ? "growth.sample.published"
        : "growth.sample.retracted",
      `Operator ${input.action === "publish" ? "published" : "retracted"} sample ${id}`,
      now,
    );
  });
}

/* ---------- payment-fee estimate ---------- */

export interface FeeModel {
  /** Percentage fee in basis points (349 = 3.49%). */
  bps: number;
  /** Fixed fee per payment in micro-USD, as a string so it serializes. */
  fixedMicros: string;
}
/** An estimate only; the payment provider's own statement is authoritative. */
export function feeModel(env: NodeJS.ProcessEnv = process.env): FeeModel {
  const number = (value: string | undefined, fallback: number, max: number) =>
    value !== undefined &&
    /^(0|[1-9][0-9]{0,8})$/.test(value) &&
    Number(value) <= max
      ? Number(value)
      : fallback;
  return {
    bps: number(env.WIDEMIND_FEE_BPS, 349, 2000),
    fixedMicros: String(
      number(env.WIDEMIND_FEE_FIXED_MICROS, 490_000, 5_000_000),
    ),
  };
}
export function feeEstimate(
  priceMicros: string,
  model: FeeModel,
): { feeMicros: string; keepMicros: string; heavy: boolean } {
  const amount = BigInt(priceMicros);
  // Round the percentage part up to a whole cent, as providers do.
  const percentage =
    ((amount * BigInt(model.bps) + 99_999_999n) / 100_000_000n) * 10_000n;
  const fee = percentage + BigInt(model.fixedMicros);
  return {
    feeMicros: String(fee),
    keepMicros: String(amount - fee),
    heavy: fee * 5n > amount,
  };
}

/* ---------- finished experiment prototypes ---------- */

export interface Prototype {
  experimentId: string;
  title: string;
  filename: string;
  /** The offering the experiment's market targets, when it can be resolved. */
  service: string | null;
}
/** Experiments that produced a private prototype, from a discovery status read. */
export function prototypes(status: unknown): Prototype[] {
  const data = row(status);
  const markets = rows(row(data.config).markets);
  const discoveries = rows(data.discoveries);
  return rows(data.experiments)
    .filter(
      (item) =>
        typeof item.id === "string" &&
        /^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$/.test(item.id) &&
        item.artifact !== null &&
        typeof item.artifact === "object",
    )
    .map((item) => {
      const discovery = discoveries.find(
        (entry) => entry.opportunity_id === item.opportunity_id,
      );
      const market = markets.find((entry) => entry.id === discovery?.market_id);
      return {
        experimentId: item.id as string,
        title: clip(item.hypothesis, 200) || "Experiment prototype",
        filename: clip(row(item.artifact).filename, 120) || "prototype",
        service: OFFERINGS.some((entry) => entry.id === market?.service)
          ? (market!.service as string)
          : null,
      };
    });
}
