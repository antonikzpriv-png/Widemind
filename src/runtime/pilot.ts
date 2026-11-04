/**
 * Milestone 7 pilot tooling: a read-only go-live checklist and an auditable run
 * record. Both read existing state only. They never create runs, grant credit,
 * contact payment providers or certify facts that accounting cannot establish.
 */
import type { DatabaseSync } from "node:sqlite";
import type {
  DeliveryOutcomes,
  FinancialSummary,
  RuntimeConfig,
} from "../contracts/index.ts";
import type { Services } from "../modules/registry.ts";
import { Store } from "./store.ts";
import { backupFresh, backupSettings } from "./backup.ts";
import {
  latestAttestation,
  outsideLedgerCosts,
  type Attestation,
  type OutsideLedgerCosts,
} from "./pilot-records.ts";

export const PILOT_OPENING_MICROS = "5000000";
const RUN_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$/;

export interface ReadinessCheck {
  id: string;
  ok: boolean;
  detail: string;
}
export interface PilotReadiness {
  version: 1;
  observedAt: string;
  ready: boolean;
  checks: ReadinessCheck[];
}

/** Resolve the pilot run from an explicit argument or the commerce run setting. */
export function pilotRunId(
  argv: readonly string[],
  env: NodeJS.ProcessEnv,
): string | null {
  const index = argv.indexOf("--run");
  const value = index >= 0 ? argv[index + 1] : env.WIDEMIND_COMMERCE_RUN_ID;
  return value && RUN_ID.test(value) ? value : null;
}

async function safeDeliveries(
  services: Services,
  runId: string,
): Promise<DeliveryOutcomes | null> {
  if (
    !services.business.deliveryOutcomes ||
    !services.business.health().available
  )
    return null;
  try {
    return await services.business.deliveryOutcomes(runId);
  } catch {
    return null;
  }
}

async function safeSummary(
  services: Services,
  runId: string | null,
): Promise<FinancialSummary | null> {
  if (!runId || !services.economy.health().available) return null;
  try {
    return await services.economy.summary(runId);
  } catch {
    return null;
  }
}

/**
 * Report what still blocks a live pilot. Only the presence of a secret is
 * inspected; no value is printed, and configuration is not proof that a
 * provider account works.
 */
