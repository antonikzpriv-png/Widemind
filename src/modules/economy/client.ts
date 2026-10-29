/** Private bounded RPC adapter. Money crosses this boundary only as exact strings. */
import type {
  Authorization,
  BudgetLimit,
  Economy,
  FinancialSummary,
  Money,
  Reservation,
  ToolReceipt,
} from "../../contracts/index.ts";

const INT64_MAX = 9223372036854775807n;
const RESPONSE_BYTES = 65_536;
const REQUEST_BYTES = 16_384;
export const SUMMARY_OPTIONAL_AMOUNTS = [
  "processorFeesMicros",
  "refundsMicros",
  "chargebacksMicros",
  "chargebackReversalsMicros",
  "paymentAdjustmentsMicros",
  "correctionsMicros",
  "testFundingMicros",
  "otherCashMicros",
] as const;
type OptionalAmount = (typeof SUMMARY_OPTIONAL_AMOUNTS)[number];
type Summary = FinancialSummary & Partial<Record<OptionalAmount, string>>;

export interface BridgeSettings {
  url: string;
  gatewayToken: string;
  operatorToken: string;
  timeoutMs: number;
}
class InvalidBoundary extends Error {}
const invalid = () => new InvalidBoundary("Invalid economy boundary value");
function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value))
    throw invalid();
  return value as Record<string, unknown>;
}
function identifier(value: unknown): string {
  if (
    typeof value !== "string" ||
    !/^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$/.test(value)
  )
    throw invalid();
  return value;
}
function boundedString(value: unknown, max: number): string {
  if (
    typeof value !== "string" ||
    value.length < 1 ||
    value.length > max ||
    /[\x00-\x1f\x7f]/.test(value)
  )
    throw invalid();
  return value;
}
function amount(value: unknown, signed = false, aggregate = false): string {
  if (
    typeof value !== "string" ||
    value.length > (aggregate ? 129 : 20) ||
    (aggregate && value.replace("-", "").length > 128) ||
    !(signed ? /^(0|-?[1-9][0-9]*)$/ : /^(0|[1-9][0-9]*)$/).test(value)
  )
    throw invalid();
  const parsed = BigInt(value);
  if (!aggregate && (parsed > INT64_MAX || parsed < -INT64_MAX - 1n))
    throw invalid();
  return value;
}
function finiteInteger(value: unknown, minimum: number): number {
  if (!Number.isSafeInteger(value) || Number(value) < minimum) throw invalid();
  return value as number;
}
export function validateMoney(value: unknown): Money {
  const row = object(value);
  if (row.currency !== "USD") throw invalid();
  return { currency: "USD", micros: amount(row.micros) };
}
export function validateLimit(value: unknown): BudgetLimit {
  const row = object(value);
  if (
    Object.keys(row).some(
      (key) =>
        !["maximum", "maxTokens", "maxDurationMs", "maxOutputBytes"].includes(
          key,
        ),
    )
  )
    throw invalid();
  const limit: BudgetLimit = {
    maximum: validateMoney(row.maximum),
    maxDurationMs: finiteInteger(row.maxDurationMs, 1),
    maxOutputBytes: finiteInteger(row.maxOutputBytes, 1),
  };
  if (row.maxTokens !== undefined)
    limit.maxTokens = finiteInteger(row.maxTokens, 1);
  return limit;
}
function equalLimit(first: BudgetLimit, second: BudgetLimit): boolean {
  return (
    first.maximum.currency === second.maximum.currency &&
    first.maximum.micros === second.maximum.micros &&
    first.maxTokens === second.maxTokens &&
    first.maxDurationMs === second.maxDurationMs &&
    first.maxOutputBytes === second.maxOutputBytes
  );
}
export function validateSummary(value: unknown): Summary | null {
  if (value === null) return null;
  const row = object(value);
  if (row.currency !== "USD" || !["sandbox", "live"].includes(String(row.mode)))
    throw invalid();
  if (
    typeof row.asOf !== "string" ||
    row.asOf.length > 40 ||
    !/^\d{4}-\d\d-\d\dT/.test(row.asOf) ||
    !/(Z|\+00:00)$/.test(row.asOf) ||
    !Number.isFinite(Date.parse(row.asOf))
  )
    throw invalid();
  const result: Summary = {
    currency: "USD",
    openingCreditMicros: amount(row.openingCreditMicros, false, true),
    postedMicros: amount(row.postedMicros, true, true),
    reservedMicros: amount(row.reservedMicros, false, true),
    holdsMicros: amount(row.holdsMicros, false, true),
    spendableMicros: amount(row.spendableMicros, true, true),
    customerReceiptsMicros: amount(row.customerReceiptsMicros, false, true),
    earnedRevenueMicros: amount(row.earnedRevenueMicros, true, true),
    operatingChargesMicros: amount(row.operatingChargesMicros, false, true),
    ownerFundingMicros: amount(row.ownerFundingMicros, false, true),
    subsidiesMicros: amount(row.subsidiesMicros, true, true),
    mode: row.mode as "sandbox" | "live",
    asOf: row.asOf,
  };
  for (const key of SUMMARY_OPTIONAL_AMOUNTS) {
    if (row[key] === undefined) continue;
    const signed = ![
      "refundsMicros",
      "chargebacksMicros",
      "testFundingMicros",
    ].includes(key);
    result[key] = amount(row[key], signed, true);
  }
  if (
    BigInt(result.postedMicros) -
      BigInt(result.reservedMicros) -
      BigInt(result.holdsMicros) !==
    BigInt(result.spendableMicros)
  )
    throw invalid();
  if (
    SUMMARY_OPTIONAL_AMOUNTS.slice(0, 7).every(
      (key) => result[key] !== undefined,
    )
  ) {
    const cash =
      BigInt(result.openingCreditMicros) +
      BigInt(result.ownerFundingMicros) +
      BigInt(result.testFundingMicros!) +
      BigInt(result.customerReceiptsMicros) +
      BigInt(result.chargebackReversalsMicros!) +
      BigInt(result.paymentAdjustmentsMicros!) +
      BigInt(result.correctionsMicros!) +
      BigInt(result.otherCashMicros ?? "0") -
      BigInt(result.operatingChargesMicros) -
      BigInt(result.processorFeesMicros!) -
      BigInt(result.refundsMicros!) -
      BigInt(result.chargebacksMicros!);
    if (cash !== BigInt(result.postedMicros)) throw invalid();
  }
  return result;
}
export function validateReservation(value: unknown): Reservation {
  const row = object(value);
  return {
    reservationId: identifier(row.reservationId),
    actionId: identifier(row.actionId),
    runId: identifier(row.runId),
    limit: validateLimit(row.limit),
    authorization: boundedString(row.authorization, 2048),
  };
}
function accountingReceipt(
  value: ToolReceipt,
  reservation: Reservation,
): ToolReceipt {
  const row = object(value),
    source = object(row.usage);
  if (
    row.version !== 1 ||
    row.actionId !== reservation.actionId ||
    !["succeeded", "failed"].includes(String(row.status)) ||
    typeof source.billableFailure !== "boolean" ||
    typeof source.provisional !== "boolean"
  )
    throw invalid();
  let metadataOmitted = false;
  const usage: ToolReceipt["usage"] = {
    actualCharge: validateMoney(source.actualCharge),
    provider: boundedString(source.provider, 200),
    accountRef: boundedString(source.accountRef, 200),
    externalEventId: boundedString(source.externalEventId, 200),
    billableFailure: source.billableFailure,
    provisional: source.provisional,
    evidenceRef: "omitted:invalid-provider-evidence",
  };
  try {
    usage.evidenceRef = boundedString(source.evidenceRef, 2048);
  } catch {
    metadataOmitted = true;
  }
  if (source.nativeAmount !== undefined) {
    try {
      const native = object(source.nativeAmount);
      usage.nativeAmount = {
        currency: boundedString(native.currency, 32),
        minorUnits: amount(native.minorUnits, true, true),
        conversionRef: boundedString(native.conversionRef, 2048),
        rounding: boundedString(native.rounding, 2048),
      };
    } catch {
      metadataOmitted = true;
    }
  }

  const receipt: ToolReceipt = {
    version: 1,
    actionId: reservation.actionId,
    status: row.status as "succeeded" | "failed",
    result: {
      omitted: true,
      reason: "Result retained in runtime action store",
    },
    usage,
  };
  if (row.externalRef !== undefined) {
    try {
      receipt.externalRef = boundedString(row.externalRef, 2048);
    } catch {
      (receipt.result as Record<string, unknown>).usageMetadataOmitted = true;
    }
  }
  return receipt;
}

