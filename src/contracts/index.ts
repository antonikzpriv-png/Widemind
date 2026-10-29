/** Version 1. Control-plane interfaces; task input is untrusted data. */
export type ID = string;
export type Json =
  | null
  | boolean
  | number
  | string
  | Json[]
  | { [key: string]: Json };
/** Canonical nonnegative integer micro-USD, serialized as a decimal string. */
export type Micros = string;
export interface Money {
  currency: "USD";
  micros: Micros;
}
export function money(micros: string | bigint): Money {
  if (typeof micros !== "string" && typeof micros !== "bigint")
    throw new Error("Money requires a decimal string or bigint");
  const value = String(micros);
  if (!/^(0|[1-9][0-9]*)$/.test(value) || BigInt(value) > 9223372036854775807n)
    throw new Error(
      "Money must be a nonnegative signed-64-bit integer in micro-USD",
    );
  return { currency: "USD", micros: value };
}
export type Availability =
  | { available: true }
  | { available: false; reason: string };
export type RunState =
  | "active"
  | "waiting"
  | "paused"
  | "exhausted"
  | "stopped"
  | "failed";
export type ActionState =
  | "planned"
  | "reserved"
  | "running"
  | "completed"
  | "failed"
  | "unresolved";
export interface BudgetLimit {
  maximum: Money;
  maxTokens?: number;
  maxDurationMs: number;
  maxOutputBytes: number;
}
/** Summary aggregates use canonical decimal strings, with at most 128 digits. */
export interface FinancialSummary {
  currency: "USD";
  openingCreditMicros: Micros;
  postedMicros: string;
  reservedMicros: Micros;
  holdsMicros: Micros;
  spendableMicros: string;
  customerReceiptsMicros: Micros;
  /** Net performance metrics can be negative after compensating corrections. */
  earnedRevenueMicros: string;
  operatingChargesMicros: Micros;
  adjustmentsMicros?: string;
  ownerFundingMicros: Micros;
  subsidiesMicros: string;
  /** Expense sign: a negative processor fee is a verified fee credit. */
  processorFeesMicros?: string;
  refundsMicros?: Micros;
  chargebacksMicros?: Micros;
  /** Wallet sign: positive values increase cash and negative values reduce it. */
  chargebackReversalsMicros?: string;
  paymentAdjustmentsMicros?: string;
  correctionsMicros?: string;
  testFundingMicros?: Micros;
  otherCashMicros?: string;
  mode: "live" | "sandbox";
  asOf: string;
}
export interface Reservation {
  reservationId: ID;
  actionId: ID;
  runId: ID;
  limit: BudgetLimit;
  /** Opaque authorization passed only to the trusted tool gateway; never public. */
  authorization: string;
}
export type Authorization =
  | { kind: "authorized"; reservation: Reservation }
  | { kind: "insufficient"; reason: string }
  | { kind: "unavailable"; reason: string };
export interface Usage {
  actualCharge: Money;
  provider: string;
  accountRef: string;
  externalEventId: string;
  billableFailure: boolean;
  provisional: boolean;
  evidenceRef: string;
  nativeAmount?: {
    currency: string;
    minorUnits: string;
    conversionRef: string;
    rounding: string;
  };
}
export interface ToolReceipt {
  version: 1;
  actionId: ID;
  status: "succeeded" | "failed";
  /** A receipt confirms both the outcome and charge; timeouts are not failures. */
  result: Json;
  usage: Usage;
  externalRef?: string;
}
export type ToolResult =
  | { kind: "receipt"; receipt: ToolReceipt }
  | { kind: "unknown"; reason: string };
export type Reconciliation =
  | ToolResult
  | { kind: "not_performed"; evidenceRef: string }
  | { kind: "unavailable"; reason: string };
