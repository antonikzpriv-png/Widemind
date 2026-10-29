import { resolve } from "node:path";
import type { RuntimeConfig, ServerSecrets } from "./contracts/index.ts";
function integer(
  value: string | undefined,
  fallback: number,
  min: number,
  max: number,
) {
  const n = value === undefined ? fallback : Number(value);
  if (!Number.isSafeInteger(n) || n < min || n > max)
    throw new Error("Invalid numeric configuration");
  return n;
}
export function loadConfig(env: NodeJS.ProcessEnv = process.env): {
  config: RuntimeConfig;
  secrets: ServerSecrets;
} {
  const profile = env.WIDEMIND_PROFILE ?? "foundation";
  if (profile !== "foundation" && profile !== "demo")
    throw new Error("Unknown runtime profile");
  const host = env.WIDEMIND_HOST ?? "127.0.0.1";
  const port = integer(env.WIDEMIND_PORT, 3000, 1, 65535);
  const publicOrigin = env.WIDEMIND_PUBLIC_ORIGIN ?? `http://127.0.0.1:${port}`;
  const origin = new URL(publicOrigin);
  if (
    !["http:", "https:"].includes(origin.protocol) ||
    origin.origin !== publicOrigin
  )
    throw new Error("PUBLIC_ORIGIN must be an exact HTTP origin");
  const secureCookies = env.WIDEMIND_SECURE_COOKIES === "true";
  if (secureCookies && origin.protocol !== "https:")
    throw new Error("Secure cookies require an HTTPS public origin");
  if (origin.protocol === "https:" && !secureCookies)
    throw new Error("HTTPS requires secure cookies");
  if (!["127.0.0.1", "localhost", "::1"].includes(host) && !secureCookies)
    throw new Error(
      "Non-loopback serving requires HTTPS termination and secure cookies",
    );
  const publicRunId = env.WIDEMIND_COMMERCE_RUN_ID;
  if (
    publicRunId !== undefined &&
    !/^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$/.test(publicRunId)
  )
    throw new Error("Invalid public commerce run identity");
  return {
    config: {
      database: resolve(env.WIDEMIND_DATABASE ?? "./var/widemind.sqlite"),
      host,
      port,
      publicOrigin,
      ...(publicRunId ? { publicRunId } : {}),
      profile,
      secureCookies,
      leaseMs: integer(env.WIDEMIND_LEASE_MS, 30000, 3000, 3600000),
      dependencyMs: integer(env.WIDEMIND_DEPENDENCY_MS, 5000, 10, 60000),
      idleMs: integer(env.WIDEMIND_IDLE_MS, 60000, 1000, 86400000),
    },
    secrets: {
      operatorPasswordHash: env.WIDEMIND_OPERATOR_PASSWORD_HASH || null,
    },
  };
}
