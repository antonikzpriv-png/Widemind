/**
 * Pilot rehearsal: one real order through the running services in sandbox
 * mode, then a cross-check that the report, a snapshot, the public site,
 * alerts and backups all agree. It refuses to run with live money or live
 * effects enabled. The snapshot it takes is never published.
 */
import { randomBytes, randomUUID } from "node:crypto";
import type { DatabaseSync } from "node:sqlite";
import type { RuntimeConfig } from "../contracts/index.ts";
import type { Services } from "../modules/registry.ts";
import { alertSettings, deliver, evaluateConditions } from "./alerts.ts";
import { backupFresh, backupSettings } from "./backup.ts";
import { pilotReport, type PilotReport } from "./pilot.ts";
import { takeSnapshot, verifyChain } from "./pilot-snapshots.ts";

export interface RehearsalCheck {
  id: string;
  status: "passed" | "failed" | "skipped";
  detail: string;
}
export interface RehearsalResult {
  ok: boolean;
  orderStatus: string | null;
  checks: RehearsalCheck[];
}
interface Quote {
  quoteId: string;
  status: string;
}
interface Order {
  orderId: string;
  status: string;
  checkoutUrl?: string;
}
interface CustomerView {
  quotes?: Quote[];
  orders?: Order[];
}

export function rehearsalAllowed(env: NodeJS.ProcessEnv): string | null {
  if (env.WIDEMIND_COMMERCE_MODE === "live")
    return "WIDEMIND_COMMERCE_MODE is live; rehearse in sandbox only";
  if (env.WIDEMIND_ENABLE_LIVE_EFFECTS === "true")
    return "WIDEMIND_ENABLE_LIVE_EFFECTS is true; rehearse with live effects off";
  if (
    env.WIDEMIND_PAYPAL_MODE === "live" ||
    env.WIDEMIND_STRIPE_MODE === "live"
  )
    return "The payment provider is in live mode; rehearse in sandbox only";
  return null;
}

