/**
 * The agent the owner creates: a name, a mission, the offerings it sells,
 * and its spending limits. Profiles are versioned (append-only); the latest
 * applies. Saving a profile maps it onto the business service's policy.
 * The first-hour plan is proposed from these settings and the tools that are
 * actually available, without calling a model, and only runs once approved.
 */
import type { DatabaseSync } from "node:sqlite";
import type { Json, RuntimeConfig } from "../contracts/index.ts";
import type { Services } from "../modules/registry.ts";
import { transaction } from "../db/database.ts";
import { Store } from "./store.ts";
import { usdToMicros } from "./pilot-records.ts";

export interface OfferingInfo {
  id: string;
  label: string;
  capability: string;
  /** How the offering reads in a sentence ("an SVG graphic"). */
  phrase: string;
  defaultPriceMicros: string;
  needTerms: string[];
  /** Example scope used when the plan proposes a discovery market. */
  exampleScope: Record<string, Json>;
}
export const OFFERINGS: readonly OfferingInfo[] = [
  {
    id: "research-brief",
    label: "Sourced research brief",
    capability: "research",
    phrase: "a sourced research brief",
    defaultPriceMicros: "2000000",
    needTerms: [
      "research",
      "report",
      "summary",
      "comparison",
      "market overview",
    ],
    exampleScope: {
      topic: "Requested topic",
      sections: ["Overview", "Findings"],
      min_sources: 2,
    },
  },
  {
    id: "svg-graphic",
    label: "SVG graphic (logo, icon, badge)",
    capability: "graphics",
    phrase: "an SVG graphic",
    defaultPriceMicros: "2000000",
    needTerms: ["logo", "icon", "svg", "badge", "illustration"],
    exampleScope: {
      title: "Requested graphic",
      description: "A simple SVG graphic",
    },
  },
  {
    id: "software-starter",
    label: "Static HTML starter page",
    capability: "software",
    phrase: "a simple web page",
    defaultPriceMicros: "3000000",
    needTerms: ["landing page", "html", "website", "one page site"],
    exampleScope: {
      title: "Requested page",
      description: "A static HTML starter page",
    },
  },
];

export interface AgentProfile {
  name: string;
  mission: string;
  /** Offering id → price in micro-USD. */
  offerings: Record<string, string>;
  experimentBudgetMicros: string;
  maxJobMicros: string;
  allowOutbound: boolean;
}
export interface StoredProfile extends AgentProfile {
  version: number;
  savedAt: string;
}

function text(value: unknown, name: string, max: number): string {
  if (
    typeof value !== "string" ||
    !value.trim() ||
    value.length > max ||
    /[\x00-\x08\x0b-\x1f\x7f]/.test(value)
  )
    throw new Error(`${name} is required (at most ${max} characters)`);
  return value.trim();
}

/** Validate owner input (USD strings from a form) into a profile. */
export function profileFromInput(input: Record<string, unknown>): AgentProfile {
  const name = text(input.name, "Name", 60);
  const mission = text(input.mission, "Mission", 500);
  const offerings: Record<string, string> = {};
  for (const offering of OFFERINGS) {
    const chosen = input[`offer_${offering.id}`];
    if (chosen !== "on" && chosen !== "true" && chosen !== true) continue;
    const price = input[`price_${offering.id}`];
    const micros =
      typeof price === "string" && price.trim()
        ? usdToMicros(price.trim())
        : offering.defaultPriceMicros;
    if (BigInt(micros) < 1_000_000n)
      throw new Error(`${offering.label} must cost at least $1.00`);
    if (BigInt(micros) % 10_000n) throw new Error("Prices must be whole cents");
    offerings[offering.id] = micros;
  }
  if (!Object.keys(offerings).length)
    throw new Error("Choose at least one offering");
  const budget = (key: string, fallback: string) => {
    const value = input[key];
    return typeof value === "string" && value.trim()
      ? usdToMicros(value.trim())
      : fallback;
  };
  const experimentBudgetMicros = budget("experimentBudget", "1000000");
  const maxJobMicros = budget("maxJob", "250000");
  if (
    BigInt(experimentBudgetMicros) > 5_000_000n ||
    BigInt(maxJobMicros) > 5_000_000n
  )
    throw new Error("Spending limits cannot exceed the $5.00 opening credit");
  return {
    name,
    mission,
    offerings,
    experimentBudgetMicros,
    maxJobMicros,
    allowOutbound:
      input.allowOutbound === "on" ||
      input.allowOutbound === "true" ||
      input.allowOutbound === true,
  };
}

