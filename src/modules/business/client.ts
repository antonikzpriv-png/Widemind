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
      const chunks: Uint8Array[] = [];
      let size = 0;
      const reader = response.body?.getReader();
      if (!reader) throw new Error("Business service returned no body");
      try {
        while (true) {
          const part = await reader.read();
          if (part.done) break;
          size += part.value.byteLength;
          if (size > 2_000_000) {
            await reader.cancel();
            throw new Error("Business response exceeds bound");
          }
          chunks.push(part.value);
        }
      } finally {
        reader.releaseLock();
      }
      const value = Buffer.concat(chunks).toString("utf8");
      if (!response.ok) throw new BusinessApiError(response.status);
      return JSON.parse(value) as T;
    },
  };
}
