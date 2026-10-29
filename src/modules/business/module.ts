import { randomUUID } from "node:crypto";
import type { ModuleDefinition } from "../registry.ts";
import type {
  Json,
  Reservation,
  ToolAction,
  ToolReceipt,
} from "../../contracts/index.ts";
import { Store } from "../../runtime/store.ts";
import { createBusinessClient, BusinessApiError } from "./client.ts";
interface Work {
  eventId: string | null;
  attempt: number;
  dueAt: number | null;
  runtimeState: string;
}
interface Step {
  kind: "completed" | "unknown" | "not_performed";
  evidenceRef?: string;
  recoveredOrchestration?: boolean;
  processed?: boolean;
  eventState?: string;
  reason?: string;
}
const record = (value: Json): Record<string, Json> => {
  if (!value || typeof value !== "object" || Array.isArray(value))
    throw new Error("Object required");
  return value;
};
const identifier = (value: unknown) => {
  if (
    typeof value !== "string" ||
    !/^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$/.test(value)
  )
    throw new Error("Invalid identifier");
  return value;
};
const token = (value: unknown) => {
  if (typeof value !== "string" || !/^[A-Za-z0-9_-]{32,256}$/.test(value))
    throw new Error("Invalid customer token");
  return value;
};
function receipt(action: ToolAction, step: Step): ToolReceipt {
  return {
    version: 1,
    actionId: action.actionId,
    status: "succeeded",
    result: {
      processed: step.processed === true,
      eventState: step.eventState ?? "unknown",
      recoveredOrchestration: step.recoveredOrchestration === true,
      transport: "private-business-portal",
    },
    usage: {
      actualCharge: { currency: "USD", micros: "0" },
      provider: "business.step",
      accountRef: process.env.WIDEMIND_TOOLS_ACCOUNT ?? "dedicated-tools",
      externalEventId: action.actionId,
      billableFailure: false,
      provisional: false,
      evidenceRef: `business-bridge:${action.actionId}`,
    },
  };
}
async function start(reservation: Reservation) {
  const origin = process.env.WIDEMIND_ECONOMY_URL,
    secret = process.env.WIDEMIND_ECONOMY_GATEWAY_TOKEN;
  if (!origin || !secret) throw new Error("Economy gateway unavailable");
  const url = new URL(origin);
  if (
    url.protocol !== "https:" &&
    !(
      url.protocol === "http:" &&
      ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname)
    )
  )
    throw new Error("Invalid economy service origin");
  const endpoint = new URL(
    `/v1/reservations/${identifier(reservation.reservationId)}`,
    url,
  );
  const headers = {
    Authorization: `Bearer ${secret}`,
    "Content-Type": "application/json",
  };
  const observed = await fetch(endpoint, {
    headers,
    redirect: "error",
    signal: AbortSignal.timeout(15000),
  });
  if (!observed.ok) throw new Error("Reservation unavailable");
  const row = (await observed.json()) as {
    run_id: string;
    action_id: string;
    provider: string;
    account: string;
    amount_micro_usd: number;
    state: string;
    limits: Record<string, number>;
  };
  if (
    row.run_id !== reservation.runId ||
    row.action_id !== reservation.actionId ||
    row.provider !== "business.step" ||
    row.account !== (process.env.WIDEMIND_TOOLS_ACCOUNT ?? "dedicated-tools") ||
    row.amount_micro_usd !== 0 ||
    row.limits.max_duration_ms !== reservation.limit.maxDurationMs ||
    row.limits.max_storage_bytes !== reservation.limit.maxOutputBytes
  )
    throw new Error("Business reservation binding mismatch");
  if (row.state === "reserved") {
    const response = await fetch(new URL(endpoint.pathname + "/start", url), {
      method: "POST",
      headers,
      body: "{}",
      redirect: "error",
      signal: AbortSignal.timeout(15000),
    });
    if (!response.ok) throw new Error("Business dispatch unavailable");
  } else if (!["running", "unknown"].includes(row.state))
    throw new Error("Reservation closed");
  // A repeated same-action dispatch is fenced by the bridge's transactionally
  // unique invocation, never by assuming an absent network response was free.
}
export const definition: ModuleDefinition = {
  id: "business",
  contractVersion: 1,
  migrations: [
    {
      version: 1,
      sql: "CREATE TABLE business_rate_limits(key TEXT PRIMARY KEY,count INTEGER NOT NULL,window_at INTEGER NOT NULL);",
    },
  ],
  create: ({ db, config }) => {
    if (config.profile === "demo") return {};
    const client = createBusinessClient();
    if (!client) return {};
    const store = new Store(db);
    const run = (value: unknown, fixed = false) => {
      const id = identifier(value ?? client.runId);
      if (fixed && id !== client.runId)
        throw new Error("Public run selection not allowed");
      if (store.run(id)?.profile !== "foundation")
        throw new Error("Unknown business run");
      return id;
    };
    const wake = (runId: string, reference: string = randomUUID()) => {
      store.deliverEvent(runId, {
        source: "business",
        accountRef: "control-plane",
        externalEventId: reference,
        kind: "business.changed",
        payload: { reference },
      });
    };
    const limit = (address: string, category: string, max: number) => {
      const key = `${category}:${address}`;
      const now = Date.now();
      db.prepare("DELETE FROM business_rate_limits WHERE window_at<?").run(
        now - 60000,
      );
      db.prepare(
        "INSERT INTO business_rate_limits VALUES (?,1,?) ON CONFLICT(key) DO UPDATE SET count=count+1",
      ).run(key, now);
      return (
        Number(
          db
            .prepare("SELECT count FROM business_rate_limits WHERE key=?")
            .get(key)?.count,
        ) <= max
      );
    };
    const route = (
      name: string,
      access: "operator" | "public",
      method: "GET" | "POST",
      handle: (
        body: Record<string, Json>,
        query: Record<string, string>,
      ) => Promise<Json>,
    ) => ({
      path: `/api/modules/business/${name}`,
      access,
      method,
      handle: async (input: {
        body: Json;
        query: Record<string, string>;
        headers: Record<string, string>;
        operator: boolean;
        remoteAddress?: string;
      }) => {
        try {
          if (
            access === "public" &&
            method === "POST" &&
            !limit(
              input.remoteAddress ?? "unknown",
              name,
              name === "request" ? 5 : 60,
            )
          )
            return {
              status: 429,
              body: { error: "Request limit reached; retry later" },
            };
          return {
            status: name === "request" ? 202 : 200,
            body: await handle(record(input.body), input.query),
          };
        } catch (error) {
          return {
            status: error instanceof BusinessApiError ? error.status : 400,
            body: {
              error:
                "Business operation unavailable; verify private access, runtime and integrations",
            },
          };
        }
      },
    });
    return {
      website: {
        health: () => ({ available: true }),
        publicContent: async () => {
          if (!client.runId)
            return {
              offerings: [],
              activity: [],
              availability: {
                available: false,
                reason: "Commerce run is not configured",
              },
            };
          return client.request("/v1/public", {
            runId: run(client.runId, true),
          });
        },
      },
      business: {
        health: () => ({ available: true }),
        selectWork: async (snapshot) => {
          const state = await client.request<Work>("/v1/work-state", {
            runId: snapshot.runId,
          });
          if (!state.eventId)
            return {
              kind: "wait",
              reason:
                "No ready business work; waiting for customer/provider events",
              wakeAt: new Date(Date.now() + 30000).toISOString(),
              acknowledgeEventIds: snapshot.events.map(
                (event) => event.eventId,
              ),
            };
          if (state.dueAt !== null && state.dueAt * 1000 > Date.now())
            return {
              kind: "wait",
              reason: "Business event scheduled",
              wakeAt: new Date(state.dueAt * 1000).toISOString(),
            };
          return {
            kind: "work",
            candidate: {
              workKey: `business-step:${state.eventId}:${state.attempt}:${snapshot.actions.filter((item) => item.action.tool === "business.step" && record(item.action.input).eventId === state.eventId).length}`,
              tool: "business.step",
              input: { eventId: state.eventId },
              limit: {
                maximum: { currency: "USD", micros: "0" },
                maxDurationMs: 120000,
                maxOutputBytes: 4096,
              },
            },
            acknowledgeEventIds: snapshot.events.map((event) => event.eventId),
          };
        },
      },
      routes: [
        route("offerings", "public", "GET", async () =>
          client.runId
            ? client.request("/v1/offerings", {
                runId: run(client.runId, true),
              })
            : { available: false, offerings: [] },
        ),
        route("request", "public", "POST", async (body) => {
          const runId = run(body.runId, true);
          const result = await client.request<Record<string, Json>>(
            "/v1/request",
            {
              runId,
              requestId: identifier(body.requestId),
              email: body.email,
              service: body.service,
              scope: body.scope,
              ...(body.customerToken
                ? { customerToken: token(body.customerToken) }
                : {}),
            },
          );
          wake(runId, `request:${String(result.opportunityId)}`);
          return result;
        }),
        ...(["customer", "accept", "delivery", "revoke"] as const).map((name) =>
          route(name, "public", "POST", async (body) => {
            const runId = run(body.runId);
            const result = await client.request(`/v1/${name}`, {
              ...body,
              runId,
              token: token(body.token),
            });
            if (name === "accept")
              wake(runId, `accept:${identifier(body.quoteId)}`);
            return result;
          }),
        ),
        ...(["reconcile", "retry"] as const).map((name) =>
          route(name, "operator", "POST", async (body) => {
            const runId = run(body.runId);
            const result = await client.request("/v1/recovery", {
              runId,
              actionId: identifier(body.actionId),
              mode: name,
              evidenceReference:
                body.evidenceReference ?? `operator-${randomUUID()}`,
            });
            wake(runId);
            return result;
          }),
        ),
        ...(["resolve-hold", "reconsider"] as const).map((name) =>
          route(name, "operator", "POST", async (body) => {
            const runId = run(body.runId);
            const result = await client.request(`/v1/${name}`, {
              ...body,
              runId,
            });
            wake(runId);
            return result;
          }),
        ),
        route("status", "operator", "GET", async (_body, query) =>
          client.request("/v1/status", { runId: run(query.runId) }),
        ),
        route("policy", "operator", "POST", async (body) => {
          const runId = run(body.runId);
          const result = await client.request("/v1/policy", {
            runId,
            policy: body.policy,
          });
          wake(runId);
          return result;
        }),
        route("opportunities", "operator", "POST", async (body) => {
          const runId = run(body.runId);
          const result = await client.request("/v1/opportunities", {
            runId,
            opportunity: body,
          });
          wake(runId);
          return result;
        }),
      ],
    };
  },
  decorate: (services, { config }) => {
    if (config.profile === "demo") return;
    const client = createBusinessClient();
    if (!client) return;
    const base = services.tools;
    services.tools = {
      ...base,
      capabilities: () => [
        ...base.capabilities(),
        { id: "business.step", availability: { available: true } },
      ],
      execute: async (action, reservation, signal) => {
        if (action.tool !== "business.step")
          return base.execute(action, reservation, signal);
        if (signal.aborted || action.limit.maximum.micros !== "0")
          return {
            kind: "unknown",
            reason: "Business step cancelled or invalid cost limit",
          };
        try {
          const checked = await services.economy.authorize({
            runId: action.runId,
            actionId: action.actionId,
            tool: action.tool,
            limit: action.limit,
          });
          if (
            checked.kind !== "authorized" ||
            checked.reservation.reservationId !== reservation.reservationId ||
            checked.reservation.authorization !== reservation.authorization
          )
            throw new Error("Invalid authorization");
          await start(reservation);
          const input = record(action.input);
          const result = await client.request<Step>("/v1/step", {
            runId: action.runId,
            actionId: action.actionId,
            eventId: identifier(input.eventId),
          });
          return result.kind === "completed"
            ? { kind: "receipt", receipt: receipt(action, result) }
            : {
                kind: "unknown",
                reason: result.reason ?? "Business step uncertain",
              };
        } catch {
          return {
            kind: "unknown",
            reason:
              "Business step outcome unavailable; reconcile bridge before retry",
          };
        }
      },
      reconcile: async (action, reservation) => {
        if (action.tool !== "business.step")
          return base.reconcile(action, reservation);
        try {
          const result = await client.request<Step>("/v1/reconcile", {
            runId: action.runId,
            actionId: action.actionId,
          });
          if (result.kind === "not_performed" && result.evidenceRef)
            return { kind: "not_performed", evidenceRef: result.evidenceRef };
          return result.kind === "completed"
            ? { kind: "receipt", receipt: receipt(action, result) }
            : {
                kind: "unknown",
                reason: "Business bridge requires operator reconciliation",
              };
        } catch {
          return { kind: "unavailable", reason: "Business bridge unavailable" };
        }
      },
    };
  },
};