export async function pilotReadiness(input: {
  env: NodeJS.ProcessEnv;
  config: RuntimeConfig;
  operatorPasswordConfigured: boolean;
  services: Services;
  db: DatabaseSync;
  runId: string | null;
  now?: number;
}): Promise<PilotReadiness> {
  const { env, config, services, runId } = input;
  const now = input.now ?? Date.now();
  const store = new Store(input.db);
  const checks: ReadinessCheck[] = [];
  const add = (id: string, ok: boolean, pass: string, fail: string) =>
    checks.push({ id, ok, detail: ok ? pass : fail });

  add(
    "run_configured",
    runId !== null,
    "Pilot run identity configured",
    "Set WIDEMIND_COMMERCE_RUN_ID or pass --run <id>",
  );
  const run = runId ? store.run(runId) : null;
  add(
    "run_exists",
    Boolean(run),
    `Runtime run exists (state ${run?.state})`,
    "Runtime run not found; create it through the operator setup",
  );
  add(
    "worker_available",
    store.runtimeStatus(config.profile, now).available,
    "A persistent worker heartbeat is current",
    "No live worker heartbeat; start npm run worker",
  );
  add(
    "operator_auth",
    input.operatorPasswordConfigured,
    "Operator password hash configured",
    "Set WIDEMIND_OPERATOR_PASSWORD_HASH (npm run operator:hash)",
  );
  add(
    "public_https",
    config.publicOrigin.startsWith("https:") && config.secureCookies,
    "Public origin uses HTTPS with secure cookies",
    "Deploy behind HTTPS: set WIDEMIND_PUBLIC_ORIGIN=https://... and WIDEMIND_SECURE_COOKIES=true",
  );
  add(
    "live_effects_enabled",
    env.WIDEMIND_ENABLE_LIVE_EFFECTS === "true",
    "Live external effects explicitly enabled",
    "Live effects are disabled (WIDEMIND_ENABLE_LIVE_EFFECTS is not true)",
  );
  add(
    "commerce_live",
    env.WIDEMIND_COMMERCE_MODE === "live",
    "Commerce configured for live payments",
    "WIDEMIND_COMMERCE_MODE is not live",
  );
  const provider = env.WIDEMIND_PAYMENT_PROVIDER ?? "paypal";
  const providerKey =
    provider === "stripe"
      ? "WIDEMIND_STRIPE_API_KEY"
      : "WIDEMIND_PAYPAL_CLIENT_ID";
  add(
    "payment_provider",
    ["paypal", "stripe"].includes(provider) && Boolean(env[providerKey]),
    `Payment provider ${provider} credential present`,
    `Payment provider ${provider} credential missing (${providerKey})`,
  );

  const economy = services.economy.health();
  add(
    "economy_available",
    economy.available,
    "Economy module configured (reachability is shown by economy_live_opening)",
    `Economy unavailable: ${economy.available ? "" : economy.reason}`,
  );
  const summary = await safeSummary(services, run ? runId : null);
  add(
    "economy_live_opening",
    summary?.mode === "live" &&
      summary.openingCreditMicros === PILOT_OPENING_MICROS,
    "Run has a live $5.00 opening grant",
    summary
      ? `Run opening is ${summary.mode} with ${summary.openingCreditMicros} micro-USD; a live 5000000 opening is required`
      : "No financial summary for the run; open it with verified live funding",
  );

  try {
    await services.tools.refresh?.();
  } catch {
    // Observation failure is reported through health below.
  }
  const tools = services.tools.health();
  add(
    "tools_available",
    tools.available,
    "Tools service reachable",
    `Tools unavailable: ${tools.available ? "" : tools.reason}`,
  );
  const capabilities = services.tools.capabilities();
  const email = capabilities.filter(
    (item) => item.id.startsWith("gmail.") && item.availability.available,
  );
  add(
    "email_capability",
    email.length > 0,
    `Email capability available (${email.map((item) => item.id).join(", ")})`,
    "No Gmail capability available; complete dedicated mailbox OAuth",
  );
  let backups: { fresh: boolean; detail: string } | null = null;
  try {
    backups = backupFresh(backupSettings(env), now);
  } catch (error) {
    backups = { fresh: false, detail: (error as Error).message };
  }
  add(
    "backups_recent",
    backups?.fresh === true,
    backups?.detail ?? "",
    backups?.detail ?? "Set WIDEMIND_BACKUP_DIR and schedule npm run backup",
  );
  const business = services.business.health();
  add(
    "business_available",
    business.available,
    "Business module available",
    `Business unavailable: ${business.available ? "" : business.reason}`,
  );

  return {
    version: 1,
    observedAt: new Date(now).toISOString(),
    ready: checks.every((check) => check.ok),
    checks,
  };
}

export type CriterionStatus =
  | "met"
  | "not_met"
  | "unknown"
  | "attestation_required";
export interface Criterion {
  id: string;
  /** Required criteria decide the milestone verdict. */
  required: boolean;
  status: CriterionStatus;
  detail: string;
}
export interface PilotOutcomes {
  grossCustomerReceiptsMicros: string;
  processorFeesMicros: string | null;
  refundsMicros: string | null;
  chargebacksMicros: string | null;
  chargebackReversalsMicros: string | null;
  netCustomerReceiptsMicros: string | null;
  operatingChargesMicros: string;
  netOperatingResultMicros: string | null;
  earnedRevenueMicros: string;
  spendableMicros: string;
  reservedMicros: string;
  holdsMicros: string;
  exhausted: boolean;
}
export interface OutsideSupport {
  openingCreditMicros: string;
  ownerFundingMicros: string;
  testFundingMicros: string | null;
  /** Noncash infrastructure subsidy; it never replenishes spendable cash. */
  subsidiesMicros: string;
}
export interface PilotReport {
  version: 1;
  verificationScope: "local_records_only";
  generatedAt: string;
  run: {
    runId?: string;
    state: string;
    createdAt: string;
    updatedAt: string;
  };
  accounting: {
    currency: "USD";
    unit: "micro-USD";
    mode: "live" | "sandbox";
    asOf: string;
  } | null;
  interventions: {
    total: number;
    byKind: Record<string, number>;
    events?: { kind: string; at: string }[];
  };
  activity: {
    actions: number;
    attempts: number;
    byState: Record<string, number>;
    byTool?: Record<string, number>;
  };
  outcomes: PilotOutcomes | null;
  outsideSupport: OutsideSupport | null;
  criteria: Criterion[];
  attestation: {
    status: "affirmed" | "withdrawn" | "none";
    recordedAt: string | null;
    evidenceRef?: string;
    note?: string;
  };
  outsideLedgerCosts: OutsideLedgerCosts;
  /** Aggregate order outcomes from the business service; null when unavailable. */
  deliveries: DeliveryOutcomes | null;
  /** Net operating result minus outside-ledger costs; null when net is unknown. */
  resultAfterOutsideCostsMicros: string | null;
  verdict: "met" | "not_met" | "pending_attestation";
  limitations: string[];
}

