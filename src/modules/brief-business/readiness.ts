import type { Json } from "../../contracts/index.ts";
import { https } from "./services.ts";
/** Owner configuration only; no provider, network or credential reads. */
export function configuredScopeValidator(env: NodeJS.ProcessEnv) {
  const stringList = (name: string): string[] => {
    const value = env[name];
    if (!value?.trim()) return [];
    const parsed: unknown = JSON.parse(value);
    if (
      !Array.isArray(parsed) ||
      parsed.length > 100 ||
      parsed.some((item) => typeof item !== "string")
    )
      throw new Error(`Invalid ${name}`);
    return parsed as string[];
  };
  const sources = stringList("WIDEMIND_RESEARCH_SOURCE_URLS").map(https);
  const hosts = stringList("WIDEMIND_RESEARCH_ALLOWED_HOSTS");
  if (
    hosts.some(
      (host) =>
        !/(?=^.{1,253}$)^[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?$/.test(host),
    )
  )
    throw new Error("Research allowed hosts must be exact hostnames");
  const allowed = new Set(
    (hosts.length ? hosts : sources.map((url) => new URL(url).hostname)).map(
      (host) => host.toLowerCase(),
    ),
  );
  const configuredInteger = (name: string, fallback: string) => {
    const raw = env[name] ?? fallback;
    if (!/^[0-9]+$/.test(raw)) throw new Error(`Invalid ${name}`);
    return BigInt(raw);
  };
  const maximumInput = configuredInteger("OPENAI_MAX_INPUT_BYTES", "24000"),
    maximumOutput = configuredInteger("OPENAI_MAX_OUTPUT_TOKENS", "1800");
  const inputPrice = env.OPENAI_INPUT_MICRO_USD_PER_MILLION_TOKENS,
    outputPrice = env.OPENAI_OUTPUT_MICRO_USD_PER_MILLION_TOKENS;
  return (service: string, scope: Record<string, Json>, allowance: string) => {
    if (service !== "research-brief") return;
    const urls = (scope.sourceUrls ?? sources) as string[];
    if (
      !Array.isArray(urls) ||
      urls.length < Number(scope.minSources) ||
      urls.length > 10
    )
      throw new Error("Configured sources cannot meet this scope");
    if (
      urls.some(
        (url) => !allowed.has(new URL(https(url)).hostname.toLowerCase()),
      )
    )
      throw new Error("Source host is outside the owner allowlist");
    if (
      BigInt(Buffer.byteLength(JSON.stringify(scope))) + 2048n >=
      maximumInput
    )
      throw new Error("Research scope exceeds configured input budget");
    if (
      !inputPrice ||
      !outputPrice ||
      !/^[0-9]+$/.test(inputPrice) ||
      !/^[0-9]+$/.test(outputPrice)
    )
      throw new Error("Explicit model prices are required");
    const cost =
      ((maximumInput + 2048n) * BigInt(inputPrice) + 999999n) / 1000000n +
      (maximumOutput * BigInt(outputPrice) + 999999n) / 1000000n;
    if (
      cost > BigInt(allowance) ||
      maximumInput + 2048n + maximumOutput > 30000n
    )
      throw new Error("Configured provider bounds exceed the quoted allowance");
  };
}