export function currentProfile(
  db: DatabaseSync,
  runId: string,
): StoredProfile | null {
  const row = db
    .prepare(
      "SELECT id,document,created_at FROM agent_profiles WHERE run_id=? ORDER BY id DESC LIMIT 1",
    )
    .get(runId) as
    | { id: number; document: string; created_at: number }
    | undefined;
  if (!row) return null;
  return {
    ...(JSON.parse(row.document) as AgentProfile),
    version: Number(row.id),
    savedAt: new Date(Number(row.created_at)).toISOString(),
  };
}

/**
 * Create the agent's run under the deployment's configured run ID (from
 * services:configure), or save a new profile version for an existing agent.
 */
export function saveAgent(input: {
  db: DatabaseSync;
  config: RuntimeConfig;
  runId: string;
  profile: AgentProfile;
  now?: number;
}): { created: boolean; profile: StoredProfile } {
  const now = input.now ?? Date.now();
  const store = new Store(input.db);
  const created = !store.run(input.runId);
  // A crash between these steps leaves a run without a profile; saving again completes it.
  if (created)
    store.create(
      input.config.profile,
      now,
      input.runId as `${string}-${string}-${string}-${string}-${string}`,
    );
  return transaction(input.db, () => {
    input.db
      .prepare(
        "INSERT INTO agent_profiles(run_id,document,created_at) VALUES (?,?,?)",
      )
      .run(input.runId, JSON.stringify(input.profile), now);
    store.event(
      input.runId,
      created ? "agent.created" : "agent.updated",
      `Operator ${created ? "created" : "updated"} the agent profile`,
      now,
    );
    return { created, profile: currentProfile(input.db, input.runId)! };
  });
}

/** Map a profile onto the business policy, keeping every other policy field. */
export function policyFromProfile(
  profile: AgentProfile,
  existing: Record<string, Json> | null,
): Record<string, Json> {
  const chosen = OFFERINGS.filter((item) => profile.offerings[item.id]);
  return {
    ...(existing ?? {}),
    goals: [profile.mission],
    capabilities: [...new Set(chosen.map((item) => item.capability))],
    prices: Object.fromEntries(
      chosen.map((item) => [item.id, Number(profile.offerings[item.id])]),
    ),
    goal_weights: Object.fromEntries(chosen.map((item) => [item.id, 1])),
    experiment_budget_micro_usd: Number(profile.experimentBudgetMicros),
    max_job_micro_usd: Number(profile.maxJobMicros),
    allow_outbound: profile.allowOutbound,
    // Negotiation floors must refer to offerings still sold, within their price.
    conversation_price_floors: Object.fromEntries(
      Object.entries(
        (existing?.conversation_price_floors as
          | Record<string, Json>
          | undefined) ?? {},
      ).filter(
        ([id, floor]) =>
          profile.offerings[id] !== undefined &&
          typeof floor === "number" &&
          floor <= Number(profile.offerings[id]),
      ),
    ),
  };
}

/** Push the profile to the business service; reports why when it cannot. */
export async function applyProfile(
  services: Services,
  runId: string,
  profile: AgentProfile,
): Promise<{ applied: boolean; detail: string }> {
  const business = services.business;
  if (
    !business.readPolicy ||
    !business.writePolicy ||
    !business.health().available
  )
    return {
      applied: false,
      detail:
        "The business service is not connected; settings will apply once it is",
    };
  try {
    const existing = await business.readPolicy(runId);
    await business.writePolicy(runId, policyFromProfile(profile, existing));
    return {
      applied: true,
      detail: "Offerings, prices, mission and limits are active",
    };
  } catch {
    return {
      applied: false,
      detail: "The business service refused the settings; check its status",
    };
  }
}

