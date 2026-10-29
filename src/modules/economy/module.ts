import type { Json, ModuleRoute } from "../../contracts/index.ts";
import type { ModuleDefinition } from "../registry.ts";
import {
  createEconomyAdapter,
  createEconomyClient,
  EconomyApiError,
  exactAmount,
  identifier,
} from "./client.ts";
function object(value: Json): Record<string, Json> {
  if (!value || typeof value !== "object" || Array.isArray(value))
    throw new Error("Expected an object");
  return value;
}
export const definition: ModuleDefinition = {
  id: "economy",
  contractVersion: 1,
  create: ({ db, config }) => {
    if (config.profile === "demo") return {};
    const client = createEconomyClient();
    const route = (
      name: string,
      operation: (body: Record<string, Json>) => Promise<unknown>,
      method: "GET" | "POST" = "POST",
    ): ModuleRoute => ({
      path: `/api/modules/economy/${name}`,
      method,
      access: "operator",
      handle: async (request) => {
        if (!client)
          return {
            status: 503,
            body: {
              available: false,
              reason:
                "Economy service URL and role credentials are not configured",
            },
          };
        try {
          return {
            status: 200,
            body: (await operation(
              object(method === "GET" ? request.query : request.body),
            )) as Json,
          };
        } catch (error) {
          return {
            status: error instanceof EconomyApiError ? error.status : 400,
            body: {
              error:
                error instanceof EconomyApiError
                  ? error.code
                  : "invalid_economy_request",
            },
          };
        }
      },
    });
    const routes: ModuleRoute[] = [
      route(
        "status",
        async (body) => {
          const runId = identifier(body.runId);
          return {
            available: true,
            balance: await client!.request(
              "operator",
              `/v1/runs/${runId}/runtime-summary`,
            ),
            reservations: await client!.request(
              "operator",
              `/v1/runs/${runId}/reservations`,
            ),
            payments: await client!.request(
              "operator",
              `/v1/runs/${runId}/payments`,
            ),
            integrity: await client!.request(
              "operator",
              `/v1/runs/${runId}/integrity`,
            ),
          };
        },
        "GET",
      ),
      route("open", async (body) => {
        const runId = identifier(body.runId);
        if (!db.prepare("SELECT 1 FROM runtime_runs WHERE id=?").get(runId))
          throw new Error("Unknown runtime run");
        const mode =
          body.mode === "sandbox" || body.mode === "test"
            ? "test"
            : body.mode === "live"
              ? "live"
              : null;
        if (!mode || body.confirmOpeningCredit !== true)
          throw new Error("Explicit five-dollar opening confirmation required");
        if (
          mode === "live" &&
          (process.env.WIDEMIND_ENABLE_LIVE_EFFECTS !== "true" ||
            typeof body.fundingReference !== "string" ||
            !body.fundingReference.trim())
        )
          throw new Error(
            "Live funding attestation and explicit live configuration required",
          );
        return client!.request("operator", "/v1/runs", {
          run_id: runId,
          mode,
          ...(mode === "live"
            ? { funding_reference: body.fundingReference }
            : {}),
        });
      }),
      route("funding", async (body) =>
        client!.request(
          "operator",
          `/v1/runs/${identifier(body.runId)}/funding`,
          {
            amount_micro_usd: exactAmount(body.amountMicros),
            category:
              body.category === "owner_funding"
                ? "owner_funding"
                : body.category === "test_funding"
                  ? "test_funding"
                  : null,
            reference: identifier(body.reference),
            idempotency_key: identifier(body.idempotencyKey),
          },
        ),
      ),
      route("checkout", async (body) =>
        client!.request(
          "operator",
          `/v1/runs/${identifier(body.runId)}/checkout`,
          {
            request_id: identifier(body.requestId),
            job_id: identifier(body.jobId),
            amount_micro_usd: exactAmount(body.amountMicros),
            description:
              typeof body.description === "string"
                ? body.description
                : "WideMind order",
          },
        ),
      ),
      route("reconcile", async (body) =>
        client!.request(
          "operator",
          `/v1/payments/${identifier(body.paymentId)}/reconcile`,
          {},
          "POST",
        ),
      ),
      route("release-hold", async (body) =>
        client!.request(
          "operator",
          `/v1/payments/${identifier(body.paymentId)}/release-hold`,
          { idempotency_key: identifier(body.idempotencyKey) },
        ),
      ),
      route("provider-verify", async () =>
        client!.request("operator", "/v1/provider/verify", {}, "POST"),
      ),
      route("refund", async (body) =>
        client!.request(
          "operator",
          `/v1/payments/${identifier(body.paymentId)}/refund`,
          {
            request_id: identifier(body.requestId),
            amount_micro_usd: exactAmount(body.amountMicros),
          },
        ),
      ),
      route("capture", async (body) =>
        client!.request(
          "operator",
          `/v1/payments/${identifier(body.paymentId)}/capture`,
          { idempotency_key: identifier(body.idempotencyKey) },
        ),
      ),
      {
        path: "/api/modules/economy/webhook",
        method: "POST",
        access: "public",
        handle: async (request) =>
          !client
            ? { status: 503, body: { error: "economy_unavailable" } }
            : client.raw(
                process.env.WIDEMIND_PAYMENT_PROVIDER === "stripe"
                  ? "/v1/webhooks/stripe"
                  : "/v1/webhooks/paypal",
                request.rawBody ?? new Uint8Array(),
                request.headers,
              ),
      },
    ];
    return {
      ...(client ? { economy: createEconomyAdapter(client) } : {}),
      routes,
    };
  },
};
