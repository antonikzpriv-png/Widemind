import { createHash, randomUUID } from "node:crypto";
import type { ModuleDefinition } from "../registry.ts";
import type {
  Json,
  Reservation,
  ToolAction,
  ToolReceipt,
} from "../../contracts/index.ts";
import { Store } from "../../runtime/store.ts";
import { createBusinessClient, BusinessApiError } from "./client.ts";
import {
  discoveryCommandPath,
  discoveryPagePath,
  renderDiscoveryPage,
} from "./discovery-ui.ts";
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
const historyOffset = (value: string | undefined) => {
  if (value === undefined) return 0;
  if (!/^(0|[1-9][0-9]{0,8})$/.test(value))
    throw new Error("Invalid discovery history offset");
  return Number(value);
};
const token = (value: unknown) => {
  if (typeof value !== "string" || !/^[A-Za-z0-9_-]{32,256}$/.test(value))
    throw new Error("Invalid customer token");
  return value;
};
const artifactVersion = (value: Json | undefined) => {
  if (value === undefined || value === "") return undefined;
  const parsed =
    typeof value === "string" && /^[0-9]{1,3}$/.test(value)
      ? Number(value)
      : value;
  if (
    typeof parsed !== "number" ||
    !Number.isInteger(parsed) ||
    parsed < 1 ||
    parsed > 100
  )
    throw new Error("Invalid artifact version");
  return parsed;
};
function previewDocument(value: Json): Json {
  const artifact = record(value);
  if (
    typeof artifact.content !== "string" ||
    Buffer.byteLength(artifact.content) > 1_500_000
  )
    throw new Error("Preview unavailable");
  const encoding = artifact.encoding ?? "utf-8";
  if (encoding !== "utf-8" && encoding !== "base64")
    throw new Error("Invalid artifact encoding");
  if (
    encoding === "base64" &&
    !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(
      artifact.content,
    )
  )
    throw new Error("Invalid artifact encoding");
  const bytes = Buffer.from(
    artifact.content,
    encoding === "base64" ? "base64" : "utf8",
  );
  if (bytes.length > 1_000_000) throw new Error("Preview exceeds bound");
  if (artifact.mediaType === "text/html") {
    return { html: new TextDecoder("utf-8", { fatal: true }).decode(bytes) };
  }
  if (
    artifact.mediaType !== "image/svg+xml" &&
    artifact.mediaType !== "image/png"
  )
    throw new Error("This artifact has no visual preview");
  const html =
    '<!doctype html><html lang="en"><meta charset="utf-8"><title>Private artwork preview</title><style>html,body{margin:0;min-height:100%;background:#111}body{display:grid;place-items:center}img{display:block;max-width:100%;height:auto}</style><img alt="Your private graphic preview" src="data:' +
    artifact.mediaType +
    ";base64," +
    bytes.toString("base64") +
    '"></html>';
  if (Buffer.byteLength(html) > 1_000_000)
    throw new Error("Preview exceeds bound");
  return { html };
}
function receipt(
  action: ToolAction,
  step: Step,
  env: NodeJS.ProcessEnv,
): ToolReceipt {
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
      accountRef: env.WIDEMIND_TOOLS_ACCOUNT ?? "dedicated-tools",
      externalEventId: action.actionId,
      billableFailure: false,
      provisional: false,
      evidenceRef: `business-bridge:${action.actionId}`,
    },
  };
}
async function start(reservation: Reservation, env: NodeJS.ProcessEnv) {
  const origin = env.WIDEMIND_ECONOMY_URL,
    secret = env.WIDEMIND_ECONOMY_GATEWAY_TOKEN;
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
    row.account !== (env.WIDEMIND_TOOLS_ACCOUNT ?? "dedicated-tools") ||
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
  create: ({ db, config, env = process.env }) => {
    if (config.profile === "demo") return {};
    const client = createBusinessClient(env);
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
      responseKind?: "sandboxed-html",
    ) => ({
      path: `/api/modules/business/${name}`,
      access,
      method,
      ...(responseKind ? { responseKind } : {}),
      handle: async (input: {
        body: Json;
        query: Record<string, string>;
        headers: Record<string, string>;
        operator: boolean;
        remoteAddress?: string;
      }) => {
        try {
          if (access === "operator" && !input.operator)
            return {
              status: 403,
              body: { error: "Operator access required" },
            };
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
            status: name === "request" || name === "message" ? 202 : 200,
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
    const discoveryCommand = async (body: Record<string, Json>) => {
      const runId = run(body.runId);
      const command = body.command;
      let result: Json;
      if (command === "configure") {
        const document =
          typeof body.configJson === "string"
            ? record(JSON.parse(body.configJson))
            : record(body.config ?? null);
        result = await client.request("/v1/discovery-configure", {
          runId,
          config: document,
        });
      } else if (command === "start") {
        const spec =
          typeof body.specJson === "string"
            ? record(JSON.parse(body.specJson))
            : record(body.spec ?? null);
        result = await client.request("/v1/experiment-start", {
          runId,
          opportunityId: identifier(body.opportunityId),
          requestId: identifier(body.requestId),
          spec,
        });
      } else if (command === "stop") {
        if (
          typeof body.reason !== "string" ||
          !body.reason.trim() ||
          body.reason.length > 1000
        )
          throw new Error("Stop reason required");
        result = await client.request("/v1/experiment-stop", {
          runId,
          experimentId: identifier(body.experimentId),
          reason: body.reason,
        });
      } else if (command === "wake") {
        result = await client.request("/v1/discovery-wake", {
          runId,
          marketId: identifier(body.marketId),
          requestId: identifier(body.requestId),
        });
      } else if (command === "link-order") {
        if (
          typeof body.evidenceReference !== "string" ||
          !body.evidenceReference.trim() ||
          body.evidenceReference.length > 1000
        )
          throw new Error("Order attribution evidence required");
        result = await client.request("/v1/experiment-link-order", {
          runId,
          experimentId: identifier(body.experimentId),
          orderId: identifier(body.orderId),
          evidenceReference: body.evidenceReference,
        });
      } else if (command === "reconcile") {
        result = await client.request("/v1/recovery", {
          runId,
          actionId: identifier(body.actionId),
          mode: "reconcile",
          evidenceReference: `operator-discovery:${identifier(body.requestId)}`,
        });
      } else throw new Error("Unknown discovery command");
      wake(runId);
      return result;
    };
    return {
      operatorPages: [
        {
          path: discoveryPagePath,
          render: async ({ query, csrf }) => {
            const selected =
              query.runId ??
              client.runId ??
              store.runs().find((item) => item.profile === "foundation")?.id ??
              "";
            const data = selected
              ? await client.request("/v1/discovery-status", {
                  runId: run(selected),
                  offset: historyOffset(query.offset),
                })
              : {};
            const artifact =
              selected && query.experimentId
                ? await client.request("/v1/experiment-artifact", {
                    runId: run(selected),
                    experimentId: identifier(query.experimentId),
                  })
                : undefined;
            return renderDiscoveryPage(data, csrf, selected, artifact);
          },
        },
      ],
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
              // Payment reconciliation completes in its separate service. Keep
              // free observation bounded even when an older delivery has a
              // distant deadline and no new webhook reaches this worker.
              wakeAt: new Date(
                Math.min(state.dueAt * 1000, Date.now() + 30000),
              ).toISOString(),
              acknowledgeEventIds: snapshot.events.map(
                (event) => event.eventId,
              ),
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
        route("discovery-status", "operator", "GET", async (_body, query) =>
          client.request("/v1/discovery-status", {
            runId: run(query.runId),
            offset: historyOffset(query.offset),
          }),
        ),
        route("experiment-artifact", "operator", "GET", async (_body, query) =>
          client.request("/v1/experiment-artifact", {
            runId: run(query.runId),
            experimentId: identifier(query.experimentId),
          }),
        ),
        ...(
          [
            ["discovery-configure", "configure"],
            ["experiment-start", "start"],
            ["experiment-stop", "stop"],
            ["discovery-wake", "wake"],
            ["experiment-link-order", "link-order"],
          ] as const
        ).map(([name, command]) =>
          route(name, "operator", "POST", async (body) =>
            discoveryCommand({ ...body, command }),
          ),
        ),
        {
          ...route("discovery-command", "operator", "POST", async (body) =>
            discoveryCommand(body),
          ),
          path: discoveryCommandPath,
          operatorRedirect: discoveryPagePath,
        },
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
              ...(body.message !== undefined ? { message: body.message } : {}),
              ...(body.customerToken
                ? { customerToken: token(body.customerToken) }
                : {}),
            },
          );
          wake(runId, `request:${String(result.opportunityId)}`);
          return result;
        }),
        ...(
          [
            "customer",
            "accept",
            "delivery",
            "manifest",
            "revoke",
            "message",
            "opt-out",
          ] as const
        ).map((name) =>
          route(name, "public", "POST", async (body) => {
            const runId = run(body.runId);
            if (name === "message") identifier(body.messageId);
            const result = await client.request(`/v1/${name}`, {
              ...body,
              runId,
              token: token(body.token),
              ...((name === "delivery" || name === "manifest") &&
              body.version !== undefined
                ? { version: artifactVersion(body.version) }
                : {}),
            });
            if (name === "accept")
              wake(runId, `accept:${identifier(body.quoteId)}`);
            if (name === "message")
              wake(
                runId,
                `message:${createHash("sha256")
                  .update(
                    JSON.stringify([
                      record(result).conversationId,
                      body.messageId,
                    ]),
                  )
                  .digest("hex")}`,
              );
            return result;
          }),
        ),
        route(
          "preview",
          "public",
          "POST",
          async (body) => {
            const result = await client.request<Json>("/v1/delivery", {
              runId: run(body.runId),
              token: token(body.token),
              orderId: identifier(body.orderId),
              ...(body.filename !== undefined
                ? { filename: identifier(body.filename) }
                : {}),
              ...(body.version !== undefined
                ? { version: artifactVersion(body.version) }
                : {}),
            });
            return previewDocument(result);
          },
          "sandboxed-html",
        ),
        route("change-request", "public", "POST", async (body) => {
          const runId = run(body.runId);
          if (body.kind !== "revision" && body.kind !== "scope_change")
            throw new Error("Invalid change request");
          if (
            typeof body.feedback !== "string" ||
            !body.feedback.trim() ||
            body.feedback.length > 2000
          )
            throw new Error("Change request needs bounded feedback");
          const requestId = identifier(body.requestId);
          const result = await client.request<Json>("/v1/change-request", {
            runId,
            token: token(body.token),
            orderId: identifier(body.orderId),
            requestId,
            kind: body.kind,
            feedback: body.feedback,
            ...(body.kind === "scope_change"
              ? { scope: record(body.scope ?? null) }
              : {}),
          });
          wake(runId, `change:${requestId}`);
          return result;
        }),
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
        route("conversations", "operator", "GET", async (_body, query) =>
          client.request("/v1/conversations", { runId: run(query.runId) }),
        ),
        route("conversation", "operator", "GET", async (_body, query) =>
          client.request("/v1/conversation", {
            runId: run(query.runId),
            conversationId: identifier(query.conversationId),
          }),
        ),
        route("conversation-resolve", "operator", "POST", async (body) => {
          const runId = run(body.runId);
          if (
            typeof body.evidenceReference !== "string" ||
            !body.evidenceReference.trim() ||
            body.evidenceReference.length > 1000
          )
            throw new Error(
              "A bounded operator evidence reference is required",
            );
          const result = await client.request("/v1/conversation-resolve", {
            runId,
            conversationId: identifier(body.conversationId),
            evidenceReference: body.evidenceReference,
          });
          wake(runId);
          return result;
        }),
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
  decorate: (services, { config, env = process.env }) => {
    if (config.profile === "demo") return;
    const client = createBusinessClient(env);
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
          await start(reservation, env);
          const input = record(action.input);
          const result = await client.request<Step>("/v1/step", {
            runId: action.runId,
            actionId: action.actionId,
            eventId: identifier(input.eventId),
          });
          return result.kind === "completed"
            ? { kind: "receipt", receipt: receipt(action, result, env) }
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
            ? { kind: "receipt", receipt: receipt(action, result, env) }
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
