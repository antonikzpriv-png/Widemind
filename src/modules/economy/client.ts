import { createHmac, timingSafeEqual } from "node:crypto";
import type {
  Economy,
  FinancialSummary,
  Json,
  Reservation,
  ToolReceipt,
  BudgetLimit,
} from "../../contracts/index.ts";
export type EconomyRole = "operator" | "gateway" | "agent";
export class EconomyApiError extends Error {
  readonly status: number;
  readonly code: string;
  constructor(status: number, code: string) {
    super(`Economy operation unavailable (${code})`);
    this.status = status;
    this.code = code;
  }
}
export interface EconomyClient {
  readonly toolsAccount: string;
  request<T = unknown>(
    role: EconomyRole,
    path: string,
    body?: unknown,
    method?: "GET" | "POST",
  ): Promise<T>;
  raw(
    path: string,
    body: Uint8Array,
    headers: Record<string, string>,
  ): Promise<{ status: number; body: Json }>;
  sign(value: unknown): string;
  verify(value: unknown, signature: string): boolean;
}
const MAX_SAFE_MONEY = 9_000_000_000_000_000n;
export function exactAmount(value: unknown): number {
  if (
    typeof value !== "string" ||
    !/^(0|[1-9][0-9]*)$/.test(value) ||
    BigInt(value) > MAX_SAFE_MONEY
  )
    throw new Error(
      "Expected canonical micro-USD string within service bounds",
    );
  return Number(value);
}
export function identifier(value: unknown): string {
  if (
    typeof value !== "string" ||
    !/^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$/.test(value)
  )
    throw new Error("Invalid economy identifier");
  return value;
}
export function createEconomyClient(
  env: NodeJS.ProcessEnv = process.env,
): EconomyClient | null {
  const address = env.WIDEMIND_ECONOMY_URL;
  const tokens = {
    operator: env.WIDEMIND_ECONOMY_OPERATOR_TOKEN,
    gateway: env.WIDEMIND_ECONOMY_GATEWAY_TOKEN,
    agent: env.WIDEMIND_ECONOMY_AGENT_TOKEN,
  };
  if (!address && !Object.values(tokens).some(Boolean)) return null;
  if (
    !address ||
    Object.values(tokens).some(
      (token) =>
        !token ||
        token.length < 32 ||
        token.length > 512 ||
        !/^[\x21-\x7e]+$/.test(token),
    ) ||
    new Set(Object.values(tokens)).size !== 3
  )
    throw new Error(
      "Economy URL and distinct operator/gateway/agent tokens are required",
    );
  const base = new URL(address);
  if (
    base.username ||
    base.password ||
    base.search ||
    base.hash ||
    base.pathname !== "/" ||
    (!["https:"].includes(base.protocol) &&
      !(
        base.protocol === "http:" &&
        ["127.0.0.1", "localhost", "[::1]"].includes(base.hostname)
      ))
  )
    throw new Error("Economy URL must be HTTPS or loopback HTTP origin");
  const toolsAccount = identifier(
    env.WIDEMIND_TOOLS_ACCOUNT ?? "dedicated-tools",
  );
  const request = async <T>(
    role: EconomyRole,
    path: string,
    body?: unknown,
    method?: "GET" | "POST",
  ): Promise<T> => {
    if (!path.startsWith("/v1/") || path.includes(".."))
      throw new Error("Invalid economy path");
    const response = await fetch(new URL(path, base), {
      method: method ?? (body === undefined ? "GET" : "POST"),
      headers: {
        Authorization: `Bearer ${tokens[role]}`,
        ...(body === undefined ? {} : { "Content-Type": "application/json" }),
      },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      redirect: "error",
      signal: AbortSignal.timeout(60000),
    });
    const chunks: Uint8Array[] = [];
    let size = 0;
    if (response.body) {
      const reader = response.body.getReader();
      try {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          size += value.byteLength;
          if (size > 2_097_152) {
            await reader.cancel();
            throw new Error("Economy response exceeded bound");
          }
          chunks.push(value);
        }
      } finally {
        reader.releaseLock();
      }
    }
    const text = Buffer.concat(chunks).toString("utf8");
    const parsed = JSON.parse(text);
    if (!response.ok)
      throw new EconomyApiError(
        response.status,
        typeof parsed?.error?.code === "string"
          ? parsed.error.code
          : "request_failed",
      );
    return parsed as T;
  };
  const sign = (value: unknown) =>
    createHmac("sha256", tokens.gateway!)
      .update(JSON.stringify(value))
      .digest("hex");
  return {
    toolsAccount,
    request,
    sign,
    verify: (value, signature) => {
      if (!/^[a-f0-9]{64}$/.test(signature)) return false;
      return timingSafeEqual(
        Buffer.from(sign(value), "hex"),
        Buffer.from(signature, "hex"),
      );
    },
    raw: async (path, body, headers) => {
      if (!["/v1/webhooks/stripe", "/v1/webhooks/paypal"].includes(path))
        throw new Error("Invalid public economy ingress");
      const response = await fetch(new URL(path, base), {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...Object.fromEntries(
            Object.entries(headers).filter(
              ([name]) =>
                name === "stripe-signature" || name.startsWith("paypal-"),
            ),
          ),
        },
        body: Buffer.from(body),
        redirect: "error",
        signal: AbortSignal.timeout(60000),
      });
      const parsed = (await response.json()) as Json;
      return { status: response.status, body: parsed };
    },
  };
}
export interface EconomyReservationRow {
  reservation_id: string;
  run_id: string;
  action_id: string;
  amount_micro_usd: number;
  provider: string;
  account: string;
  limits: Record<string, number>;
  state: string;
  actual_micro_usd: number | null;
  provisional_micro_usd: number;
}
function limitRecord(limit: BudgetLimit) {
  return {
    max_duration_ms: limit.maxDurationMs,
    max_storage_bytes: limit.maxOutputBytes,
    ...(limit.maxTokens === undefined ? {} : { max_tokens: limit.maxTokens }),
  };
}
function binding(reservation: Reservation) {
  return {
    reservationId: reservation.reservationId,
    runId: reservation.runId,
    actionId: reservation.actionId,
    limit: reservation.limit,
  };
}
export function createEconomyAdapter(client: EconomyClient): Economy {
  return {
    health: () => ({ available: true }),
    summary: async (runId) => {
      try {
        return await client.request<FinancialSummary>(
          "operator",
          `/v1/runs/${identifier(runId)}/runtime-summary`,
        );
      } catch (error) {
        if (error instanceof EconomyApiError && error.status === 404)
          return null;
        throw error;
      }
    },
    authorize: async (request) => {
      try {
        let row = await client.request<EconomyReservationRow>(
          "gateway",
          `/v1/runs/${identifier(request.runId)}/reservations`,
          {
            action_id: identifier(request.actionId),
            amount_micro_usd: exactAmount(request.limit.maximum.micros),
            idempotency_key: `runtime-${request.actionId}`,
            provider: identifier(request.tool),
            account: client.toolsAccount,
            limits: limitRecord(request.limit),
          },
        );
        row = await client.request<EconomyReservationRow>(
          "gateway",
          `/v1/reservations/${identifier(row.reservation_id)}`,
        );
        if (
          row.run_id !== request.runId ||
          row.action_id !== request.actionId ||
          row.provider !== request.tool ||
          row.account !== client.toolsAccount ||
          row.amount_micro_usd !== exactAmount(request.limit.maximum.micros)
        )
          throw new Error("Authorization binding mismatch");
        if (!["reserved", "running", "unknown"].includes(row.state))
          return {
            kind: "unavailable",
            reason: "Reservation is closed; reconcile the durable action",
          };
        const reservation: Reservation = {
          reservationId: row.reservation_id,
          runId: request.runId,
          actionId: request.actionId,
          limit: request.limit,
          authorization: "",
        };
        reservation.authorization = client.sign(binding(reservation));
        return { kind: "authorized", reservation };
      } catch (error) {
        if (error instanceof EconomyApiError)
          return {
            kind:
              error.code === "insufficient_funds"
                ? "insufficient"
                : "unavailable",
            reason: error.message,
          };
        throw error;
      }
    },
    settle: async (reservation, receipt) => {
      if (
        !client.verify(binding(reservation), reservation.authorization) ||
        receipt.actionId !== reservation.actionId
      )
        throw new Error("Invalid economy authorization or receipt identity");
      const row = await client.request<EconomyReservationRow>(
        "gateway",
        `/v1/reservations/${identifier(reservation.reservationId)}`,
      );
      if (
        row.run_id !== reservation.runId ||
        row.action_id !== receipt.actionId ||
        row.provider !== receipt.usage.provider ||
        row.account !== receipt.usage.accountRef
      )
        throw new Error("Usage does not match the authorized provider/account");
      const usage = {
        evidence_ref: receipt.usage.evidenceRef,
        billable_failure: receipt.usage.billableFailure ? 1 : 0,
      };
      await client.request(
        "gateway",
        `/v1/reservations/${identifier(reservation.reservationId)}/settle`,
        {
          actual_micro_usd: exactAmount(receipt.usage.actualCharge.micros),
          provider: row.provider,
          account: row.account,
          external_charge_id: identifier(receipt.usage.externalEventId),
          usage,
          outcome: receipt.usage.provisional ? "unknown" : receipt.status,
        },
      );
      return receipt.usage.provisional
        ? {
            kind: "unavailable",
            reason:
              "Provisional usage posted; reservation retained until final provider reconciliation",
          }
        : { kind: "settled" };
    },
  };
}
