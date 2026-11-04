/**
 * Pilot alerts. Runs as its own process so it can notice a dead worker.
 * Conditions are evaluated from local records and free economy reads; a
 * notification goes out only when a condition is raised, cleared, or has
 * stayed active past the repeat interval. Messages carry counts and states
 * only, never customer, payment or credential data.
 */
import type { DatabaseSync } from "node:sqlite";
import type { RuntimeConfig } from "../contracts/index.ts";
import type { Services } from "../modules/registry.ts";
import { transaction } from "../db/database.ts";
import { Store } from "./store.ts";
import { backupFresh, backupSettings, type BackupSettings } from "./backup.ts";
import { offsiteFresh } from "./offsite.ts";

export type Severity = "critical" | "warning";
export interface Condition {
  id: string;
  severity: Severity;
  /** null when the condition could not be observed; never raises or clears. */
  active: boolean | null;
  message: string;
}
export interface Notice {
  condition: string;
  severity: Severity;
  change: "raised" | "cleared" | "repeat";
  message: string;
}
export interface AlertSettings {
  webhookUrl: string | null;
  repeatMs: number;
  /** Optional economy operator read for reconciliation dead letters. */
  economy: { url: string; operatorToken: string } | null;
  /** Backup location and maximum age; null when backups are not configured. */
  backup?: BackupSettings | null;
  /** Whether off-host copies are configured (WIDEMIND_OFFSITE_ENDPOINT set). */
  offsiteConfigured?: boolean;
}

const LOOPBACK = ["127.0.0.1", "localhost", "[::1]"];
export function alertSettings(env: NodeJS.ProcessEnv): AlertSettings {
  let webhookUrl: string | null = null;
  if (env.WIDEMIND_ALERT_WEBHOOK_URL) {
    const url = new URL(env.WIDEMIND_ALERT_WEBHOOK_URL);
    if (
      url.username ||
      url.password ||
      !(
        url.protocol === "https:" ||
        (url.protocol === "http:" && LOOPBACK.includes(url.hostname))
      )
    )
      throw new Error(
        "WIDEMIND_ALERT_WEBHOOK_URL must be HTTPS (or loopback HTTP) without embedded credentials",
      );
    webhookUrl = url.href;
  }
  const repeat = env.WIDEMIND_ALERT_REPEAT_MINUTES ?? "360";
  if (!/^[0-9]{1,5}$/.test(repeat) || Number(repeat) < 5)
    throw new Error(
      "WIDEMIND_ALERT_REPEAT_MINUTES must be an integer of at least 5",
    );
  const economyUrl = env.WIDEMIND_ECONOMY_URL,
    operatorToken = env.WIDEMIND_ECONOMY_OPERATOR_TOKEN;
  return {
    webhookUrl,
    repeatMs: Number(repeat) * 60_000,
    economy:
      economyUrl && operatorToken
        ? { url: new URL(economyUrl).origin, operatorToken }
        : null,
    backup: backupSettings(env),
    offsiteConfigured: Boolean(env.WIDEMIND_OFFSITE_ENDPOINT),
  };
}

async function deadLetters(
  settings: AlertSettings,
  transport: typeof fetch,
): Promise<number | null> {
  if (!settings.economy) return null;
  try {
    const response = await transport(
      settings.economy.url +
        "/v1/reconciliation/tasks?state=dead_letter&limit=100",
      {
        headers: { Authorization: "Bearer " + settings.economy.operatorToken },
        signal: AbortSignal.timeout(5000),
      },
    );
    if (!response.ok) return null;
    const body = (await response.json()) as unknown;
    return Array.isArray(body) ? body.length : null;
  } catch {
    return null;
  }
}

export async function evaluateConditions(input: {
  db: DatabaseSync;
  services: Services;
  config: RuntimeConfig;
  runId: string;
  settings: AlertSettings;
  transport?: typeof fetch;
  now?: number;
}): Promise<Condition[]> {
  const { db, services, runId } = input;
  const now = input.now ?? Date.now();
  const store = new Store(db);
  const run = store.run(runId);
  if (!run) throw new Error("Pilot run not found");
  const conditions: Condition[] = [];
  const add = (
    id: string,
    severity: Severity,
    active: boolean | null,
    message: string,
  ) => conditions.push({ id, severity, active, message });

  const status = store.runtimeStatus(input.config.profile, now);
  add(
    "worker_down",
    "critical",
    !status.available,
    status.available
      ? "Persistent worker heartbeat is current"
      : `No live worker heartbeat (last ${status.lastHeartbeatAt ?? "never"})`,
  );
  add(
    "run_failed",
    "critical",
    run.state === "failed",
    `Run state is ${run.state}`,
  );

  // The economy client reports failures as a missing summary rather than an
  // error, and a pilot run is always opened, so no summary means accounting
  // cannot be read.
  let summary = null;
  if (services.economy.health().available) {
    try {
      summary = await services.economy.summary(runId);
    } catch {
      summary = null;
    }
  }
  const economyOk = summary !== null;
  add(
    "economy_unavailable",
    "critical",
    !economyOk,
    economyOk
      ? "Economy accounting readable"
      : "No accounting summary: economy unreachable, failing, or the run is not opened",
  );
  // Without a summary, only the run state can show exhaustion.
  const exhausted =
    run.state === "exhausted" ||
    (summary !== null && BigInt(summary.spendableMicros) <= 0n);
  add(
    "credit_exhausted",
    "warning",
    exhausted ? true : economyOk ? false : null,
    summary
      ? `Spendable credit is ${summary.spendableMicros} micro-USD`
      : `Run state is ${run.state}`,
  );

  const unresolved = Number(
    db
      .prepare(
        "SELECT COUNT(*) AS count FROM runtime_actions WHERE run_id=? AND state='unresolved'",
      )
      .get(runId)?.count ?? 0,
  );
  add(
    "unresolved_actions",
    "warning",
    unresolved > 0,
    `${unresolved} action(s) have an unknown external outcome and need reconciliation`,
  );

  const letters = await deadLetters(input.settings, input.transport ?? fetch);
  add(
    "reconciliation_dead_letters",
    "critical",
    letters === null ? null : letters > 0,
    letters === null
      ? "Reconciliation tasks not observable (economy operator access not configured or unavailable)"
      : `${letters} payment reconciliation task(s) exhausted their retries`,
  );
  const backups = backupFresh(input.settings.backup ?? null, now);
  add(
    "backup_stale",
    "warning",
    backups === null ? null : !backups.fresh,
    backups?.detail ?? "Backups not configured (WIDEMIND_BACKUP_DIR unset)",
  );
  const offsite = offsiteFresh(
    Boolean(input.settings.offsiteConfigured),
    input.settings.backup ?? null,
    now,
  );
  add(
    "offsite_backup_stale",
    "warning",
    offsite === null ? null : !offsite.fresh,
    offsite?.detail ?? "Off-host backups not configured",
  );
  return conditions;
}

