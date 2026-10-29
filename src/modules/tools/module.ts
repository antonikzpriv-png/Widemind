import type { ModuleDefinition } from "../registry.ts";
import type {
  Availability,
  Reconciliation,
  Tools,
  ToolResult,
} from "../../contracts/index.ts";

export interface ToolsConnection {
  url: string;
  token: string;
}
export function connection(
  env: NodeJS.ProcessEnv = process.env,
): ToolsConnection | null {
  if (!env.WIDEMIND_TOOLS_URL || !env.WIDEMIND_TOOLS_TOKEN) return null;
  const url = new URL(env.WIDEMIND_TOOLS_URL);
  if (
    url.username ||
    url.password ||
    url.search ||
    url.hash ||
    url.pathname !== "/" ||
    !["http:", "https:"].includes(url.protocol)
  )
    throw new Error("Invalid tools service origin");
  if (
    url.protocol === "http:" &&
    !["127.0.0.1", "localhost", "[::1]"].includes(url.hostname)
  )
    throw new Error("Tools HTTP is loopback-only");
  if (env.WIDEMIND_TOOLS_TOKEN.length < 32)
    throw new Error("Tools service token must contain at least 32 characters");
  return { url: url.origin, token: env.WIDEMIND_TOOLS_TOKEN };
}
export async function toolsRequest(
  config: ToolsConnection,
  path: string,
  body?: unknown,
  signal?: AbortSignal,
): Promise<unknown> {
  const response = await fetch(config.url + path, {
    method: body === undefined ? "GET" : "POST",
    headers: {
      Authorization: `Bearer ${config.token}`,
      "Content-Type": "application/json",
    },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    signal: signal ?? AbortSignal.timeout(15000),
    redirect: "error",
  });
  const reader = response.body?.getReader();
  if (!reader) throw new Error("Tools service returned no body");
  let size = 0;
  const chunks: Uint8Array[] = [];
  while (true) {
    const part = await reader.read();
    if (part.done) break;
    size += part.value.length;
    if (size > 2_000_000) {
      await reader.cancel();
      throw new Error("Tools response exceeds the transport limit");
    }
    chunks.push(part.value);
  }
  if (!response.ok)
    throw new Error(`Tools request rejected (${response.status})`);
  return JSON.parse(Buffer.concat(chunks).toString("utf8"));
}
export function createTools(
  config: ToolsConnection,
): Tools & { refresh(): Promise<void> } {
  let availability: Availability = {
    available: false,
    reason: "Tools service has not been checked",
  };
  let capabilities: ReturnType<Tools["capabilities"]> = [];
  return {
    health: () => availability,
    capabilities: () => capabilities,
    async refresh() {
      try {
        const data = (await toolsRequest(config, "/capabilities")) as {
          capabilities: ReturnType<Tools["capabilities"]>;
        };
        if (!Array.isArray(data.capabilities))
          throw new Error("Invalid capabilities");
        capabilities = data.capabilities;
        availability = { available: true };
      } catch {
        availability = {
          available: false,
          reason: "Tools service unavailable or unauthenticated",
        };
        capabilities = [];
      }
    },
    async execute(action, reservation, signal): Promise<ToolResult> {
      const cancel = () => {
        void toolsRequest(config, "/cancel", {
          actionId: action.actionId,
        }).catch(() => {});
      };
      signal.addEventListener("abort", cancel, { once: true });
      try {
        return (await toolsRequest(
          config,
          "/execute",
          { action, reservation },
          signal,
        )) as ToolResult;
      } catch {
        return {
          kind: "unknown",
          reason: "Tools transport interrupted; reconcile before retry",
        };
      } finally {
        signal.removeEventListener("abort", cancel);
      }
    },
    async reconcile(action, reservation): Promise<Reconciliation> {
      try {
        return (await toolsRequest(config, "/reconcile", {
          action,
          reservation,
        })) as Reconciliation;
      } catch {
        return {
          kind: "unavailable",
          reason: "Tools reconciliation unavailable; reservation retained",
        };
      }
    },
  };
}
export const definition: ModuleDefinition = {
  id: "tools",
  contractVersion: 1,
  create({ config }) {
    const target = config.profile === "demo" ? null : connection();
    if (!target) return {};
    const tools = createTools(target);
    // Constructors never perform network work. Availability is refreshed by the
    // private status route or an actual authorized runtime execution cycle.
    return {
      tools,
      routes: [
        {
          path: "/api/modules/tools/status",
          method: "GET",
          access: "operator",
          handle: async () => {
            await tools.refresh();
            return {
              status: 200,
              body: {
                availability: tools.health(),
                capabilities: [...tools.capabilities()],
              },
            };
          },
        },
        {
          path: "/api/modules/tools/artifact",
          method: "POST",
          access: "operator",
          handle: async (request) => ({
            status: 200,
            body: (await toolsRequest(
              target,
              "/artifacts/read",
              request.body,
            )) as import("../../contracts/index.ts").Json,
          }),
        },
      ],
    };
  },
};