function token(value: string | undefined): string {
  if (
    !value ||
    value.length < 32 ||
    value.length > 512 ||
    !/^[\x21-\x7e]+$/.test(value)
  )
    throw invalid();
  return value;
}
export function bridgeSettings(env: NodeJS.ProcessEnv): BridgeSettings | null {
  const configured = [
    env.WIDEMIND_ECONOMY_URL,
    env.WIDEMIND_GATEWAY_TOKEN,
    env.WIDEMIND_OPERATOR_TOKEN,
  ];
  // Economy tokens alone may configure the Python service without opting Node in.
  if (!env.WIDEMIND_ECONOMY_URL) return null;
  try {
    if (!configured.every(Boolean)) throw invalid();
    if (/[\x00-\x1f\x7f]/.test(env.WIDEMIND_ECONOMY_URL)) throw invalid();
    const url = new URL(env.WIDEMIND_ECONOMY_URL);
    if (
      url.username ||
      url.password ||
      url.search ||
      url.hash ||
      url.pathname !== "/" ||
      !["http:", "https:"].includes(url.protocol) ||
      (url.protocol === "http:" &&
        !["127.0.0.1", "localhost", "[::1]"].includes(url.hostname))
    )
      throw invalid();
    const gatewayToken = token(env.WIDEMIND_GATEWAY_TOKEN),
      operatorToken = token(env.WIDEMIND_OPERATOR_TOKEN);
    if (gatewayToken === operatorToken) throw invalid();
    const rawTimeout = env.WIDEMIND_ECONOMY_TIMEOUT_MS ?? "4000";
    if (!/^[0-9]{1,4}$/.test(rawTimeout)) throw invalid();
    const timeoutMs = Number(rawTimeout);
    if (timeoutMs < 100 || timeoutMs > 4000) throw invalid();
    return { url: url.origin, gatewayToken, operatorToken, timeoutMs };
  } catch {
    throw new Error(
      "Invalid economy bridge configuration; use a private HTTP loopback or HTTPS endpoint and distinct role tokens",
    );
  }
}

