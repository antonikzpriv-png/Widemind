import type { Economy, Json } from "../../contracts/index.ts";
import type { ModuleContext, ModuleDefinition } from "../registry.ts";
import { EconomyClient, bridgeSettings } from "../economy/client.ts";
import { EconomyCommerce, commerceSettings } from "./commerce.ts";
import { BusinessService } from "./engine.ts";
import type { CommerceReader } from "./engine.ts";
import { migrations } from "./migrations.ts";
import { record, text } from "./services.ts";
import type { BusinessServiceDefinition } from "./services.ts";
import { asJson, consoleProjection, orderProjection } from "./views.ts";
import { renderBusinessPage, renderBusinessPublic } from "./ui.ts";
import { configuredScopeValidator } from "./readiness.ts";

export function createBusinessModule(
  context: ModuleContext,
  options: {
    commerce?: CommerceReader;
    economy?: Pick<Economy, "summary">;
    services?: BusinessServiceDefinition[];
  } = {},
) {
  const env = context.env ?? process.env;
  if (env.WIDEMIND_BUSINESS_ENABLED !== "true") return {};
  if (context.config.profile !== "foundation")
    throw new Error("Business requires the foundation profile");
  const settings = bridgeSettings(env);
  const commerceConfiguration = commerceSettings(env);
  const commerce =
    options.commerce ??
    (commerceConfiguration
      ? new EconomyCommerce(commerceConfiguration)
      : {
          order: async () => ({
            kind: "unavailable" as const,
            reason: "Protected economy commerce is not configured",
          }),
        });
  const economy =
    options.economy ??
    (settings ? new EconomyClient(settings) : { summary: async () => null });
  const business = new BusinessService(context.db, commerce, {
    validateScope: configuredScopeValidator(env),
    ...(options.services ? { services: options.services } : {}),
    ...(env.WIDEMIND_GMAIL_ADDRESS
      ? { contactEmail: env.WIDEMIND_GMAIL_ADDRESS }
      : {}),
  });
  const summary = async (runId: string) => {
    try {
      return await economy.summary(runId);
    } catch {
      return null;
    }
  };
  const selectRun = (query: Record<string, string>) =>
    query.runId ??
    business.runtime.runs().find((run) => run.profile === "foundation")?.id ??
    "";
  const projection = async (query: Record<string, string>) => {
    const runId = selectRun(query);
    if (!runId)
      return {
        schemaVersion: 1,
        runId: "",
        runtimeState: "unavailable",
        policy: business.policy(""),
        summary: null,
        counts: {},
        pagination: {},
        opportunities: [],
        orders: [],
        quotes: [],
        customers: [],
        effects: [],
        events: [],
        memories: [],
        integrations: [],
      };
    return consoleProjection(business, runId, query, await summary(runId));
  };
  const commandRoute = {
    path: "/api/modules/brief-business/command",
    method: "POST" as const,
    access: "operator" as const,
    operatorRedirect: "/operator/modules/brief-business/console",
    async handle(request: { body: Json; operator: boolean }) {
      if (!request.operator)
        return { status: 403, body: { error: "Operator access required" } };
      try {
        const input = record(request.body),
          runId = text(input.runId, 200),
          requestId = text(input.requestId, 200);
        const command: Record<string, Json> = {
          command: text(input.command, 100),
        };
        for (const key of [
          "service",
          "customerEmail",
          "origin",
          "sourceReference",
          "quoteId",
          "evidenceReference",
          "orderId",
          "opportunityId",
          "decision",
          "email",
        ])
          if (input[key] !== undefined && input[key] !== "")
            command[key] = text(
              input[key],
              key === "sourceReference" ? 2048 : 1000,
            );
        if (input.scopeJson !== undefined)
          command.scope = JSON.parse(text(input.scopeJson, 12000));
        else if (input.scope !== undefined)
          command.scope = asJson(record(input.scope));
        if (input.policyJson !== undefined)
          command.policy = JSON.parse(text(input.policyJson, 12000));
        else if (input.policy !== undefined)
          command.policy = asJson(record(input.policy));
        if (input.priority !== undefined && input.priority !== "")
          command.priority =
            typeof input.priority === "string"
              ? Number(input.priority)
              : asJson(input.priority);
        const outcome = business.enqueue(runId, requestId, command);
        return {
          status: outcome.duplicate ? 200 : 202,
          body: asJson({
            recorded: true,
            eventId: outcome.eventId,
            duplicate: outcome.duplicate,
          }),
        };
      } catch {
        return {
          status: 400,
          body: { error: "Invalid request or unavailable business transition" },
        };
      }
    },
  };
  return {
    business,
    website: {
      health: () => business.health(),
      async publicContent() {
        const run = business.runtime
            .runs()
            .find((item) => item.profile === "foundation"),
          policy = business.policy(run?.id ?? "");
        return {
          availability: business.health(),
          offerings: [...business.services.values()]
            .filter(
              (service) =>
                policy.capabilities.includes(service.capability) &&
                policy.prices[service.name],
            )
            .map((service) => ({
              id: service.name,
              title: service.name.replaceAll("-", " "),
              description: `${service.description} Scoped price: ${policy.prices[service.name]} micro-USD.`,
            })),
          activity: business
            .all(
              "SELECT * FROM business_public_activity ORDER BY created_at DESC LIMIT 100",
            )
            .map((row) => ({
              id: String(row.id),
              at: String(row.created_at),
              text: `Completed ${business.services.has(String(row.service)) ? String(row.service) : "scoped work"}`,
            })),
        };
      },
      renderPublic: (data: Parameters<typeof renderBusinessPublic>[0]) =>
        renderBusinessPublic(data, business.contactEmail ?? undefined),
    },
    operatorPages: [
      {
        path: "/operator/modules/brief-business/console",
        async render({
          query,
          csrf,
        }: {
          query: Record<string, string>;
          csrf: string;
        }) {
          return renderBusinessPage(await projection(query), csrf, query);
        },
      },
    ],
    routes: [
      commandRoute,
      {
        path: "/api/modules/brief-business/console",
        method: "GET" as const,
        access: "operator" as const,
        async handle(request: { query: Record<string, string> }) {
          try {
            return {
              status: 200,
              body: asJson(await projection(request.query)),
            };
          } catch {
            return {
              status: 400,
              body: { error: "Invalid business view request" },
            };
          }
        },
      },
      {
        path: "/api/modules/brief-business/order",
        method: "GET" as const,
        access: "operator" as const,
        async handle(request: { query: Record<string, string> }) {
          try {
            return {
              status: 200,
              body: asJson(
                orderProjection(
                  business,
                  text(request.query.runId, 200),
                  text(request.query.orderId, 200),
                  true,
                ),
              ),
            };
          } catch {
            return { status: 404, body: { error: "Unknown order" } };
          }
        },
      },
    ],
  };
}
export const definition: ModuleDefinition = {
  id: "brief-business",
  contractVersion: 1,
  migrations,
  create: createBusinessModule,
};
