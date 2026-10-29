/** Private commerce control client; the Python economy is the only money authority. */
import type {
  Json,
  Reconciliation,
  Reservation,
  ToolAction,
  ToolReceipt,
  Tools,
} from "../../contracts/index.ts";
import type { ModuleContext } from "../registry.ts";
import { isDeepStrictEqual } from "node:util";
import { bridgeSettings, type BridgeSettings } from "../economy/client.ts";
import { verifiedCommerceDelivery } from "./evidence.ts";

export type CommerceMode = "sandbox" | "live";
export interface CommerceSettings extends BridgeSettings {
  mode: CommerceMode;
}
export interface CommerceOrder {
  runId: string;
  jobId: string;
  mode: CommerceMode;
  currency: "USD";
  requestId: string;
  paymentId: string | null;
  checkoutUrl: string | null;
  priceMicros: string;
  paymentStatus:
    | "pending"
    | "settled"
    | "partially_refunded"
    | "refunded"
    | "disputed"
    | "failed";
  revision: string;
  receiptsMicros: string;
  refundsMicros: string;
  chargebacksMicros: string;
  chargebackReversalsMicros: string;
  processorFeesMicros: string;
  paymentAdjustmentsMicros: string;
  netReceiptsMicros: string;
  earnedRevenueMicros: string;
  heldMicros: string;
  pendingLiabilityMicros: string;
  releasedMicros: string;
  costsFinal: boolean;
  deliveryObligation: "unfulfilled" | "fulfilled";
  deliveryReference: string | null;
}
export type CommerceResult<T = CommerceOrder> =
  | { kind: "available"; order: T }
  | { kind: "unavailable"; reason: string };
export interface CommerceIdentity {
  runId: string;
  jobId: string;
}
export interface CommerceCheckout extends CommerceIdentity {
  requestId: string;
  amountMicros: string;
  currency: "USD";
  description: string;
}
export interface CommerceFulfillment extends CommerceIdentity {
  deliveryReference: string;
  idempotencyKey: string;
}
export interface BusinessCommerce {
  createPaymentLink(request: CommerceCheckout): Promise<CommerceResult>;
  order(request: CommerceIdentity): Promise<CommerceResult>;
  recordFulfillment(request: CommerceFulfillment): Promise<CommerceResult>;
  releaseHold(request: CommerceFulfillment): Promise<CommerceResult>;
}

