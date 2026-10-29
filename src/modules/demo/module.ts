import { money } from "../../contracts/index.ts";
import type { ModuleDefinition } from "../registry.ts";
/** Local executable demonstration, never a simulated customer or funded run. */
export const definition: ModuleDefinition = {
  id: "demo",
  contractVersion: 1,
  create: () => ({
    economy: {
      health: () => ({ available: true }),
      summary: async () => null,
      authorize: async ({ runId, actionId, limit }) =>
        limit.maximum.micros !== "0"
          ? {
              kind: "unavailable",
              reason: "Demo cannot authorize paid operations",
            }
          : {
              kind: "authorized",
              reservation: {
                runId,
                actionId,
                limit,
                reservationId: `demo:${actionId}`,
                authorization: "demo-zero-cost-only",
              },
            },
      settle: async (_reservation, receipt) =>
        receipt.usage.actualCharge.micros === "0"
          ? { kind: "settled" }
          : {
              kind: "unavailable",
              reason: "Demo cannot settle monetary charges",
            },
    },
    business: {
      health: () => ({ available: true }),
      selectWork: async (snapshot) =>
        snapshot.actions.length
          ? {
              kind: "wait",
              acknowledgeEventIds: snapshot.events.map(
                (event) => event.eventId,
              ),
              reason:
                "Local demonstration finished; no automatic paid planning",
            }
          : {
              kind: "work",
              acknowledgeEventIds: snapshot.events.map(
                (event) => event.eventId,
              ),
              candidate: {
                workKey: "local-foundation-check-v1",
                tool: "demo.write-note",
                input: {
                  text: "WideMind worker produced this local note independently of the website.",
                },
                limit: {
                  maximum: money("0"),
                  maxDurationMs: 1000,
                  maxOutputBytes: 4096,
                },
              },
            },
    },
    tools: {
      health: () => ({ available: true }),
      capabilities: () => [
        { id: "demo.write-note", availability: { available: true } },
      ],
      execute: async (action, reservation, signal) => {
        if (
          signal.aborted ||
          action.tool !== "demo.write-note" ||
          reservation.actionId !== action.actionId ||
          reservation.authorization !== "demo-zero-cost-only" ||
          reservation.limit.maximum.micros !== "0"
        )
          return {
            kind: "unknown",
            reason: "Local tool authorization or cancellation failed",
          };
        return {
          kind: "receipt",
          receipt: {
            version: 1,
            actionId: action.actionId,
            status: "succeeded",
            result: {
              artifact: {
                type: "text/plain",
                text: "WideMind worker produced this local note independently of the website.",
              },
            },
            usage: {
              actualCharge: money("0"),
              provider: "local-demo",
              accountRef: "none",
              externalEventId: action.actionId,
              billableFailure: false,
              provisional: false,
              evidenceRef: `local:${action.actionId}`,
            },
          },
        };
      },
      // This tool has no external effect; its deterministic local result may be recomputed.
      reconcile: async (action) => ({
        kind: "not_performed",
        evidenceRef: `local-pure:${action.actionId}`,
      }),
    },
  }),
};
