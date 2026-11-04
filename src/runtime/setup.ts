/**
 * Guided setup: the go-live checklist arranged as ordered stages, each step
 * with a plain title and exactly what to do next. It only reads state.
 */
import type { DatabaseSync } from "node:sqlite";
import type { PilotReadiness } from "./pilot.ts";
import { currentProfile } from "./agent.ts";

export type Stage =
  | "Create your agent"
  | "Connect the services"
  | "Connect accounts"
  | "Protect the data"
  | "Rehearse in sandbox"
  | "Go live";
export interface SetupStep {
  id: string;
  stage: Stage;
  title: string;
  done: boolean;
  /** What to do, shown while the step is not done. */
  todo: string;
  /** Current state reported by the check. */
  detail: string;
}
export interface SetupGuide {
  steps: SetupStep[];
  next: SetupStep | null;
  done: number;
  /** Everything except the go-live stage is done. */
  readyToRehearse: boolean;
  readyForLive: boolean;
}

const GUIDE: { id: string; stage: Stage; title: string; todo: string }[] = [
  {
    id: "run_configured",
    stage: "Create your agent",
    title: "Agent identity configured",
    todo: "Run npm run services:configure (it writes WIDEMIND_COMMERCE_RUN_ID to var/integration.env), then restart the website.",
  },
  {
    id: "agent_profile",
    stage: "Create your agent",
    title: "Agent created with a name, mission and offerings",
    todo: "Fill in Create your agent below.",
  },
  {
    id: "operator_auth",
    stage: "Connect the services",
    title: "Operator password set",
    todo: "Run npm run operator:hash and set WIDEMIND_OPERATOR_PASSWORD_HASH.",
  },
  {
    id: "economy_available",
    stage: "Connect the services",
    title: "Economy (ledger) connected",
    todo: "Start the economy service (npm run services:start, or the widemind-economy unit) and set WIDEMIND_ECONOMY_URL and its tokens.",
  },
  {
    id: "tools_available",
    stage: "Connect the services",
    title: "Tools connected",
    todo: "Start the tools service and set WIDEMIND_TOOLS_URL and WIDEMIND_TOOLS_TOKEN.",
  },
  {
    id: "business_available",
    stage: "Connect the services",
    title: "Business service connected",
    todo: "Start the business service and set WIDEMIND_BUSINESS_URL and WIDEMIND_BUSINESS_TOKEN.",
  },
  {
    id: "worker_available",
    stage: "Connect the services",
    title: "Agent worker running",
    todo: "Start the worker: npm run worker (or the widemind-worker unit).",
  },
  {
    id: "payment_provider",
    stage: "Connect accounts",
    title: "PayPal Business connected",
    todo: "Create a PayPal developer app, then set WIDEMIND_PAYPAL_CLIENT_ID, _CLIENT_SECRET, _MERCHANT_ID and _WEBHOOK_ID (docs/ECONOMY_INTEGRATION.md). PayPal will ask you to verify your identity.",
  },
  {
    id: "email_capability",
    stage: "Connect accounts",
    title: "The agent's own Gmail connected",
    todo: "Create a dedicated Gmail account for the agent and complete OAuth (docs/GMAIL.md). Google will ask you to verify it.",
  },
  {
    id: "backups_recent",
    stage: "Protect the data",
    title: "Backups running",
    todo: "Set WIDEMIND_BACKUP_DIR and enable the backup timer, or run npm run backup.",
  },
  {
    id: "offsite_backups",
    stage: "Protect the data",
    title: "Encrypted off-site copies",
    todo: "Run npm run backup:keygen on your own computer, then set WIDEMIND_OFFSITE_* (docs/DEPLOYMENT.md).",
  },
  {
    id: "rehearsal_passed",
    stage: "Rehearse in sandbox",
    title: "Sandbox rehearsal passed",
    todo: "Fund the sandbox run (npm run pilot:init -- --sandbox), then run npm run pilot:rehearse -- --email <your inbox> and pay with a PayPal sandbox buyer.",
  },
  {
    id: "public_https",
    stage: "Go live",
    title: "Website on HTTPS",
    todo: "Put the site behind Caddy with your domain and set WIDEMIND_PUBLIC_ORIGIN=https://... and WIDEMIND_SECURE_COOKIES=true.",
  },
  {
    id: "commerce_live",
    stage: "Go live",
    title: "Live payments",
    todo: "Switch PayPal to live credentials and set WIDEMIND_COMMERCE_MODE=live.",
  },
  {
    id: "live_effects_enabled",
    stage: "Go live",
    title: "Live actions allowed",
    todo: "Set WIDEMIND_ENABLE_LIVE_EFFECTS=true once everything above passes.",
  },
  {
    id: "economy_live_opening",
    stage: "Go live",
    title: "Live $5.00 opening funded",
    todo: "Open the run with verified live $5.00 funding through the economy operator flow.",
  },
];

export function setupGuide(
  db: DatabaseSync,
  runId: string | null,
  readiness: PilotReadiness,
): SetupGuide {
  const checks = new Map(readiness.checks.map((check) => [check.id, check]));
  const profile = runId ? currentProfile(db, runId) : null;
  const rehearsed = runId
    ? Boolean(
        db
          .prepare(
            "SELECT 1 FROM runtime_events WHERE run_id=? AND kind='pilot.rehearsal.passed' LIMIT 1",
          )
          .get(runId),
      )
    : false;
  const steps = GUIDE.map((item): SetupStep => {
    if (item.id === "agent_profile")
      return {
        ...item,
        done: Boolean(profile),
        detail: profile
          ? `${profile.name} (version ${profile.version})`
          : "Not created yet",
      };
    if (item.id === "rehearsal_passed")
      return {
        ...item,
        done: rehearsed,
        detail: rehearsed
          ? "A rehearsal passed every check"
          : "No passing rehearsal recorded",
      };
    const check = checks.get(item.id);
    return {
      ...item,
      done: check?.ok === true,
      detail: check?.detail ?? "Not checked",
    };
  });
  const before = steps.filter((step) => step.stage !== "Go live");
  return {
    steps,
    next: steps.find((step) => !step.done) ?? null,
    done: steps.filter((step) => step.done).length,
    readyToRehearse: before
      .filter((step) => step.id !== "rehearsal_passed")
      .every((step) => step.done),
    readyForLive: steps.every((step) => step.done),
  };
}