export async function rehearse(input: {
  origin: string;
  email: string;
  service?: string;
  db: DatabaseSync;
  services: Services;
  config: RuntimeConfig;
  env: NodeJS.ProcessEnv;
  runId: string;
  /** Called once with the checkout link; resolves when payment was attempted. */
  onCheckout: (checkoutUrl: string) => Promise<void>;
  timeoutMs?: number;
  pollMs?: number;
  log?: (line: string) => void;
}): Promise<RehearsalResult> {
  const refusal = rehearsalAllowed(input.env);
  if (refusal) throw new Error(refusal);
  const log = input.log ?? (() => {});
  const pollMs = input.pollMs ?? 2000;
  const deadline = Date.now() + (input.timeoutMs ?? 15 * 60_000);
  const checks: RehearsalCheck[] = [];
  const check = (id: string, passed: boolean, detail: string) =>
    checks.push({ id, status: passed ? "passed" : "failed", detail });
  const skip = (id: string, detail: string) =>
    checks.push({ id, status: "skipped", detail });

  async function post<T>(path: string, body: unknown): Promise<T> {
    const response = await fetch(input.origin + path, {
      method: "POST",
      headers: { Origin: input.origin, "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(15_000),
    });
    const value = (await response.json()) as T;
    if (!response.ok) throw new Error(`${path} answered ${response.status}`);
    return value;
  }
  async function until<T>(
    label: string,
    read: () => Promise<T | null | undefined>,
  ): Promise<T> {
    while (Date.now() < deadline) {
      const value = await read().catch(() => null);
      if (value) return value;
      await new Promise((resolve) => setTimeout(resolve, pollMs));
    }
    throw new Error(`Timed out waiting for ${label}`);
  }
  const report = () =>
    pilotReport({
      db: input.db,
      services: input.services,
      runId: input.runId,
      view: "private",
    });
  const delivered = (value: PilotReport) =>
    value.deliveries?.paidDeliveredNotRefunded ?? 0;
  const receipts = (value: PilotReport) =>
    BigInt(value.outcomes?.grossCustomerReceiptsMicros ?? "0");

  const before = await report();
  const token = randomBytes(32).toString("hex");
  log("Placing a rehearsal request");
  await post("/api/modules/business/request", {
    requestId: "rehearsal-" + randomUUID(),
    customerToken: token,
    email: input.email,
    service: input.service ?? "svg-graphic",
    scope: {
      title: "Pilot rehearsal",
      description: "A sandbox order placed by the pilot rehearsal",
    },
  });
  const customer = () =>
    post<CustomerView>("/api/modules/business/customer", { token });
  const quote = await until("a quote", async () =>
    (await customer()).quotes?.find((item) => item.status === "offered"),
  );
  log("Accepting the quote");
  await post("/api/modules/business/accept", { token, quoteId: quote.quoteId });
  const checkout = await until("a checkout link", async () =>
    (await customer()).orders?.find((item) => item.checkoutUrl),
  );
  await input.onCheckout(checkout.checkoutUrl!);
  log("Waiting for payment and delivery");
  const done = await until("delivery", async () =>
    (await customer()).orders?.find(
      (item) =>
        item.orderId === checkout.orderId &&
        ["completed", "failed", "cancelled"].includes(item.status),
    ),
  );
  check(
    "order_completed",
    done.status === "completed",
    `Order finished as ${done.status}`,
  );
  if (done.status === "completed") {
    const delivery = await post<{ content?: string }>(
      "/api/modules/business/delivery",
      { token, orderId: checkout.orderId },
    );
    check(
      "delivery_retrievable",
      typeof delivery.content === "string" && delivery.content.length > 0,
      "The customer can download the delivery",
    );
  }

  const after = await report();
  check(
    "report_counts_delivery",
    delivered(after) === delivered(before) + 1,
    `Paid, delivered and kept orders went from ${delivered(before)} to ${delivered(after)}`,
  );
  check(
    "report_counts_receipt",
    receipts(after) > receipts(before),
    `Customer receipts went from ${receipts(before)} to ${receipts(after)} micro-USD`,
  );
  const publicData = (await (
    await fetch(input.origin + "/api/public", {
      signal: AbortSignal.timeout(15_000),
    })
  ).json()) as { financial?: { customerReceiptsMicros?: string } | null };
  check(
    "public_site_agrees",
    publicData.financial?.customerReceiptsMicros ===
      after.outcomes?.grossCustomerReceiptsMicros,
    `Public receipts ${publicData.financial?.customerReceiptsMicros ?? "unavailable"}, report ${after.outcomes?.grossCustomerReceiptsMicros ?? "unavailable"}`,
  );
  const snapshot = await takeSnapshot({
    db: input.db,
    services: input.services,
    runId: input.runId,
    publish: false,
  });
  const stored = JSON.parse(
    (
      input.db
        .prepare("SELECT report_json FROM pilot_snapshots WHERE id=?")
        .get(snapshot.id) as { report_json: string }
    ).report_json,
  ) as PilotReport;
  check(
    "snapshot_agrees",
    stored.outcomes?.grossCustomerReceiptsMicros ===
      after.outcomes?.grossCustomerReceiptsMicros &&
      verifyChain(input.db, input.runId).ok,
    `Unpublished snapshot ${snapshot.id} matches the report and the chain verifies`,
  );

  const settings = alertSettings(input.env);
  const conditions = await evaluateConditions({
    db: input.db,
    services: input.services,
    config: input.config,
    runId: input.runId,
    settings,
  });
  for (const id of ["worker_down", "economy_unavailable", "run_failed"]) {
    const condition = conditions.find((item) => item.id === id);
    check(
      `alert_${id}_clear`,
      condition?.active === false,
      condition?.message ?? "",
    );
  }
  if (settings.webhookUrl) {
    const sent = await deliver(
      settings,
      [
        {
          condition: "rehearsal",
          severity: "warning",
          change: "raised",
          message: "Pilot rehearsal test notification; no action needed",
        },
      ],
      new Date().toISOString(),
    );
    check(
      "alert_webhook_delivers",
      sent,
      sent
        ? "A test notification was accepted by the webhook"
        : "The webhook did not accept the test notification",
    );
  } else
    skip("alert_webhook_delivers", "WIDEMIND_ALERT_WEBHOOK_URL is not set");

  const backups = backupFresh(backupSettings(input.env));
  if (backups) check("backups_recent", backups.fresh, backups.detail);
  else skip("backups_recent", "WIDEMIND_BACKUP_DIR is not set");

  return {
    ok: checks.every((item) => item.status !== "failed"),
    orderStatus: done.status,
    checks,
  };
}