export interface Economy {
  health(): Availability;
  /** Never implicitly credit a run. Opening grant requires verified funding. */
  summary(runId: ID): Promise<FinancialSummary | null>;
  /** Idempotent by actionId; atomically reserve before any paid operation. */
  authorize(request: {
    runId: ID;
    actionId: ID;
    tool: string;
    limit: BudgetLimit;
  }): Promise<Authorization>;
  /** Rearm only after definitive, free evidence that no attempt was performed. */
  confirmNotPerformed?(
    reservation: Reservation,
    evidenceRef: string,
  ): Promise<
    | { kind: "renewed" }
    | { kind: "closed" }
    | { kind: "unavailable"; reason: string }
  >;
  /** Idempotent by actionId + provider/account/event ID; includes charged failures. */
  settle(
    reservation: Reservation,
    receipt: ToolReceipt,
  ): Promise<{ kind: "settled" } | { kind: "unavailable"; reason: string }>;
}
export interface ToolAction {
  version: 1;
  actionId: ID;
  runId: ID;
  workKey: string;
  tool: string;
  input: Json;
  limit: BudgetLimit;
  jobId?: ID;
  opportunityId?: ID;
}
export interface Tools {
  /** Free capability observation; never a paid planning path. */
  refresh?(): Promise<void>;
  health(): Availability;
  capabilities(): ReadonlyArray<{ id: string; availability: Availability }>;
  /** Must enforce authorization/maximums, provider idempotency and AbortSignal. */
  execute(
    action: ToolAction,
    reservation: Reservation,
    signal: AbortSignal,
  ): Promise<ToolResult>;
  /** Free observational lookup only. Never resend/re-publish here. */
  reconcile(
    action: ToolAction,
    reservation: Reservation,
  ): Promise<Reconciliation>;
}
export interface Opportunity {
  opportunityId: ID;
  sourceRef: string;
  observedAt: string;
  description: string;
  expectedRevenue: Money | null;
  estimatedCost: Money;
  status: "proposed" | "selected" | "rejected";
}
export interface Order {
  jobId: ID;
  opportunityId?: ID;
  scope: string;
  quotedPrice: Money;
  costAllowance: Money;
  dueAt: string;
  paymentTiming: "prepaid" | "milestone" | "on_delivery";
  status: "proposed" | "accepted" | "delivered" | "cancelled" | "disputed";
}
export interface Candidate {
  /** Stable business-intent key within this run, including intentional attempts. */
  workKey: string;
  tool: string;
  input: Json;
  limit: BudgetLimit;
  jobId?: ID;
  opportunityId?: ID;
}
export interface ActionView {
  action: ToolAction;
  state: ActionState;
  receipt: ToolReceipt | null;
}
export interface RuntimeEvent {
  eventId: ID;
  source: string;
  accountRef: string;
  externalEventId: string;
  kind: string;
  payload: Json;
  receivedAt: string;
}
export interface BusinessSnapshot {
  runId: ID;
  now: string;
  financial: FinancialSummary | null;
  actions: ActionView[];
  capabilities: ReturnType<Tools["capabilities"]>;
  events: RuntimeEvent[];
}
export type WorkSelection = (
  | { kind: "work"; candidate: Candidate }
  | { kind: "wait"; reason: string; wakeAt?: string }
  | { kind: "unavailable"; reason: string }
) & { acknowledgeEventIds?: ID[] };
export interface Business {
  health(): Availability;
  /** Pure, free local selection. Paid planning is itself a proposed tool action. */
  selectWork(snapshot: BusinessSnapshot): Promise<WorkSelection>;
}
export interface PublicRun {
  runId: ID;
  state: RunState;
  profile: "foundation" | "demo";
  createdAt: string;
  updatedAt: string;
  nextWakeAt: string | null;
}
export interface PublicWebsiteData {
  version: 1;
  financialRunId?: ID | null;
  runs: PublicRun[];
  financial: FinancialSummary | null;
  offerings: Array<{ id: string; title: string; description: string }>;
  activity: Array<{ id: string; at: string; text: string }>;
  availability: Availability;
}
export interface Website {
  health(): Availability;
  /** Explicitly curated public content, never raw actions, errors or customer data. */
  publicContent(): Promise<
    Pick<PublicWebsiteData, "offerings" | "activity" | "availability">
  >;
  /** Trusted server renderer; receives only the public projection. */
  renderPublic?(data: PublicWebsiteData): Promise<string>;
}
export interface RuntimeConfig {
  database: string;
  host: string;
  port: number;
  publicOrigin: string;
  /** Explicit commerce identity for public financial publication. */
  publicRunId?: ID;
  profile: "foundation" | "demo";
  secureCookies: boolean;
  leaseMs: number;
  idleMs: number;
  /** Free module calls must not pin a worker indefinitely. */
  dependencyMs?: number;
}
export interface ServerSecrets {
  operatorPasswordHash: string | null;
}
export interface Migration {
  version: number;
  sql: string;
}
export interface ModuleRoute {
  /** Exact namespaced path: /api/modules/<module-id>/<route>. */
  path: string;
  method: "GET" | "POST";
  access: "public" | "operator";
  /** Fixed registered operator page for successful same-origin HTML form posts. */
  operatorRedirect?: string;
  handle(request: {
    body: Json;
    remoteAddress?: string;
    /** Exact bytes for signature verification on public ingress routes only. */
    rawBody?: Uint8Array;
    query: Record<string, string>;
    headers: Record<string, string>;
    operator: boolean;
  }): Promise<{ status: number; body: Json }>;
}
/** Trusted server-rendered private UI; authentication is enforced by the host. */
export interface OperatorPage {
  /** Exact namespaced path: /operator/modules/<module-id>/<page>. */
  path: string;
  render(request: {
    query: Record<string, string>;
    csrf: string;
  }): string | Promise<string>;
}
export interface ModuleRegistration {
  id: string;
  contractVersion: 1;
  migrations?: readonly Migration[];
  economy?: Economy;
  tools?: Tools;
  business?: Business;
  website?: Website;
  routes?: readonly ModuleRoute[];
  operatorPages?: readonly OperatorPage[];
}
