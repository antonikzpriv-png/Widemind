/** Opt-in private bridge. The foundation owns reservations and receipt settlement. */
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { delimiter, isAbsolute, resolve, sep } from "node:path";
import type {
  Availability,
  Reconciliation,
  Reservation,
  ToolAction,
  ToolResult,
  Tools,
} from "../../contracts/index.ts";
import type { ModuleContext, ModuleDefinition } from "../registry.ts";
import { createCommerceTools } from "../brief-business/commerce.ts";
import { assertCentralBusinessProtocol } from "../brief-business/protocol.ts";

const ids = ["gmail.read", "gmail.send", "research.collect"] as const;
const unknown = (): ToolResult => ({
  kind: "unknown",
  reason:
    "Private tool outcome unavailable; retain authorization and reconcile",
});
function has(env: NodeJS.ProcessEnv, keys: string[]): boolean {
  return keys.every(
    (key) => typeof env[key] === "string" && env[key]!.trim().length > 0,
  );
}
function list(env: NodeJS.ProcessEnv, key: string): string[] {
  const value: unknown = JSON.parse(env[key] ?? "[]");
  if (
    !Array.isArray(value) ||
    value.length > 100 ||
    value.some(
      (item) =>
        typeof item !== "string" || !item || /[\x00-\x1f\x7f]/.test(item),
    )
  )
    throw new Error("Invalid private provider list");
  return value as string[];
}
function configuredInteger(
  value: string | undefined,
  minimum: bigint,
  maximum: bigint,
): bigint {
  if (!value || !/^(0|[1-9][0-9]*)$/.test(value))
    throw new Error("Invalid private provider bound");
  const amount = BigInt(value);
  if (amount < minimum || amount > maximum)
    throw new Error("Invalid private provider bound");
  return amount;
}
function localReadiness(env: NodeJS.ProcessEnv): {
  gmail: boolean;
  research: boolean;
} {
  const validAddress = (value: string) =>
    /^[^\s<>@,;\x00-\x1f\x7f]+@[^\s<>@,;\x00-\x1f\x7f]+\.[^\s<>@,;\x00-\x1f\x7f]+$/.test(
      value,
    ) && value.length <= 254;
  const account = (value: string | undefined) =>
    typeof value === "string" &&
    /^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$/.test(value);
  let common = false;
  try {
    if (
      !has(env, [
        "WIDEMIND_BUSINESS_TOOLS_JOURNAL",
        "WIDEMIND_ECONOMY_URL",
        "WIDEMIND_GATEWAY_TOKEN",
      ]) ||
      !["provider-test", "live"].includes(
        env.WIDEMIND_BUSINESS_TOOLS_PROFILE ?? "",
      ) ||
      !isAbsolute(env.WIDEMIND_BUSINESS_TOOLS_JOURNAL!)
    )
      throw new Error("Missing private provider settings");
    const url = new URL(env.WIDEMIND_ECONOMY_URL!);
    if (
      !["https:", "http:"].includes(url.protocol) ||
      url.username ||
      url.password ||
      url.search ||
      url.hash ||
      url.pathname !== "/" ||
      (url.protocol === "http:" &&
        !["127.0.0.1", "localhost", "[::1]"].includes(url.hostname))
    )
      throw new Error("Invalid control endpoint");
    const token = env.WIDEMIND_GATEWAY_TOKEN!;
    if (
      token.length < 32 ||
      token.length > 512 ||
      /[\x00-\x20\x7f]/.test(token)
    )
      throw new Error("Invalid gateway identity");
    const recipients = list(env, "WIDEMIND_BUSINESS_ALLOWED_RECIPIENTS");
    configuredInteger(
      env.WIDEMIND_BUSINESS_GMAIL_DAILY_LIMIT ?? "500",
      1n,
      10000n,
    );
    configuredInteger(
      env.WIDEMIND_BUSINESS_RESEARCH_DAILY_LIMIT ?? "50",
      1n,
      1000n,
    );
    if (
      recipients.some((item) => !validAddress(item)) ||
      (env.WIDEMIND_BUSINESS_TOOLS_PROFILE === "provider-test" &&
        !recipients.length)
    )
      throw new Error("Invalid controlled recipients");
    common = true;
  } catch {
    return { gmail: false, research: false };
  }
  const gmail =
    common &&
    account(env.WIDEMIND_BUSINESS_GMAIL_ACCOUNT) &&
    validAddress(env.WIDEMIND_GMAIL_ADDRESS ?? "") &&
    has(env, ["GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET", "GMAIL_REFRESH_TOKEN"]);
  let research = false;
  try {
    if (
      !common ||
      !account(env.WIDEMIND_BUSINESS_OPENAI_ACCOUNT) ||
      !has(env, ["OPENAI_API_KEY", "OPENAI_MODEL"]) ||
      /[\x00-\x1f\x7f]/.test(env.OPENAI_MODEL!) ||
      env.OPENAI_MODEL!.length > 100
    )
      throw new Error("Missing research profile");
    const ordinary = configuredInteger(
      env.OPENAI_INPUT_MICRO_USD_PER_MILLION_TOKENS,
      1n,
      1000000000000n,
    );
    configuredInteger(
      env.OPENAI_OUTPUT_MICRO_USD_PER_MILLION_TOKENS,
      1n,
      1000000000000n,
    );
    configuredInteger(
      env.OPENAI_CACHED_INPUT_MICRO_USD_PER_MILLION_TOKENS,
      0n,
      ordinary,
    );
    configuredInteger(env.OPENAI_MAX_OUTPUT_TOKENS ?? "1800", 100n, 10000n);
    configuredInteger(env.OPENAI_MAX_INPUT_BYTES ?? "24000", 1000n, 100000n);
    const sources = list(env, "WIDEMIND_RESEARCH_SOURCE_URLS"),
      hosts = list(env, "WIDEMIND_RESEARCH_ALLOWED_HOSTS");
    if (
      sources.length > 10 ||
      new Set(sources).size !== sources.length ||
      hosts.some(
        (host) =>
          !/^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$/.test(host) ||
          !host.includes("."),
      )
    )
      throw new Error("Invalid source permissions");
    const sourceHosts = sources.map((value) => {
      const url = new URL(value);
      if (
        url.protocol !== "https:" ||
        url.username ||
        url.password ||
        url.hash ||
        (url.port && url.port !== "443") ||
        value.length > 2048 ||
        /\s/.test(value)
      )
        throw new Error("Invalid source URL");
      return url.hostname.toLowerCase();
    });
    const permitted = new Set(
      (hosts.length ? hosts : sourceHosts).map((host) => host.toLowerCase()),
    );
    if (!permitted.size || sourceHosts.some((host) => !permitted.has(host)))
      throw new Error("Missing exact source host permission");
    research = true;
  } catch {
    /* Advertise unavailable before any paid action. */
  }
  return { gmail, research };
}
export class BusinessTools implements Tools {
  readonly #env: NodeJS.ProcessEnv;
  readonly #readiness: Map<string, Availability>;
  constructor(env: NodeJS.ProcessEnv) {
    this.#env = { ...env };
    const { gmail, research } = localReadiness(env);
    this.#readiness = new Map(
      ids.map((id) => [
        id,
        (id === "research.collect" ? research : gmail)
          ? { available: true }
          : {
              available: false,
              reason:
                "Dedicated provider credentials, profile, sources, or protected journal are missing",
            },
      ]),
    );
  }
  health(): Availability {
    return [...this.#readiness.values()].some((item) => item.available)
      ? { available: true }
      : {
          available: false,
          reason: "Business providers require explicit account configuration",
        };
  }
  capabilities() {
    return ids.map((id) => ({ id, availability: this.#readiness.get(id)! }));
  }
  async call(
    operation: "execute" | "reconcile",
    action: ToolAction,
    reservation: Reservation,
    signal?: AbortSignal,
  ): Promise<Reconciliation> {
    if (!ids.includes(action.tool as (typeof ids)[number])) return unknown();
    if (
      operation === "execute" &&
      (!this.#readiness.get(action.tool)?.available || signal?.aborted)
    )
      return unknown();
    const input = JSON.stringify({ operation, action, reservation });
    if (Buffer.byteLength(input) > 1_500_000) return unknown();
    const timeout = Math.max(
      1,
      Math.min(
        action.limit.maxDurationMs,
        operation === "reconcile" ? 4000 : 300000,
      ),
    );
    const childEnv: NodeJS.ProcessEnv = {};
    // Preserve proxy/CA runtime configuration; provider credentials stay in this
    // trusted process environment and never become command arguments or prompts.
    for (const [key, value] of Object.entries(this.#env)) {
      if (
        /^(?:PATH|SYSTEMROOT|TMPDIR|TEMP|TMP|PYTHONPATH|PYTHONHOME|HTTP_PROXY|HTTPS_PROXY|ALL_PROXY|NO_PROXY|http_proxy|https_proxy|all_proxy|no_proxy|SSL_CERT_FILE|SSL_CERT_DIR|REQUESTS_CA_BUNDLE|CURL_CA_BUNDLE)$/.test(
          key,
        ) ||
        /^(?:WIDEMIND_BUSINESS_|OPENAI_|GMAIL_|WIDEMIND_RESEARCH_)/.test(key) ||
        [
          "WIDEMIND_GMAIL_ADDRESS",
          "WIDEMIND_ECONOMY_URL",
          "WIDEMIND_GATEWAY_TOKEN",
          "WIDEMIND_WORKSPACE",
          "WIDEMIND_DATABASE",
          "WIDEMIND_ECONOMY_DB",
        ].includes(key)
      )
        childEnv[key] = value;
    }
    childEnv.PYTHONPATH = [
      fileURLToPath(new URL("../../../src/", import.meta.url)),
      childEnv.PYTHONPATH,
    ]
      .filter(Boolean)
      .join(delimiter);
    return await new Promise<Reconciliation>((done) => {
      const child = spawn(
        this.#env.WIDEMIND_BUSINESS_PYTHON ?? "python3",
        ["-m", "widemind.business_tools.runtime"],
        { env: childEnv, stdio: ["pipe", "pipe", "pipe"], shell: false },
      );
      const chunks: Buffer[] = [];
      let size = 0,
        finished = false;
      const finish = (result: Reconciliation) => {
        if (finished) return;
        finished = true;
        clearTimeout(timer);
        signal?.removeEventListener("abort", abort);
        done(result);
      };
      const abort = () => {
        child.kill("SIGKILL");
        finish(unknown());
      };
      const timer = setTimeout(abort, timeout);
      signal?.addEventListener("abort", abort, { once: true });
      if (signal?.aborted) {
        abort();
        return;
      }
      child.stdout.on("data", (chunk: Buffer) => {
        size += chunk.length;
        if (size > 1_500_000) abort();
        else chunks.push(chunk);
      });
      // Provider exceptions can contain private URLs. Drain without logging.
      child.stderr.on("data", () => {});
      child.stdin.on("error", () => {});
      child.on("error", () => finish(unknown()));
      child.on("close", (code) => {
        if (code !== 0) {
          finish(unknown());
          return;
        }
        try {
          const result = JSON.parse(
            Buffer.concat(chunks).toString("utf8"),
          ) as Reconciliation;
          if (
            result.kind === "receipt" &&
            result.receipt.actionId === action.actionId &&
            result.receipt.version === 1 &&
            ["succeeded", "failed"].includes(result.receipt.status)
          )
            finish(result);
          else if (result.kind === "unknown" || result.kind === "unavailable")
            finish(unknown());
          else finish(unknown());
        } catch {
          finish(unknown());
        }
      });
      child.stdin.end(input);
    });
  }
  async execute(
    action: ToolAction,
    reservation: Reservation,
    signal: AbortSignal,
  ): Promise<ToolResult> {
    return (await this.call(
      "execute",
      action,
      reservation,
      signal,
    )) as ToolResult;
  }
  async reconcile(
    action: ToolAction,
    reservation: Reservation,
  ): Promise<Reconciliation> {
    return await this.call("reconcile", action, reservation);
  }
}

export function createBusinessToolsModule(
  context: ModuleContext,
  env: NodeJS.ProcessEnv = context.env ?? process.env,
): ReturnType<ModuleDefinition["create"]> {
  if (!env.WIDEMIND_BUSINESS_TOOLS_PROFILE) return {};
  assertCentralBusinessProtocol(env);
  if (
    context.config.profile !== "foundation" ||
    !["provider-test", "live"].includes(env.WIDEMIND_BUSINESS_TOOLS_PROFILE)
  )
    throw new Error(
      "Business tools require an explicit foundation provider-test/live profile",
    );
  if (env.WIDEMIND_BUSINESS_TOOLS_JOURNAL) {
    const path = resolve(env.WIDEMIND_BUSINESS_TOOLS_JOURNAL);
    if (
      !isAbsolute(env.WIDEMIND_BUSINESS_TOOLS_JOURNAL) ||
      path === resolve(context.config.database) ||
      [env.WIDEMIND_WORKSPACE, env.WIDEMIND_BUSINESS_WORKSPACE].some(
        (root) =>
          root &&
          (path === resolve(root) || path.startsWith(resolve(root) + sep)),
      )
    )
      throw new Error(
        "Business tool journal requires a separate absolute protected path",
      );
  }
  const providers = new BusinessTools({
    ...env,
    WIDEMIND_BUSINESS_STATE_DB: resolve(context.config.database),
  });
  const commerce = createCommerceTools(context, env);
  const commerceIds = new Set(["commerce.checkout", "commerce.fulfill"]);
  return {
    tools: {
      health: () =>
        providers.health().available || commerce?.health().available
          ? { available: true }
          : providers.health(),
      capabilities: () => [
        ...providers.capabilities(),
        ...(commerce?.capabilities() ?? []),
      ],
      execute: (action, reservation, signal) =>
        commerceIds.has(action.tool) && commerce
          ? commerce.execute(action, reservation, signal)
          : providers.execute(action, reservation, signal),
      reconcile: (action, reservation) =>
        commerceIds.has(action.tool) && commerce
          ? commerce.reconcile(action, reservation)
          : providers.reconcile(action, reservation),
    },
  };
}
export const definition: ModuleDefinition = {
  id: "business-tools",
  contractVersion: 1,
  create: createBusinessToolsModule,
};