export class EconomyClient implements Economy {
  readonly #settings: BridgeSettings;
  readonly #fetch: typeof fetch;
  constructor(settings: BridgeSettings, transport: typeof fetch = fetch) {
    this.#settings = settings;
    this.#fetch = transport;
  }
  health() {
    return { available: true as const };
  }
  async #rpc(
    operation: string,
    body: unknown,
    operator = false,
  ): Promise<unknown> {
    const controller = new AbortController();
    let timeout: ReturnType<typeof setTimeout>;
    const deadline = new Promise<never>((_resolve, reject) => {
      timeout = setTimeout(() => {
        controller.abort();
        reject(new Error("Economy deadline exceeded"));
      }, this.#settings.timeoutMs);
    });
    let reader: ReadableStreamDefaultReader<Uint8Array> | undefined;
    const operationPromise = (async () => {
      const request = JSON.stringify(body);
      if (Buffer.byteLength(request) > REQUEST_BYTES) throw invalid();
      const response = await this.#fetch(
        new URL(`/v1/runtime/${operation}`, this.#settings.url),
        {
          method: "POST",
          redirect: "error",
          signal: controller.signal,
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${operator ? this.#settings.operatorToken : this.#settings.gatewayToken}`,
          },
          body: request,
        },
      );
      if (!response.ok || !response.body) throw invalid();
      const declared = response.headers.get("content-length");
      if (
        declared &&
        (!/^[0-9]+$/.test(declared) ||
          BigInt(declared) > BigInt(RESPONSE_BYTES))
      )
        throw invalid();
      reader = response.body.getReader();
      let bytes = 0;
      const chunks: Uint8Array[] = [];
      while (true) {
        const part = await reader.read();
        if (part.done) break;
        bytes += part.value.byteLength;
        if (bytes > RESPONSE_BYTES) throw invalid();
        chunks.push(part.value);
      }
      return JSON.parse(
        new TextDecoder("utf-8", { fatal: true }).decode(Buffer.concat(chunks)),
      );
    })();
    try {
      return await Promise.race([operationPromise, deadline]);
    } finally {
      clearTimeout(timeout!);
      controller.abort();
      // Best-effort cleanup must not itself pin a control-plane call forever.
      if (reader) void reader.cancel().catch(() => {});
    }
  }
  async summary(runId: string): Promise<FinancialSummary | null> {
    try {
      return validateSummary(
        await this.#rpc("summary", { runId: identifier(runId) }),
      );
    } catch {
      return null;
    }
  }
  async authorize(
    request: Parameters<Economy["authorize"]>[0],
  ): Promise<Authorization> {
    try {
      identifier(request.runId);
      identifier(request.actionId);
      boundedString(request.tool, 200);
      const limit = validateLimit(request.limit);
      const response = object(
        await this.#rpc("authorize", {
          runId: request.runId,
          actionId: request.actionId,
          tool: request.tool,
          limit,
        }),
      );
      if (response.kind === "insufficient")
        return {
          kind: "insufficient",
          reason: "Available credit cannot fund this action",
        };
      if (response.kind !== "authorized")
        return {
          kind: "unavailable",
          reason: "Economy authorization is unavailable",
        };
      const reservation = validateReservation(response.reservation);
      if (
        reservation.runId !== request.runId ||
        reservation.actionId !== request.actionId ||
        !equalLimit(reservation.limit, limit)
      )
        throw invalid();
      return { kind: "authorized", reservation };
    } catch {
      return {
        kind: "unavailable",
        reason: "Economy authorization is unavailable",
      };
    }
  }
  async settle(
    reservation: Reservation,
    receipt: ToolReceipt,
  ): ReturnType<Economy["settle"]> {
    try {
      const checked = validateReservation(reservation);
      const accounting = accountingReceipt(receipt, checked);
      if (
        Buffer.byteLength(
          JSON.stringify({ reservation: checked, receipt: accounting }),
        ) > REQUEST_BYTES
      ) {
        delete accounting.externalRef;
        delete accounting.usage.nativeAmount;
        accounting.usage.evidenceRef = "omitted:accounting-metadata-budget";
        (accounting.result as Record<string, unknown>).usageMetadataOmitted =
          true;
      }
      const response = object(
        await this.#rpc("settle", {
          reservation: checked,
          receipt: accounting,
        }),
      );
      if (response.kind === "settled" && receipt.usage.provisional === false)
        return { kind: "settled" };
    } catch {
      /* Retain the durable action and cap for reconciliation. */
    }
    return {
      kind: "unavailable",
      reason: "Economy settlement requires reconciliation",
    };
  }
  async confirmNotPerformed(
    reservation: Reservation,
    evidenceRef: string,
  ): Promise<{ kind: "renewed" } | { kind: "unavailable"; reason: string }> {
    try {
      const response = object(
        await this.#rpc("not-performed", {
          reservation: validateReservation(reservation),
          evidenceRef: boundedString(evidenceRef, 2048),
        }),
      );
      if (response.kind === "renewed") return { kind: "renewed" };
    } catch {
      /* No proof of a renewed permit: keep the action unresolved. */
    }
    return {
      kind: "unavailable",
      reason:
        "Economy authorization renewal requires definitive reconciliation",
    };
  }
  async seed(
    runId: string,
    mode: "sandbox" | "live",
  ): Promise<FinancialSummary | null> {
    return validateSummary(
      await this.#rpc("seed", { runId: identifier(runId), mode }, true),
    );
  }
}
