import type { Json } from "../../contracts/index.ts";
export class BusinessApiError extends Error {
  readonly status: number;
  constructor(status: number) {
    super("Business service request unavailable");
    this.status = status;
  }
}
export interface BusinessClient {
  request<T = Json>(path: string, body?: unknown): Promise<T>;
  runId: string | null;
}
export function createBusinessClient(
  env: NodeJS.ProcessEnv = process.env,
): BusinessClient | null {
  const address = env.WIDEMIND_BUSINESS_URL,
    token = env.WIDEMIND_BUSINESS_TOKEN;
  if (!address && !token) return null;
  if (
    !address ||
    !token ||
    token.length < 32 ||
    token.length > 512 ||
    !/^[\x21-\x7e]+$/.test(token)
  )
    throw new Error("Business URL and dedicated token required");
  const base = new URL(address);
  if (
    base.username ||
    base.password ||
    base.search ||
    base.hash ||
    base.pathname !== "/" ||
    (base.protocol !== "https:" &&
      !(
        base.protocol === "http:" &&
        ["127.0.0.1", "localhost", "[::1]"].includes(base.hostname)
      ))
  )
    throw new Error("Business URL requires HTTPS or loopback HTTP");
  return {
    runId: env.WIDEMIND_COMMERCE_RUN_ID ?? null,
    request: async <T>(path: string, body?: unknown) => {
      if (!/^\/v1\/[a-z-]+$/.test(path))
        throw new Error("Invalid business path");
      const response = await fetch(new URL(path, base), {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify(body ?? {}),
        redirect: "error",
        signal: AbortSignal.timeout(path === "/v1/step" ? 120000 : 15000),
      });
      const value = await response.text();
      if (Buffer.byteLength(value) > 2_000_000)
        throw new Error("Business response exceeds bound");
      if (!response.ok) throw new BusinessApiError(response.status);
      return JSON.parse(value) as T;
    },
  };
}
