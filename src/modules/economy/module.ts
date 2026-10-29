/** Normal foundation module registration for the single Python economy authority. */
import type { Json } from "../../contracts/index.ts";
import { Store } from "../../runtime/store.ts";
import type { ModuleContext, ModuleDefinition } from "../registry.ts";
import { EconomyClient, bridgeSettings } from "./client.ts";

export function createEconomyModule(
  { db, config }: ModuleContext,
  env: NodeJS.ProcessEnv = process.env,
): ReturnType<ModuleDefinition["create"]> {
  const settings = bridgeSettings(env);
  if (!settings) return {};
  if (config.profile !== "foundation")
    throw new Error(
      "The economy bridge requires the foundation profile; disable the demo economy",
    );
  const economy = new EconomyClient(settings),
    store = new Store(db);
  return {
    economy,
    routes: [
      {
        path: "/api/modules/economy/open",
        method: "POST",
        access: "operator",
        async handle(request) {
          if (!request.operator)
            return {
              status: 403,
              body: { error: "Operator authorization required" },
            };
          const body = request.body;
          if (
            !body ||
            typeof body !== "object" ||
            Array.isArray(body) ||
            Object.keys(body).some((key) => !["runId", "mode"].includes(key)) ||
            typeof body.runId !== "string" ||
            !["sandbox", "live"].includes(String(body.mode))
          )
            return {
              status: 422,
              body: {
                error:
                  "A runtime run ID and explicit sandbox/live mode are required",
              },
            };
          const run = store.run(body.runId);
          if (!run)
            return { status: 404, body: { error: "Runtime run not found" } };
          if (run.profile !== "foundation")
            return {
              status: 409,
              body: {
                error: "Only foundation runs can use the authoritative economy",
              },
            };
          try {
            const summary = await economy.seed(
              run.id,
              body.mode as "sandbox" | "live",
            );
            if (!summary || summary.mode !== body.mode)
              throw new Error("Invalid mode");
            return { status: 200, body: summary as unknown as Json };
          } catch {
            return {
              status: 503,
              body: {
                error:
                  "Economy run funding could not be verified; inspect the existing run before retrying",
              },
            };
          }
        },
      },
    ],
  };
}

export const definition: ModuleDefinition = {
  id: "economy",
  contractVersion: 1,
  create: createEconomyModule,
};