const unavailable = (): CommerceResult => ({
  kind: "unavailable",
  reason:
    "Protected commerce authority unavailable; preserve the original intent",
});
function invalid(): never {
  throw new Error("Invalid commerce boundary");
}
function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) invalid();
  return value as Record<string, unknown>;
}
function id(value: unknown): string {
  if (
    typeof value !== "string" ||
    !/^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$/.test(value)
  )
    invalid();
  return value;
}
function line(value: unknown, max: number): string {
  if (
    typeof value !== "string" ||
    !value.trim() ||
    value.length > max ||
    /[\x00-\x1f\x7f]/.test(value)
  )
    invalid();
  return value;
}
function micros(value: unknown, signed = false): string {
  if (
    typeof value !== "string" ||
    value.length > 129 ||
    value.replace("-", "").length > 128 ||
    !(signed ? /^(0|-?[1-9][0-9]*)$/ : /^(0|[1-9][0-9]*)$/).test(value)
  )
    invalid();
  return value;
}
function price(value: unknown): string {
  const result = micros(value),
    amount = BigInt(result);
  if (amount < 500000n || amount > 999999990000n || amount % 10000n) invalid();
  return result;
}
function identity(value: CommerceIdentity): CommerceIdentity {
  return { runId: id(value.runId), jobId: id(value.jobId) };
}
function nullableId(value: unknown): string | null {
  return value === null ? null : id(value);
}
function checkoutUrl(value: unknown): string | null {
  if (value === null) return null;
  const text = line(value, 2048),
    url = new URL(text);
  if (
    url.protocol !== "https:" ||
    !url.hostname ||
    url.username ||
    url.password
  )
    invalid();
  return text;
}
export function validateCommerceOrder(
  value: unknown,
  expected: CommerceIdentity,
  mode: CommerceMode,
): CommerceOrder {
  const row = object(value);
  if (
    row.runId !== expected.runId ||
    row.jobId !== expected.jobId ||
    row.mode !== mode ||
    row.currency !== "USD" ||
    ![
      "pending",
      "settled",
      "partially_refunded",
      "refunded",
      "disputed",
      "failed",
    ].includes(String(row.paymentStatus)) ||
    typeof row.costsFinal !== "boolean" ||
    !["unfulfilled", "fulfilled"].includes(String(row.deliveryObligation))
  )
    invalid();
  const order: CommerceOrder = {
    ...identity(expected),
    mode,
    currency: "USD",
    requestId: id(row.requestId),
    paymentId: nullableId(row.paymentId),
    checkoutUrl: checkoutUrl(row.checkoutUrl),
    priceMicros: price(row.priceMicros),
    paymentStatus: row.paymentStatus as CommerceOrder["paymentStatus"],
    revision: micros(row.revision),
    receiptsMicros: micros(row.receiptsMicros),
    refundsMicros: micros(row.refundsMicros),
    chargebacksMicros: micros(row.chargebacksMicros),
    chargebackReversalsMicros: micros(row.chargebackReversalsMicros, true),
    processorFeesMicros: micros(row.processorFeesMicros, true),
    paymentAdjustmentsMicros: micros(row.paymentAdjustmentsMicros, true),
    netReceiptsMicros: micros(row.netReceiptsMicros, true),
    earnedRevenueMicros: micros(row.earnedRevenueMicros),
    heldMicros: micros(row.heldMicros),
    pendingLiabilityMicros: micros(row.pendingLiabilityMicros),
    releasedMicros: micros(row.releasedMicros),
    costsFinal: row.costsFinal,
    deliveryObligation:
      row.deliveryObligation as CommerceOrder["deliveryObligation"],
    deliveryReference:
      row.deliveryReference === null ? null : line(row.deliveryReference, 2000),
  };
  const principal =
    BigInt(order.receiptsMicros) -
    BigInt(order.refundsMicros) -
    BigInt(order.chargebacksMicros) +
    BigInt(order.chargebackReversalsMicros);
  const earnable = principal - BigInt(order.pendingLiabilityMicros);
  if (
    BigInt(order.netReceiptsMicros) !==
      principal -
        BigInt(order.processorFeesMicros) +
        BigInt(order.paymentAdjustmentsMicros) ||
    BigInt(order.earnedRevenueMicros) > BigInt(order.priceMicros) ||
    BigInt(order.earnedRevenueMicros) > (earnable > 0n ? earnable : 0n) ||
    (order.deliveryObligation === "fulfilled") !==
      (order.deliveryReference !== null) ||
    ((order.deliveryObligation === "unfulfilled" ||
      order.paymentStatus === "disputed") &&
      order.earnedRevenueMicros !== "0")
  )
    invalid();
  return order;
}

export function commerceSettings(
  env: NodeJS.ProcessEnv,
): CommerceSettings | null {
  if (!env.WIDEMIND_COMMERCE_ACCOUNT && !env.WIDEMIND_COMMERCE_MODE)
    return null;
  const settings = bridgeSettings(env);
  if (
    !settings ||
    (env.WIDEMIND_COMMERCE_MODE !== "sandbox" &&
      env.WIDEMIND_COMMERCE_MODE !== "live")
  )
    throw new Error(
      "Commerce requires explicit protected economy configuration and sandbox/live mode",
    );
  id(env.WIDEMIND_COMMERCE_ACCOUNT);
  return { ...settings, mode: env.WIDEMIND_COMMERCE_MODE };
}