const LIMITATIONS = [
  "Figures come from the local runtime and the economy service's accounting; they are not a bank or provider reconciliation.",
  "Customer independence and costs outside the ledger are operator-recorded facts, not verified by accounting.",
  "Costs outside the ledger are complete only if the operator recorded every hosting, domain, subscription and tooling bill.",
  "Useful work is approximated by paid deliveries that were not fully refunded; customer satisfaction itself is not recorded.",
];

function sumKnown(values: (string | undefined)[]): bigint | null {
  if (values.some((value) => value === undefined)) return null;
  return values.reduce((total, value) => total + BigInt(value!), 0n);
}

export function pilotOutcomes(summary: FinancialSummary): {
  outcomes: PilotOutcomes;
  outsideSupport: OutsideSupport;
} {
  const deductions = sumKnown([
    summary.processorFeesMicros,
    summary.refundsMicros,
    summary.chargebacksMicros,
  ]);
  const net =
    deductions === null
      ? null
      : BigInt(summary.customerReceiptsMicros) -
        deductions +
        BigInt(summary.chargebackReversalsMicros ?? "0");
  return {
    outcomes: {
      grossCustomerReceiptsMicros: summary.customerReceiptsMicros,
      processorFeesMicros: summary.processorFeesMicros ?? null,
      refundsMicros: summary.refundsMicros ?? null,
      chargebacksMicros: summary.chargebacksMicros ?? null,
      chargebackReversalsMicros: summary.chargebackReversalsMicros ?? null,
      netCustomerReceiptsMicros: net === null ? null : String(net),
      operatingChargesMicros: summary.operatingChargesMicros,
      netOperatingResultMicros:
        net === null
          ? null
          : String(net - BigInt(summary.operatingChargesMicros)),
      earnedRevenueMicros: summary.earnedRevenueMicros,
      spendableMicros: summary.spendableMicros,
      reservedMicros: summary.reservedMicros,
      holdsMicros: summary.holdsMicros,
      exhausted: BigInt(summary.spendableMicros) <= 0n,
    },
    outsideSupport: {
      openingCreditMicros: summary.openingCreditMicros,
      ownerFundingMicros: summary.ownerFundingMicros,
      testFundingMicros: summary.testFundingMicros ?? null,
      subsidiesMicros: summary.subsidiesMicros,
    },
  };
}

export interface CriteriaContext {
  attestation: Attestation | null;
  outsideCosts: OutsideLedgerCosts;
  deliveries?: DeliveryOutcomes | null;
}
const NO_CONTEXT: CriteriaContext = {
  attestation: null,
  outsideCosts: { entries: 0, totalMicros: "0", byCategory: {} },
};

function deliveryCriteria(
  deliveries: DeliveryOutcomes | null | undefined,
): Criterion[] {
  if (!deliveries)
    return [
      {
        id: "paid_work_delivered",
        required: true,
        status: "unknown",
        detail: "Business delivery records are unavailable",
      },
    ];
  return [
    {
      id: "paid_work_delivered",
      required: true,
      status: deliveries.paidDeliveredNotRefunded > 0 ? "met" : "not_met",
      detail: `${deliveries.paidDeliveredNotRefunded} paid order(s) delivered and not fully refunded; ${deliveries.deliveredFullyRefunded} delivered but fully refunded`,
    },
    {
      id: "no_overdue_deliveries",
      required: false,
      status: deliveries.overdue === 0 ? "met" : "not_met",
      detail: `${deliveries.overdue} open delivery obligation(s) past due`,
    },
  ];
}