export interface PlanItem {
  id: string;
  title: string;
  detail: string;
  available: boolean;
  /** Why the item cannot run yet, when unavailable. */
  blocker?: string;
  /** For discovery: the offering it looks for buyers of. */
  offeringId?: string;
}
export interface FirstHourPlan {
  generatedFrom: "profile-and-available-tools";
  items: PlanItem[];
}

/** Propose the agent's first moves from its profile and the tools it can actually use. */
export async function proposeFirstPlan(input: {
  services: Services;
  profile: AgentProfile;
  runState: string | null;
  funded: boolean;
}): Promise<FirstHourPlan> {
  const { services, profile } = input;
  let live: string[] = [];
  try {
    live = (await services.website.publicContent()).offerings.map(
      (item) => item.id,
    );
  } catch {
    live = [];
  }
  const chosen = OFFERINGS.filter((item) => profile.offerings[item.id]);
  const research = services.tools
    .capabilities()
    .some(
      (item) => item.id.startsWith("research") && item.availability.available,
    );
  const items: PlanItem[] = [];
  for (const offering of chosen) {
    const available = live.includes(offering.id);
    items.push({
      id: `offer:${offering.id}`,
      title: `Sell ${offering.phrase} for ${formatUsd(profile.offerings[offering.id]!)}`,
      detail:
        "Listed on the public site so visitors can request it and pay before work starts.",
      available,
      ...(available
        ? {}
        : { blocker: `The ${offering.capability} tool is not available yet` }),
    });
  }
  // Look for buyers of something the agent can actually deliver now.
  const first = chosen.find((item) => live.includes(item.id)) ?? chosen[0]!;
  items.push({
    id: "discovery",
    title: `Look for people who need ${first.phrase}`,
    detail: `Scans the sources you give for requests matching ${first.needTerms.slice(0, 3).join(", ")} and proposes bounded experiments. Spending is capped at ${formatUsd(minMicros(profile.experimentBudgetMicros, "500000"))}.`,
    available: research,
    offeringId: first.id,
    ...(research
      ? {}
      : { blocker: "Needs the research tool (OpenAI) configured" }),
  });
  items.push({
    id: "start",
    title: "Start working",
    detail:
      "Resumes the agent so it answers requests and runs the approved steps. Waiting never triggers paid model calls.",
    available: input.funded && input.runState !== "stopped",
    ...(input.funded
      ? {}
      : {
          blocker:
            "Fund the opening $5 first (sandbox: npm run pilot:init -- --sandbox)",
        }),
  });
  return { generatedFrom: "profile-and-available-tools", items };
}

function minMicros(a: string, b: string): string {
  return BigInt(a) < BigInt(b) ? a : b;
}
export function formatUsd(micros: string): string {
  const value = BigInt(micros);
  const cents = (value + 5_000n) / 10_000n;
  return `$${cents / 100n}.${String(cents % 100n).padStart(2, "0")}`;
}

/** Validate discovery source URLs the owner provides. */
export function sourceUrls(value: unknown): string[] {
  const urls = (typeof value === "string" ? value : "")
    .split(/[\s,]+/)
    .map((item) => item.trim())
    .filter(Boolean);
  if (urls.length < 1 || urls.length > 5)
    throw new Error("Give one to five source URLs to scan");
  for (const url of urls) {
    const parsed = new URL(url);
    if (parsed.protocol !== "https:" || parsed.username || parsed.password)
      throw new Error("Source URLs must be public HTTPS addresses");
  }
  return urls;
}

/**
 * Apply the approved plan items. Each item reports its own outcome; one
 * failing item does not undo the others. The decision is recorded.
 */