export class EconomyCommerce implements BusinessCommerce {
  readonly #settings: CommerceSettings;
  readonly #fetch: typeof fetch;
  constructor(settings: CommerceSettings, transport: typeof fetch = fetch) {
    const checked = bridgeSettings({
      WIDEMIND_ECONOMY_URL: settings.url,
      WIDEMIND_OPERATOR_TOKEN: settings.operatorToken,
      WIDEMIND_GATEWAY_TOKEN: settings.gatewayToken,
      WIDEMIND_ECONOMY_TIMEOUT_MS: String(settings.timeoutMs),
    });
    if (!checked || !["sandbox", "live"].includes(settings.mode)) invalid();
    this.#settings = { ...checked, mode: settings.mode };
    this.#fetch = transport;
  }
  async #rpc(
    path: string,
    body: unknown,
    operator: boolean,
    externalSignal?: AbortSignal,
  ): Promise<unknown> {
    const controller = new AbortController();
    const signal = externalSignal
      ? AbortSignal.any([controller.signal, externalSignal])
      : controller.signal;
    if (signal.aborted) invalid();
    let reader: ReadableStreamDefaultReader<Uint8Array> | undefined;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const deadline = new Promise<never>((_resolve, reject) => {
      timer = setTimeout(() => {
        controller.abort();
        reject(new Error("Commerce deadline exceeded"));
      }, this.#settings.timeoutMs);
    });
    let abortListener: (() => void) | undefined;
    const aborted = new Promise<never>((_resolve, reject) => {
      abortListener = () =>
        reject(
          new Error("Commerce request aborted; reconcile its original intent"),
        );
      signal.addEventListener("abort", abortListener, { once: true });
    });
    const request = (async () => {
      const serialized = JSON.stringify(body);
      if (Buffer.byteLength(serialized) > 16384) invalid();
      const response = await this.#fetch(new URL(path, this.#settings.url), {
        method: "POST",
        redirect: "error",
        signal,
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${operator ? this.#settings.operatorToken : this.#settings.gatewayToken}`,
        },
        body: serialized,
      });
      if (!response.ok || !response.body) invalid();
      const declared = response.headers.get("content-length");
      if (
        declared !== null &&
        (!/^[0-9]{1,10}$/.test(declared) || BigInt(declared) > 65536n)
      )
        invalid();
      reader = response.body.getReader();
      const chunks: Uint8Array[] = [];
      let length = 0;
      while (true) {
        const part = await reader.read();
        if (part.done) break;
        length += part.value.byteLength;
        if (length > 65536) invalid();
        chunks.push(part.value);
      }
      return JSON.parse(
        new TextDecoder("utf-8", { fatal: true }).decode(Buffer.concat(chunks)),
      ) as unknown;
    })();
    try {
      return await Promise.race([request, deadline, aborted]);
    } finally {
      clearTimeout(timer);
      if (abortListener) signal.removeEventListener("abort", abortListener);
      controller.abort();
      if (reader) void reader.cancel().catch(() => {});
    }
  }
  async #orderCall(
    operation: string,
    request: CommerceIdentity & Record<string, unknown>,
    operator: boolean,
    signal?: AbortSignal,
  ): Promise<CommerceResult> {
    try {
      const expected = identity(request);
      const result = await this.#rpc(
        `/v1/runtime/commerce/${operation}`,
        { ...request, ...expected, mode: this.#settings.mode },
        operator,
        signal,
      );
      return {
        kind: "available",
        order: validateCommerceOrder(result, expected, this.#settings.mode),
      };
    } catch {
      return unavailable();
    }
  }
  async createPaymentLink(
    request: CommerceCheckout,
    signal?: AbortSignal,
  ): Promise<CommerceResult> {
    try {
      if (request.currency !== "USD") invalid();
      const result = await this.#orderCall(
        "checkout",
        {
          ...identity(request),
          requestId: id(request.requestId),
          amountMicros: price(request.amountMicros),
          currency: "USD",
          description: line(request.description, 200),
        },
        true,
        signal,
      );
      if (
        result.kind === "available" &&
        (result.order.requestId !== request.requestId ||
          result.order.priceMicros !== request.amountMicros)
      )
        invalid();
      return result;
    } catch {
      return unavailable();
    }
  }
  async order(request: CommerceIdentity): Promise<CommerceResult> {
    try {
      return await this.#orderCall("order", { ...identity(request) }, false);
    } catch {
      return unavailable();
    }
  }
  async recordFulfillment(
    request: CommerceFulfillment,
    signal?: AbortSignal,
  ): Promise<CommerceResult> {
    try {
      const result = await this.#orderCall(
        "fulfillment",
        {
          ...identity(request),
          deliveryReference: line(request.deliveryReference, 2000),
          idempotencyKey: id(request.idempotencyKey),
        },
        true,
        signal,
      );
      if (
        result.kind === "available" &&
        (result.order.deliveryObligation !== "fulfilled" ||
          result.order.deliveryReference !== request.deliveryReference)
      )
        invalid();
      return result;
    } catch {
      return unavailable();
    }
  }
  async releaseHold(request: CommerceFulfillment): Promise<CommerceResult> {
    try {
      const result = await this.#orderCall(
        "release-hold",
        {
          ...identity(request),
          deliveryReference: line(request.deliveryReference, 2000),
          idempotencyKey: id(request.idempotencyKey),
        },
        true,
      );
      if (
        result.kind === "available" &&
        (result.order.deliveryObligation !== "fulfilled" ||
          result.order.deliveryReference !== request.deliveryReference)
      )
        invalid();
      return result;
    } catch {
      return unavailable();
    }
  }
  /** Trusted tool boundary only: this is the single dispatch gate for commerce. */
  async verify(
    action: ToolAction,
    reservation: Reservation,
    dispatch: boolean,
    signal?: AbortSignal,
  ): Promise<Record<string, unknown>> {
    return object(
      await this.#rpc(
        "/v1/runtime/verify",
        { action, reservation, dispatch },
        false,
        signal,
      ),
    );
  }
  /** Reconcile a previously persisted intent through the existing GET-only Stripe recovery. */
  async recoverCheckout(requestId: string): Promise<void> {
    await this.#rpc(
      `/v1/checkouts/${encodeURIComponent(id(requestId))}/recover`,
      {},
      true,
    );
  }
}