function independence(attestation: Attestation | null): Criterion {
  return attestation?.stance === "affirmed"
    ? {
        id: "independent_customer",
        required: true,
        status: "met",
        detail: `Operator attested customer independence on ${attestation.recordedAt.slice(0, 10)}; evidence is held privately`,
      }
    : {
        id: "independent_customer",
        required: true,
        status: "attestation_required",
        detail: attestation
          ? "The latest independence attestation was withdrawn"
          : "Accounting cannot show that the payer is independent of the operator; record an attestation with npm run pilot:attest",
      };
}

/** Net operating result after costs the operator recorded outside the ledger. */
export function resultAfterOutsideCosts(
  outcomes: PilotOutcomes | null,
  outsideCosts: OutsideLedgerCosts,
): string | null {
  if (!outcomes || outcomes.netOperatingResultMicros === null) return null;
  return String(
    BigInt(outcomes.netOperatingResultMicros) -
      BigInt(outsideCosts.totalMicros),
  );
}

export function pilotCriteria(
  summary: FinancialSummary | null,
  context: CriteriaContext = NO_CONTEXT,
): Criterion[] {
  if (!summary)
    return [
      {
        id: "accounting_available",
        required: true,
        status: "not_met",
        detail: "No financial summary is available for the run",
      },
      independence(context.attestation),
      ...deliveryCriteria(context.deliveries),
    ];
  const { outcomes } = pilotOutcomes(summary);
  const criterion = (
    id: string,
    status: CriterionStatus,
    detail: string,
    required = true,
  ): Criterion => ({ id, required, status, detail });
  const net = outcomes.netCustomerReceiptsMicros;
  const afterCosts = resultAfterOutsideCosts(outcomes, context.outsideCosts);
  return [
    criterion(
      "live_mode",
      summary.mode === "live" ? "met" : "not_met",
      `Accounting mode is ${summary.mode}`,
    ),
    criterion(
      "opening_exactly_5usd",
      summary.openingCreditMicros === PILOT_OPENING_MICROS ? "met" : "not_met",
      `Opening credit is ${summary.openingCreditMicros} micro-USD`,
    ),
    criterion(
      "no_test_funding",
      summary.testFundingMicros === undefined
        ? "unknown"
        : summary.testFundingMicros === "0"
          ? "met"
          : "not_met",
      summary.testFundingMicros === undefined
        ? "Economy did not report test funding"
        : `Test funding is ${summary.testFundingMicros} micro-USD`,
    ),
    criterion(
      "customer_receipts",
      BigInt(summary.customerReceiptsMicros) > 0n ? "met" : "not_met",
      `Verified customer receipts are ${summary.customerReceiptsMicros} micro-USD`,
    ),
    independence(context.attestation),
    criterion(
      "delivery_costs_covered",
      net === null
        ? "unknown"
        : BigInt(net) >= BigInt(summary.operatingChargesMicros) &&
            BigInt(net) > 0n
          ? "met"
          : "not_met",
      net === null
        ? "Fees, refunds or chargebacks were not reported, so net receipts are unknown"
        : `Net customer receipts ${net} against operating charges ${summary.operatingChargesMicros} micro-USD`,
    ),
    criterion(
      "no_owner_funding",
      summary.ownerFundingMicros === "0" ? "met" : "not_met",
      `Owner funding after opening is ${summary.ownerFundingMicros} micro-USD`,
    ),
    criterion(
      "operating_costs_covered",
      context.outsideCosts.entries === 0 || afterCosts === null
        ? "unknown"
        : BigInt(afterCosts) >= 0n
          ? "met"
          : "not_met",
      context.outsideCosts.entries === 0
        ? "No costs outside the ledger were recorded; record hosting and subscriptions with npm run pilot:cost"
        : afterCosts === null
          ? "Net receipts are unknown"
          : `Result after ${context.outsideCosts.totalMicros} micro-USD of outside costs is ${afterCosts} micro-USD`,
      false,
    ),
    ...deliveryCriteria(context.deliveries),
  ];
}