export async function approveFirstPlan(input: {
  db: DatabaseSync;
  services: Services;
  runId: string;
  profile: AgentProfile;
  plan: FirstHourPlan;
  selected: string[];
  sources: string[];
  now?: number;
}): Promise<{ id: string; ok: boolean; detail: string }[]> {
  const now = input.now ?? Date.now();
  const results: { id: string; ok: boolean; detail: string }[] = [];
  const chosen = input.plan.items.filter((item) =>
    input.selected.includes(item.id),
  );
  const offers = chosen.filter((item) => item.id.startsWith("offer:"));
  if (offers.length) {
    const kept = Object.fromEntries(
      Object.entries(input.profile.offerings).filter(([id]) =>
        offers.some((item) => item.id === `offer:${id}`),
      ),
    );
    const applied = await applyProfile(input.services, input.runId, {
      ...input.profile,
      offerings: kept,
    });
    for (const item of offers)
      results.push({
        id: item.id,
        ok: applied.applied,
        detail: applied.detail,
      });
  }
  const discoveryItem = chosen.find((item) => item.id === "discovery");
  if (discoveryItem) {
    // Discovery must target an offering that is being sold after this approval.
    const sold = offers.length
      ? offers.map((item) => item.id.slice("offer:".length))
      : Object.keys(input.profile.offerings);
    const offering =
      OFFERINGS.find(
        (item) =>
          item.id === discoveryItem.offeringId && sold.includes(item.id),
      ) ?? OFFERINGS.find((item) => sold.includes(item.id))!;
    const configure = input.services.business.writeDiscovery;
    if (!configure || !input.services.business.health().available)
      results.push({
        id: "discovery",
        ok: false,
        detail: "The business service is not connected",
      });
    else
      try {
        await configure(
          input.runId,
          discoveryConfig(input.profile, offering, input.sources),
        );
        results.push({
          id: "discovery",
          ok: true,
          detail: `Scanning ${input.sources.length} source(s)`,
        });
      } catch {
        results.push({
          id: "discovery",
          ok: false,
          detail: "The business service refused the discovery settings",
        });
      }
  }
  if (chosen.some((item) => item.id === "start")) {
    const store = new Store(input.db);
    const run = store.run(input.runId);
    if (!run || run.state === "stopped")
      results.push({
        id: "start",
        ok: false,
        detail: "The run is stopped or missing",
      });
    else {
      store.control(input.runId, "resume", now);
      results.push({ id: "start", ok: true, detail: "The agent is running" });
    }
  }
  transaction(input.db, () => {
    input.db
      .prepare(
        "INSERT INTO agent_plans(run_id,proposal,state,result,decided_at) VALUES (?,?,?,?,?)",
      )
      .run(
        input.runId,
        JSON.stringify(input.plan),
        "approved",
        JSON.stringify(results),
        now,
      );
    new Store(input.db).event(
      input.runId,
      "agent.plan.approved",
      `Operator approved ${chosen.length} first-hour step(s)`,
      now,
    );
  });
  return results;
}

/** The discovery settings the plan sends: one bounded market for one offering. */
export function discoveryConfig(
  profile: AgentProfile,
  offering: OfferingInfo,
  sources: string[],
): Record<string, Json> {
  const allowance = Number(minMicros(profile.experimentBudgetMicros, "500000"));
  return {
    enabled: true,
    allowance_micro_usd: allowance,
    markets: [
      {
        id: `first-${offering.id}`,
        label: `${profile.name}: first scan (${offering.label})`,
        need_terms: offering.needTerms,
        source_urls: sources,
        service: offering.id,
        scope: offering.exampleScope,
        kind: "experiment",
        proposed_offering:
          `${offering.label} for ${formatUsd(profile.offerings[offering.id] ?? offering.defaultPriceMicros)}. ${profile.mission}`.slice(
            0,
            2000,
          ),
        research_allowance_micro_usd: allowance,
      },
    ],
  };
}

export function latestPlanDecision(db: DatabaseSync, runId: string) {
  const row = db
    .prepare(
      "SELECT state,result,decided_at FROM agent_plans WHERE run_id=? ORDER BY id DESC LIMIT 1",
    )
    .get(runId) as
    | { state: string; result: string; decided_at: number }
    | undefined;
  return row
    ? {
        state: row.state,
        results: JSON.parse(row.result) as {
          id: string;
          ok: boolean;
          detail: string;
        }[],
        decidedAt: new Date(Number(row.decided_at)).toISOString(),
      }
    : null;
}