export function createCommerceTools(
  context: ModuleContext,
  env: NodeJS.ProcessEnv = context.env ?? process.env,
): Tools | null {
  const settings = commerceSettings(env);
  if (!settings) return null;
  const account = id(env.WIDEMIND_COMMERCE_ACCOUNT),
    client = new EconomyCommerce(settings);
  function parameters(action: ToolAction, observational = false) {
    const serialized = JSON.stringify(action);
    if (Buffer.byteLength(serialized) > 16384) invalid();
    const saved = context.db
      .prepare(
        "SELECT action_json FROM runtime_actions WHERE id=? AND run_id=?",
      )
      .get(action.actionId, action.runId);
    if (
      !saved ||
      typeof saved.action_json !== "string" ||
      !isDeepStrictEqual(JSON.parse(saved.action_json), JSON.parse(serialized))
    )
      invalid();
    if (
      !action.jobId ||
      !["commerce.checkout", "commerce.fulfill"].includes(action.tool) ||
      action.limit.maximum.currency !== "USD" ||
      action.limit.maximum.micros !== "0" ||
      action.limit.maxOutputBytes < 8192
    )
      invalid();
    const input = object(action.input),
      common = { runId: id(action.runId), jobId: id(action.jobId) };
    if (action.tool === "commerce.checkout") {
      if (
        Object.keys(input).some(
          (key) => !["amountMicros", "currency", "description"].includes(key),
        ) ||
        input.currency !== "USD"
      )
        invalid();
      const order = context.db
        .prepare(
          "SELECT price_micros,status FROM business_orders WHERE id=? AND run_id=?",
        )
        .get(common.jobId, common.runId);
      if (
        !order ||
        order.price_micros !== input.amountMicros ||
        (!observational && order.status !== "accepted")
      )
        invalid();
      return {
        common,
        checkout: {
          ...common,
          requestId: id(action.actionId),
          amountMicros: price(input.amountMicros),
          currency: "USD" as const,
          description: line(input.description, 200),
        },
      };
    }
    if (Object.keys(input).some((key) => key !== "deliveryReference"))
      invalid();
    const deliveryReference = line(input.deliveryReference, 2000);
    if (
      !verifiedCommerceDelivery(
        context.db,
        common.runId,
        common.jobId,
        deliveryReference,
      )
    )
      invalid();
    return {
      common,
      fulfillment: {
        ...common,
        deliveryReference,
        idempotencyKey: id(action.actionId),
      },
    };
  }
  async function verified(
    action: ToolAction,
    reservation: Reservation,
    dispatch: boolean,
    signal?: AbortSignal,
  ) {
    const result = await client.verify(action, reservation, dispatch, signal),
      profile = object(result.profile);
    if (
      result.kind !== "verified" ||
      profile.provider !== "commerce" ||
      profile.account !== account ||
      profile.mode !== settings!.mode
    )
      invalid();
  }
  function receipt(action: ToolAction, order: CommerceOrder): ToolReceipt {
    return {
      version: 1,
      actionId: action.actionId,
      status: "succeeded",
      result: order as unknown as Json,
      usage: {
        actualCharge: { currency: "USD", micros: "0" },
        provider: "commerce",
        accountRef: account,
        externalEventId: action.actionId + ":commerce-final",
        billableFailure: false,
        provisional: false,
        evidenceRef: "protected-commerce:" + action.actionId,
      },
    };
  }
  return {
    health: () => ({ available: true }),
    capabilities: () =>
      ["commerce.checkout", "commerce.fulfill"].map((tool) => ({
        id: tool,
        availability: { available: true },
      })),
    async execute(action, reservation, signal) {
      try {
        const input = parameters(action);
        if (signal.aborted) invalid();
        const boundedSignal = AbortSignal.any([
          signal,
          AbortSignal.timeout(action.limit.maxDurationMs),
        ]);
        await verified(action, reservation, true, boundedSignal);
        const result = input.checkout
          ? await client.createPaymentLink(input.checkout, boundedSignal)
          : await client.recordFulfillment(input.fulfillment!, boundedSignal);
        if (result.kind !== "available")
          return { kind: "unknown", reason: result.reason };
        return { kind: "receipt", receipt: receipt(action, result.order) };
      } catch {
        return {
          kind: "unknown",
          reason:
            "Commerce authorization/evidence/outcome requires reconciliation",
        };
      }
    },
    async reconcile(action, reservation): Promise<Reconciliation> {
      try {
        const input = parameters(action, true);
        await verified(action, reservation, false);
        let result = await client.order(input.common);
        if (result.kind !== "available")
          return {
            kind: "unknown",
            reason: "No durable commerce outcome is currently verified",
          };
        if (input.checkout) {
          if (
            result.order.requestId !== action.actionId ||
            result.order.priceMicros !== input.checkout.amountMicros
          )
            invalid();
          if (result.order.paymentId === null) {
            await client.recoverCheckout(action.actionId);
            result = await client.order(input.common);
          }
          if (result.kind !== "available" || result.order.paymentId === null)
            return {
              kind: "unknown",
              reason:
                "Checkout outcome remains unresolved; no new session was created",
            };
        } else if (
          result.order.deliveryReference !==
            input.fulfillment!.deliveryReference ||
          result.order.deliveryObligation !== "fulfilled"
        ) {
          return {
            kind: "unknown",
            reason: "Delivery commitment is not yet durably recognized",
          };
        }
        return { kind: "receipt", receipt: receipt(action, result.order) };
      } catch {
        return {
          kind: "unknown",
          reason: "Commerce observational reconciliation unavailable",
        };
      }
    },
  };
}
