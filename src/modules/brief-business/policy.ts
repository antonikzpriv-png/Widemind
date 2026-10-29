import { money } from "../../contracts/index.ts";
import { integer, record, text } from "./services.ts";

export interface BusinessPolicy {
  goals: string[];
  capabilities: string[];
  allowOutbound: boolean;
  prices: Record<string, string>;
  goalWeights: Record<string, number>;
  experimentBudgetMicros: string;
  maxJobMicros: string;
  turnaroundSeconds: number;
  quoteValidSeconds: number;
  maxOpenOrders: number;
  priorityWeight: number;
  marginWeight: number;
}
export const defaultPolicy: BusinessPolicy = {
  goals: ["Deliver useful work with a positive measured contribution"],
  capabilities: ["research"],
  allowOutbound: false,
  prices: { "research-brief": "2000000" },
  goalWeights: { "research-brief": 1 },
  experimentBudgetMicros: "1000000",
  maxJobMicros: "250000",
  turnaroundSeconds: 86400,
  quoteValidSeconds: 172800,
  maxOpenOrders: 10,
  priorityWeight: 10,
  marginWeight: 1,
};
export function validatePolicy(value: unknown): BusinessPolicy {
  const p = record(value);
  const known = new Set(Object.keys(defaultPolicy));
  if (Object.keys(p).some((key) => !known.has(key)))
    throw new Error("Unknown policy field");
  if (
    !Array.isArray(p.goals) ||
    !p.goals.length ||
    p.goals.length > 20 ||
    !Array.isArray(p.capabilities) ||
    p.capabilities.length > 100 ||
    typeof p.allowOutbound !== "boolean"
  )
    throw new Error("Invalid goals or capability policy");
  const prices = Object.fromEntries(
    Object.entries(record(p.prices)).map(([key, value]) => [
      text(key, 100),
      money(value as string).micros,
    ]),
  );
  const goalWeights = Object.fromEntries(
    Object.entries(record(p.goalWeights)).map(([key, value]) => [
      text(key, 100),
      integer(value, 0, 1000),
    ]),
  );
  return {
    goals: p.goals.map((item) => text(item, 500)),
    capabilities: p.capabilities.map((item) => text(item, 100)),
    allowOutbound: p.allowOutbound,
    prices,
    goalWeights,
    experimentBudgetMicros: money(p.experimentBudgetMicros as string).micros,
    maxJobMicros: money(p.maxJobMicros as string).micros,
    turnaroundSeconds: integer(p.turnaroundSeconds, 1, 31_536_000),
    quoteValidSeconds: integer(p.quoteValidSeconds, 1, 31_536_000),
    maxOpenOrders: integer(p.maxOpenOrders, 1, 10000),
    priorityWeight: integer(p.priorityWeight, 0, 1000),
    marginWeight: integer(p.marginWeight, 0, 1000),
  };
}
