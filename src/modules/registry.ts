import { readdir } from "node:fs/promises";
import type { DatabaseSync } from "node:sqlite";
import type {
  Business,
  Economy,
  Migration,
  ModuleRegistration,
  ModuleRoute,
  OperatorPage,
  RuntimeConfig,
  Tools,
  Website,
} from "../contracts/index.ts";
import { migrate } from "../db/migrate.ts";
export interface ModuleContext {
  /** Private construction input; never part of RuntimeConfig or public projections. */
  env?: NodeJS.ProcessEnv;
  db: DatabaseSync;
  config: RuntimeConfig;
}
export interface ModuleDefinition {
  id: string;
  contractVersion: 1;
  migrations?: readonly Migration[];
  /** Trusted composition after all role providers are registered. */
  decorate?(services: Services, context: ModuleContext): void;
  create(
    context: ModuleContext,
  ): Omit<ModuleRegistration, "id" | "contractVersion" | "migrations">;
}
export interface Services {
  economy: Economy;
  tools: Tools;
  business: Business;
  website: Website;
  routes: ModuleRoute[];
  operatorPages?: OperatorPage[];
}
const unavailable = (name: string) => ({
  available: false as const,
  reason: `${name} module is not configured`,
});
export function unavailableServices(): Services {
  return {
    routes: [],
    operatorPages: [],
    economy: {
      health: () => unavailable("Economy"),
      summary: async () => null,
      authorize: async () => ({
        kind: "unavailable",
        reason: "Economy module is not configured; no funds assumed",
      }),
      settle: async () => ({
        kind: "unavailable",
        reason: "Economy settlement is unavailable",
      }),
    },
    tools: {
      health: () => unavailable("Tools"),
      capabilities: () => [],
      execute: async () => ({
        kind: "unknown",
        reason: "Tools module is unavailable",
      }),
      reconcile: async () => ({
        kind: "unavailable",
        reason: "Tool reconciliation is unavailable",
      }),
    },
    business: {
      health: () => unavailable("Business"),
      selectWork: async () => ({
        kind: "unavailable",
        reason: "Business selection is unavailable",
      }),
    },
    website: {
      health: () => unavailable("Website commerce"),
      publicContent: async () => ({
        offerings: [],
        activity: [],
        availability: unavailable("Website commerce"),
      }),
    },
  };
}
export async function discoverModules(
  config: RuntimeConfig,
  directory = new URL("./", import.meta.url),
): Promise<ModuleDefinition[]> {
  const entries = await readdir(directory, { withFileTypes: true });
  const modules: ModuleDefinition[] = [];
  for (const entry of entries.sort((a, b) => a.name.localeCompare(b.name))) {
    if (
      !entry.isDirectory() ||
      (entry.name === "demo" && config.profile !== "demo")
    )
      continue;
    const files = await readdir(new URL(`${entry.name}/`, directory));
    const file = files.includes("module.js")
      ? "module.js"
      : files.includes("module.ts")
        ? "module.ts"
        : null;
    if (!file) continue;
    const imported = (await import(
      new URL(`${entry.name}/${file}`, directory).href
    )) as { definition: ModuleDefinition };
    if (
      !imported.definition ||
      imported.definition.id !== entry.name ||
      imported.definition.contractVersion !== 1 ||
      entry.name === "core"
    )
      throw new Error("Invalid module definition");
    modules.push(imported.definition);
  }
  return modules;
}
export function registerModules(
  context: ModuleContext,
  definitions: readonly ModuleDefinition[],
): Services {
  const ids = new Set<string>();
  for (const item of definitions) {
    if (ids.has(item.id)) throw new Error("Duplicate module ID");
    ids.add(item.id);
  }
  migrate(
    context.db,
    definitions.map((item) => ({
      id: item.id,
      contractVersion: 1,
      ...(item.migrations ? { migrations: item.migrations } : {}),
    })),
  );
  const services = unavailableServices();
  const roles = new Set<string>();
  for (const definition of definitions) {
    const providers = definition.create(context);
    for (const page of providers.operatorPages ?? []) {
      if (
        !page.path.startsWith(`/operator/modules/${definition.id}/`) ||
        !/^\/operator\/modules\/[a-z][a-z0-9_-]*\/[a-z0-9/-]+$/.test(
          page.path,
        ) ||
        typeof page.render !== "function"
      )
        throw new Error("Invalid module operator page");
      if (services.operatorPages!.some((item) => item.path === page.path))
        throw new Error("Duplicate module operator page");
      services.operatorPages!.push(page);
    }
    for (const route of providers.routes ?? []) {
      if (
        !route.path.startsWith(`/api/modules/${definition.id}/`) ||
        !/^\/api\/modules\/[a-z][a-z0-9_-]*\/[a-z0-9/-]+$/.test(route.path) ||
        !["GET", "POST"].includes(route.method) ||
        !["public", "operator"].includes(route.access)
      )
        throw new Error("Invalid module route");
      if (
        route.responseKind !== undefined &&
        (route.responseKind !== "sandboxed-html" ||
          route.access !== "public" ||
          route.method !== "POST")
      )
        throw new Error("Sandboxed HTML requires a public POST route");
      if (
        route.operatorRedirect !== undefined &&
        (route.method !== "POST" ||
          route.access !== "operator" ||
          !(providers.operatorPages ?? []).some(
            (page) => page.path === route.operatorRedirect,
          ))
      )
        throw new Error("Invalid module operator redirect");
      if (
        services.routes.some(
          (item) => item.path === route.path && item.method === route.method,
        )
      )
        throw new Error("Duplicate module route");
      services.routes.push(route);
    }
    for (const role of ["economy", "tools", "business", "website"] as const) {
      if (!providers[role]) continue;
      if (roles.has(role))
        throw new Error(
          `Multiple providers for ${role}; configure one provider`,
        );
      roles.add(role);
      Object.assign(services, { [role]: providers[role] });
    }
  }
  for (const definition of definitions)
    definition.decorate?.(services, context);
  return services;
}