interface AlertRow {
  condition: string;
  active: number;
  since: number;
  notified_active: number | null;
  notified_at: number | null;
}

/** Record current states and return the notices that are due. */
export function dueNotices(
  db: DatabaseSync,
  runId: string,
  conditions: Condition[],
  repeatMs: number,
  now = Date.now(),
): Notice[] {
  return transaction(db, () => {
    const notices: Notice[] = [];
    for (const condition of conditions) {
      if (condition.active === null) continue;
      const active = condition.active ? 1 : 0;
      const row = db
        .prepare("SELECT * FROM pilot_alerts WHERE run_id=? AND condition=?")
        .get(runId, condition.id) as AlertRow | undefined;
      if (!row)
        db.prepare(
          "INSERT INTO pilot_alerts(run_id,condition,active,since) VALUES (?,?,?,?)",
        ).run(runId, condition.id, active, now);
      else if (row.active !== active)
        db.prepare(
          "UPDATE pilot_alerts SET active=?,since=? WHERE run_id=? AND condition=?",
        ).run(active, now, runId, condition.id);
      const notified = row?.notified_active ?? 0;
      let change: Notice["change"] | null = null;
      if (active && !notified) change = "raised";
      else if (!active && notified) change = "cleared";
      else if (active && now - (row?.notified_at ?? 0) >= repeatMs)
        change = "repeat";
      if (change)
        notices.push({
          condition: condition.id,
          severity: condition.severity,
          change,
          message: condition.message,
        });
    }
    return notices;
  });
}

/** Mark notices delivered only after the channel accepted them. */
export function markNotified(
  db: DatabaseSync,
  runId: string,
  notices: Notice[],
  now = Date.now(),
) {
  transaction(db, () => {
    for (const notice of notices)
      db.prepare(
        "UPDATE pilot_alerts SET notified_active=?,notified_at=? WHERE run_id=? AND condition=?",
      ).run(notice.change === "cleared" ? 0 : 1, now, runId, notice.condition);
  });
}

export function alertText(notices: Notice[]): string {
  return notices
    .map(
      (notice) =>
        `[WideMind ${notice.severity}] ${notice.condition} ${notice.change}: ${notice.message}`,
    )
    .join("\n");
}

export async function deliver(
  settings: AlertSettings,
  notices: Notice[],
  observedAt: string,
  transport: typeof fetch = fetch,
): Promise<boolean> {
  if (!notices.length || !settings.webhookUrl) return true;
  try {
    const text = alertText(notices);
    const response = await transport(settings.webhookUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      // `text` suits Slack-style hooks, `content` Discord-style hooks.
      body: JSON.stringify({
        source: "widemind",
        observedAt,
        text,
        content: text,
        alerts: notices,
      }),
      signal: AbortSignal.timeout(5000),
      redirect: "error",
    });
    return response.ok;
  } catch {
    return false;
  }
}

/** One evaluation and delivery pass. Delivery failures retry on the next pass. */
export async function alertPass(input: {
  db: DatabaseSync;
  services: Services;
  config: RuntimeConfig;
  runId: string;
  settings: AlertSettings;
  transport?: typeof fetch;
  now?: number;
}) {
  const now = input.now ?? Date.now();
  const conditions = await evaluateConditions({ ...input, now });
  const notices = dueNotices(
    input.db,
    input.runId,
    conditions,
    input.settings.repeatMs,
    now,
  );
  const observedAt = new Date(now).toISOString();
  const delivered = await deliver(
    input.settings,
    notices,
    observedAt,
    input.transport,
  );
  if (delivered) markNotified(input.db, input.runId, notices, now);
  return {
    observedAt,
    active: conditions.filter((item) => item.active).map((item) => item.id),
    conditions,
    notices,
    delivered,
    channel: input.settings.webhookUrl ? "webhook" : "stdout",
  };
}