/** Only required criteria decide the verdict; profitability is reported separately. */
function verdict(criteria: Criterion[]): PilotReport["verdict"] {
  const required = criteria.filter((item) => item.required);
  if (required.every((item) => item.status === "met")) return "met";
  return required.every(
    (item) => item.status === "met" || item.status === "attestation_required",
  )
    ? "pending_attestation"
    : "not_met";
}

function increment(record: Record<string, number>, key: string) {
  record[key] = (record[key] ?? 0) + 1;
}

/**
 * Build the auditable run record. The public view omits the run identity,
 * per-event timestamps and tool names; the private view is for the operator.
 */
export async function pilotReport(input: {
  db: DatabaseSync;
  services: Services;
  runId: string;
  view: "private" | "public";
  now?: number;
}): Promise<PilotReport> {
  const { db, services, runId, view } = input;
  const now = input.now ?? Date.now();
  const run = new Store(db).run(runId);
  if (!run) throw new Error("Pilot run not found");
  const isPublic = view === "public";

  const events = db
    .prepare(
      "SELECT kind,at FROM runtime_events WHERE run_id=? AND kind LIKE 'operator.%' ORDER BY id",
    )
    .all(runId) as { kind: string; at: number }[];
  const byKind: Record<string, number> = {};
  for (const event of events) increment(byKind, event.kind);

  const actions = db
    .prepare(
      "SELECT state,attempts,action_json FROM runtime_actions WHERE run_id=?",
    )
    .all(runId) as { state: string; attempts: number; action_json: string }[];
  const byState: Record<string, number> = {};
  const byTool: Record<string, number> = {};
  let attempts = 0;
  for (const action of actions) {
    increment(byState, action.state);
    attempts += Number(action.attempts);
    let tool = "unknown";
    try {
      const parsed = JSON.parse(action.action_json) as { tool?: unknown };
      if (typeof parsed.tool === "string" && parsed.tool.length <= 100)
        tool = parsed.tool;
    } catch {
      // Malformed historical action records are counted as unknown.
    }
    increment(byTool, tool);
  }

  const summary = await safeSummary(services, runId);
  const money = summary ? pilotOutcomes(summary) : null;
  const attestation = latestAttestation(db, runId);
  const outsideCosts = outsideLedgerCosts(db, runId, !isPublic);
  const deliveries = await safeDeliveries(services, runId);
  const criteria = pilotCriteria(summary, {
    attestation,
    outsideCosts,
    deliveries,
  });
  return {
    version: 1,
    verificationScope: "local_records_only",
    generatedAt: new Date(now).toISOString(),
    run: {
      ...(isPublic ? {} : { runId }),
      state: run.state,
      createdAt: new Date(run.created_at).toISOString(),
      updatedAt: new Date(run.updated_at).toISOString(),
    },
    accounting: summary
      ? {
          currency: "USD",
          unit: "micro-USD",
          mode: summary.mode,
          asOf: summary.asOf,
        }
      : null,
    interventions: {
      total: events.length,
      byKind,
      ...(isPublic
        ? {}
        : {
            events: events.map((event) => ({
              kind: event.kind,
              at: new Date(event.at).toISOString(),
            })),
          }),
    },
    activity: {
      actions: actions.length,
      attempts,
      byState,
      ...(isPublic ? {} : { byTool }),
    },
    outcomes: money?.outcomes ?? null,
    outsideSupport: money?.outsideSupport ?? null,
    attestation: {
      status: attestation?.stance ?? "none",
      recordedAt: attestation?.recordedAt ?? null,
      ...(isPublic || !attestation
        ? {}
        : { evidenceRef: attestation.evidenceRef, note: attestation.note }),
    },
    outsideLedgerCosts: outsideCosts,
    deliveries,
    resultAfterOutsideCostsMicros: resultAfterOutsideCosts(
      money?.outcomes ?? null,
      outsideCosts,
    ),
    criteria,
    verdict: verdict(criteria),
    limitations: LIMITATIONS,
  };
}
